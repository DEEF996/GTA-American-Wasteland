"""Publish completed original HUD quads into an existing GTA MOVE2 lease."""
import hashlib
import json
import math
import struct
from pathlib import Path

from hud_quads import Assets, require
from preview_gameplay_move import scene
from preview_pose import Publisher

HEADER = struct.Struct('<8s4I4Q4I2Q32s2fI')
QUAD = struct.Struct('<2I16f')
SIZE = HEADER.size + 1024 * QUAD.size
assert HEADER.size == 132 and QUAD.size == 72 and SIZE == 73860


def packet(state, identity, request, sequence, now_ms, source_tick, source_time_us, atlas_hash, quads):
    require(scene(state) == identity, 'HUD scene changed')
    require(all(type(v) is int for v in (request,sequence,now_ms,source_tick,source_time_us)) and
            0 < request < 2**64 and 0 < sequence < 0x3fffffff and 0 <= now_ms < 2**64 and
            0 <= source_tick < 2**64 and 0 <= source_time_us < 2**64 and
            (source_tick == 0) == (source_time_us == 0), 'Invalid HUD clock or lease')
    require(type(atlas_hash) is bytes and len(atlas_hash) == 32 and any(atlas_hash) and len(quads) <= 1024,
            'HUD atlas hash or quad count differs')
    result = bytearray(HEADER.pack(b'GTAHUD01',1,SIZE,state['pid'],state['epoch'],
        state['creation_100ns'],request,sequence,now_ms,state['ped_address'],state['area'],state['ped_area'],0,
        source_tick,source_time_us,atlas_hash,640.,448.,len(quads)))
    for row in quads:
        require(type(row['texture']) is int and 0 <= row['texture'] < 64 and
                type(row['color']) is int and 0 <= row['color'] <= 0xffffffff and
                len(row['vertices']) == 4 and all(len(v) == 4 for v in row['vertices']), 'Invalid HUD quad fields')
        # Check each authored vertex once; constructing another quad would
        # repeat the same bounds check four times and allocate unused geometry.
        for vertex in row['vertices']:
            require(all(math.isfinite(v) for v in vertex) and
                    abs(vertex[0]) <= 4096 and abs(vertex[1]) <= 4096 and
                    0 <= vertex[2] <= 1 and 0 <= vertex[3] <= 1,
                    'Original HUD quad exceeds bounds')
        result.extend(QUAD.pack(row['texture'],row['color'],*(v for vertex in row['vertices'] for v in vertex)))
    result.extend(bytes(SIZE-len(result)))
    return bytes(result)


class HudPublisher:
    def __init__(self, observer, manifest):
        self.manifest = Path(manifest).resolve(strict=True)
        metadata = json.loads(self.manifest.read_bytes())
        bundle = self.manifest.parent / metadata['bundle']
        require(bundle.resolve().parent == self.manifest.parent and bundle.name == 'hud-atlases.bin',
                'HUD atlas path leaves its private bundle')
        raw = bundle.read_bytes()
        require(16 <= len(raw) <= 32*1024*1024 and hashlib.sha256(raw).hexdigest() == metadata['bundle_sha256'] and
                raw[:8] == b'GTAHUDTX' and struct.unpack_from('<2I',raw,8) == (1,len(metadata['textures'])),
                'HUD atlas bytes or format differ')
        self.assets, self.hash = Assets(metadata), hashlib.sha256(raw).digest()
        offset = 16
        for texture in metadata['textures']:
            width,height,size = struct.unpack_from('<3I',raw,offset)
            offset += 12
            require((width,height,size) == (texture['width'],texture['height'],texture['width']*texture['height']*4)
                    and size <= len(raw)-offset and hashlib.sha256(raw[offset:offset+size]).hexdigest() == texture['rgba_sha256'],
                    'HUD atlas record differs from private metadata')
            offset += size
        require(offset == len(raw), 'HUD atlas contains trailing bytes')
        self.transport = Publisher(observer, transport='HUD_V1', packet_size=SIZE)
        self.sequence = 0

    def publish(self, state, identity, request, now_ms, source_tick, source_time_us, layout, native_read):
        """Call on the producer thread immediately after layout.update(), before another step."""
        quads = self.assets.convert(layout,native_read)
        self.sequence += 1
        self.transport.publish(packet(state,identity,request,self.sequence,now_ms,source_tick,
                                      source_time_us,self.hash,quads),self.sequence)
        return len(quads)

    def clear(self):
        self.transport.clear()

    def close(self):
        self.transport.close()
