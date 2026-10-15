"""Read-only retirement guard over original component-manager allocations."""
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
import struct

import probe_loader as host

METADATA_SHA = '06a212c60f9b84cfb9b1da40c564af5df0bd192fa8ce1c50de6fefd3a69bd506'


def live_cache_bytes(address, data, unused_slots):
    """Ignore dead storage in our readback only; never modify the native cache."""
    result = bytearray(data)
    for start, end in unused_slots:
        start, end = max(0, start-address), min(len(result), end-address)
        if start < end:
            result[start:end] = bytes(end-start)
    return bytes(result)


def reference_words(blocks, extents):
    ordered = sorted(extent for extent in extents if extent[0] < extent[1])
    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(end, merged[-1][1])
        else:
            merged.append((start, end))
    starts = [extent[0] for extent in merged]
    lower, upper = (merged[0][0], merged[-1][1]) if merged else (0, 0)
    result = []
    for name, data in blocks:
        for word_index, (value,) in enumerate(struct.iter_unpack('<I', data)):
            if value < lower or value >= upper:
                continue
            index = bisect_right(starts, value)-1
            if index >= 0 and value < merged[index][1]:
                result.append(dict(component=name, offset=word_index*4, pointer=value))
    return result


class SceneReferences:
    def __init__(self, read, word, arena, context):
        self.read, self.word, self.arena, self.context = read, word, arena, context
        data = Path(__file__).with_name('scene_component_sizes.json').read_bytes()
        host.require(hashlib.sha256(data).hexdigest() == METADATA_SHA, 'Scene component allocation witnesses changed')
        metadata = json.loads(data)
        host.require(metadata['stock_exe_sha256'] == host.FILES['THAW.exe'][1] and
                     len(metadata['components']) == 93, 'Scene component witness source differs')
        self.sizes = {}
        for key, size, address, length, digest in metadata['components']:
            host.require(key not in self.sizes and hashlib.sha256(read(address, length)).hexdigest() == digest,
                         'Original component allocation prefix changed')
            self.sizes[key] = size

    def block(self, address, size):
        host.require(address % 4 == 0 and self.arena[0] <= address <= self.arena[1]-size,
                     'Scene reference allocation leaves original arena')
        return self.read(address, size)

    def blocks(self):
        """Every live/deferred Composite, its registered components, and Nxcamera."""
        manager = self.word(0x6dd6b0)
        self.block(manager, 0x44)
        mask, unused = self.word(0x6dd424), []
        host.require(mask & ~255 == 0, 'Scene reference cache registry mask differs')
        for slot in range(8):
            if mask & (1 << slot):
                cache = self.word(0x6dd400+4*slot)
                raw = self.block(cache, 0xfcc)
                count, static, moving = struct.unpack_from('<3I', raw, 0xfc0)
                host.require(count <= 500 and static+moving == count, 'Scene reference cache live-entry counts differ')
                unused.append((cache+0x20+count*8, cache+0xfc0))
        blocks, owners, components, nx_cameras = [], set(), set(), set()
        for sentinel in (manager+4, manager+0x28):
            header = struct.unpack('<5I', self.block(sentinel, 20))
            host.require(header[1:3] == (0xffffffff, 0), 'Scene reference owner sentinel differs')
            pointer, previous, seen = header[3], sentinel, set()
            while pointer != sentinel:
                host.require(pointer not in seen and len(seen) < 4096, 'Scene reference owner list cycles or overflows')
                seen.add(pointer)
                node = struct.unpack('<5I', self.block(pointer, 20))
                owner = node[1]
                host.require(node[4] == previous and owner not in owners, 'Scene reference owner backlinks/identity differ')
                owners.add(owner)
                raw = self.block(owner, 0x35c if owner == self.context['actor'] else 0x124)
                owner_words = struct.unpack('<'+str(len(raw)//4)+'I', raw)
                host.require(owner_words[3] == manager if sentinel == manager+4 else owner_words[3] == 0,
                             'Scene reference owner registration differs')
                label = 'actor' if owner == self.context['actor'] else 'camera_owner' if owner == self.context['camera'] else f'owner_{owner:08x}'
                blocks.append((label, raw))
                component, types = owner_words[0x11c//4], set()
                while component:
                    host.require(component not in components and len(types) < 93, 'Scene reference component chain cycles')
                    components.add(component)
                    row = struct.unpack('<6I', self.block(component, 24))
                    key = row[1]
                    host.require(row[3] == owner and key not in types and key in self.sizes,
                                 'Scene reference component owner/type lacks an original size witness')
                    types.add(key)
                    name = 'core' if key == 0x5bd63b29 else 'walk' if key == 0x726e85aa else f'{label}_component_{key:08x}'
                    contents = self.block(component, self.sizes[key])
                    blocks.append((name, live_cache_bytes(component, contents, unused)))
                    if key == 0xc4e311fa:
                        camera = struct.unpack_from('<I', contents, 24)[0]
                        host.require(camera not in nx_cameras, 'Scene reference Nxcamera is duplicated')
                        nx_cameras.add(camera)
                        camera_data = self.block(camera, 0xd8)
                        host.require(struct.unpack_from('<I', camera_data)[0] == 0x6933d0, 'Scene reference Nxcamera class differs')
                        blocks.append((f'nxcamera_{camera:08x}', camera_data))
                    component = row[4]
                previous, pointer = pointer, node[3]
            host.require(header[4] == previous, 'Scene reference owner list tail differs')
        host.require(self.context['actor'] in owners and self.context['camera'] in owners,
                     'Scene reference manager omitted original skater or camera')
        return blocks
