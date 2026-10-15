"""Export original THAW PC model streams and textures without changing the game.

Format references: slfx77/neversoft-multitool Core/Formats/Mesh/XbxScene/
ThawSceneFile.cs, ThawSceneMeshSupport.cs, XbxSkinVertexCodec.cs; Texture/
XbxScene/ThawTexFile.cs, themselves referencing NxTools fmt_thawscene_import.py.
Skin weights are verified against reTHAWed.exe 0x533000, not the upstream codec.
Raw JSON preserves source coordinates, packed weights and all material passes.
OBJ is an inspection copy of source geometry, not a rigged interchange format.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import zlib

from assets_export import region, require


def unpack(data, offset, fmt):
    return struct.unpack('<' + fmt, region(data, offset, struct.calcsize('<' + fmt), fmt))


def finite(values):
    require(all(math.isfinite(v) for v in values), 'Non-finite model data')
    return list(values)


def normal(packed):
    values = []
    for shift, bits in ((0, 11), (11, 11), (22, 10)):
        value = (packed >> shift) & ((1 << bits) - 1)
        if value & (1 << (bits - 1)):
            value -= 1 << bits
        values.append(value / ((1 << (bits - 1)) - 1))
    length = math.sqrt(sum(x*x for x in values))
    return [x / length for x in values] if length else values


def skin_weights(packed):
    """Native signed 11:11:10 conversion, with at most three influences.

    reTHAWed 0x5330FE..0x533181 uses float32 reciprocals at 0x6E284C/48.
    There is no inferred fourth weight and no normalization of their sum.
    """
    require(isinstance(packed, int) and 0 <= packed <= 0xFFFFFFFF, 'Invalid packed skin weights')
    f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
    weights = []
    for shift, bits in ((0, 11), (11, 11), (22, 10)):
        value = (packed >> shift) & ((1 << bits) - 1)
        if value & (1 << (bits - 1)):
            value -= 1 << bits
        weights.append(f32(value * f32(1 / ((1 << (bits - 1)) - 1))))
    require(all(math.isfinite(w) and 0 <= w <= 1 for w in weights) and sum(weights) > 0,
            'Invalid native skin weights')
    return weights + [0.0]


def strip_triangles(raw, count, billboard=False):
    result, strip = [], []
    for index in list(raw) + [0x7FFF]:
        if index == 0x7FFF:
            for i in range(2, len(strip)):
                triangle = [strip[i-2], strip[i-1], strip[i]]
                if i & 1:
                    triangle[1], triangle[2] = triangle[2], triangle[1]
                if len(set(triangle)) == 3:
                    result.append(triangle[::-1] if billboard else triangle)
            strip = []
        else:
            require(index < count, 'Vertex index out of bounds')
            strip.append(index)
    return result


def parse_model(data):
    require(len(data) >= 48 and data[32:34] == b'\x02\x10', 'Expected standalone THAW PC scene v2')
    material_count, material_size = unpack(data, 34, 'HI')
    require(0 < material_count <= 4096, 'Invalid material count')
    materials = []
    for index in range(material_count):
        at = 48 + 288 * index
        region(data, at, 288, 'material')
        checksum, name, passes = unpack(data, at, 'III')
        require(passes <= 4, 'Unknown material pass count')
        material = {'checksum': checksum, 'name_checksum': name,
                    'double_sided': bool(data[at+13]), 'alpha_cutoff': data[at+16],
                    'use_alpha_cutoff': bool(unpack(data, at+18, 'H')[0]),
                    'draw_order': finite(unpack(data, at+44, 'f'))[0], 'passes': []}
        for p in range(passes):
            blend, blend_extra = unpack(data, at+144+4*p, 'Hh')
            material['passes'].append({'flags': unpack(data, at+48+4*p, 'I')[0],
                'texture_checksum': unpack(data, at+64+4*p, 'I')[0],
                'color': finite(unpack(data, at+80+16*p, '4f')),
                'blend_mode': blend, 'blend_extra': blend_extra,
                'addressing': unpack(data, at+160+4*p, 'I')[0]})
        materials.append(material)
    sentinel = 32 + material_size
    magic, padding = unpack(data, sentinel, 'II')
    require(magic == 0xBABEFACE and padding <= 0x100000, 'Missing scene sentinel')
    scene = sentinel + 8 + padding
    version, size = unpack(data, scene, 'HH')
    require(0 < size < 4096, 'Invalid scene header size')
    link_count = unpack(data, scene+84, 'I')[0]
    links_at = scene + unpack(data, scene+120, 'I')[0]
    sector_count, sectors_rel, geoms_rel, _, _, meshes_rel = unpack(data, scene+128, '6I')
    require(sector_count <= 10000 and link_count <= 10000, 'Invalid scene counts')
    meshes, mesh_index = [], 0
    material_by_id = {m['checksum']: m for m in materials}
    for sector in range(sector_count):
        sec_at, geom_at = scene+sectors_rel+48*sector, scene+geoms_rel+104*sector
        _, checksum, flags = unpack(data, sec_at, 'III')
        mesh_count = unpack(data, geom_at+68, 'I')[0]
        require(mesh_count <= 10000, 'Invalid mesh count')
        require(not flags & 0x00800000, 'Billboard geometry is outside this exporter')
        for _ in range(mesh_count):
            at = scene + meshes_rel + 224 * mesh_index
            region(data, at, 224, 'mesh header')
            mesh_index += 1
            material_id = unpack(data, at+20, 'I')[0]
            require(material_id in material_by_id, 'Unresolved material')
            material = material_by_id[material_id]
            stride, count = data[at+24], unpack(data, at+38, 'H')[0]
            face_count = unpack(data, at+40, 'I')[0]
            face_at = scene + unpack(data, at+92, 'I')[0]
            vertex_at = scene + unpack(data, at+124, 'I')[0]
            skinned, has_normals, colors = bool(flags & 16), bool(flags & 4), bool(flags & 2)
            base_stride = 12 + (12 if skinned else 0) + ((4 if skinned else 12) if has_normals else 0) + (4 if colors else 0)
            require(stride >= base_stride, 'Vertex stride too small')
            require((stride-base_stride) % 8 == 0, 'Unknown vertex tail')
            uv_count = (stride-base_stride) // 8
            require(uv_count <= 4, 'Unknown UV stream count')
            region(data, vertex_at, count*stride, 'vertex stream')
            mesh = {'sector_checksum': checksum, 'sector_flags': flags,
                    'mesh_flags': unpack(data, at, 'I')[0], 'material_checksum': material_id,
                    'vertex_stride': stride, 'positions': [], 'normals': [], 'uv_sets': [[] for _ in range(uv_count)],
                    'colors_bgra': [], 'bone_indices': [], 'bone_weights': [], 'packed_weights': []}
            for v in range(count):
                cursor = vertex_at + v * stride
                mesh['positions'].append(finite(unpack(data, cursor, '3f')))
                cursor += 12
                if skinned:
                    packed = unpack(data, cursor, 'I')[0]
                    bones = unpack(data, cursor+4, '4H')
                    require(all(b % 3 == 0 and b // 3 < 256 for b in bones), 'Invalid skin bone indices')
                    mesh['bone_indices'].append([b//3 for b in bones])
                    mesh['bone_weights'].append(skin_weights(packed))
                    mesh['packed_weights'].append(packed)
                    cursor += 12
                if has_normals:
                    mesh['normals'].append(normal(unpack(data, cursor, 'I')[0]) if skinned else finite(unpack(data, cursor, '3f')))
                    cursor += 4 if skinned else 12
                if colors:
                    mesh['colors_bgra'].append(list(region(data, cursor, 4, 'vertex color')))
                    cursor += 4
                for uv in mesh['uv_sets']:
                    uv.append(finite(unpack(data, cursor, '2f')))
                    cursor += 8
            raw_indices = unpack(data, face_at+4, str(face_count)+'H')
            mesh['indices'] = strip_triangles(raw_indices, count)
            meshes.append(mesh)
    links = []
    for index in range(link_count):
        at = links_at + index*80
        checksum, parent, _, slot, _ = unpack(data, at, 'IIHHI')
        links.append({'sector_checksum': checksum, 'parent_checksum': parent, 'index': slot,
                      'transform_row_major': finite(unpack(data, at+16, '16f'))})
    require(meshes and all(m['positions'] for m in meshes), 'Empty original mesh')
    return {'format': 'thaw-model-v1', 'coordinates': 'original THAW model space; row-vector matrices; raw UVs',
            'materials': materials, 'meshes': meshes, 'links': links,
            'validation': {'indices_checked': True, 'finite_vertices': True, 'source_skin_indices_divisor': 3,
                           'skin_weight_encoding': 'native signed 11:11:10 /1023,/1023,/511; three weights; fourth zero'}}


def dxt_rgba(data, width, height, compression):
    step = 8 if compression == 1 else 16
    require(len(data) == ((width+3)//4)*((height+3)//4)*step, 'DXT block length mismatch')
    out = bytearray(width*height*4)
    at = 0
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            alpha = [255]*16
            if compression == 5:
                a0, a1 = data[at], data[at+1]
                table = [a0, a1]
                table += [((7-i)*a0+i*a1)//7 for i in range(1, 7)] if a0 > a1 else [((5-i)*a0+i*a1)//5 for i in range(1, 5)]+[0, 255]
                bits = int.from_bytes(data[at+2:at+8], 'little')
                alpha = [table[(bits >> (3*i))&7] for i in range(16)]
                at += 8
            c0, c1, bits = unpack(data, at, 'HHI')
            def rgb565(c):
                r,g,b = (c>>11)&31, (c>>5)&63, c&31
                return [(r<<3)|(r>>2), (g<<2)|(g>>4), (b<<3)|(b>>2)]
            a,b = rgb565(c0), rgb565(c1)
            palette = [a+[255], b+[255]]
            if c0 > c1 or compression == 5:
                palette += [[(2*x+y)//3 for x,y in zip(a,b)]+[255], [(x+2*y)//3 for x,y in zip(a,b)]+[255]]
            else:
                palette += [[(x+y)//2 for x,y in zip(a,b)]+[255], [0,0,0,0]]
            for i in range(16):
                x,y = bx+i%4, by+i//4
                if x < width and y < height:
                    color = palette[(bits>>(2*i))&3].copy()
                    color[3] = min(color[3], alpha[i])
                    out[4*(y*width+x):4*(y*width+x)+4] = bytes(color)
            at += 8
    return bytes(out)


def png_bytes(width, height, rgba):
    require(len(rgba) == width*height*4, 'RGBA size mismatch')
    def chunk(kind, payload):
        return struct.pack('>I', len(payload))+kind+payload+struct.pack('>I', zlib.crc32(kind+payload))
    # Keep source scanline order. JSON UVs are likewise untouched.
    scanlines = b''.join(b'\0'+rgba[y*width*4:(y+1)*width*4] for y in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>2I5B', width,height,8,6,0,0,0))+chunk(b'IDAT', zlib.compress(scanlines))+chunk(b'IEND', b'')


def parse_textures(data):
    magic, version, flags, count = unpack(data, 0, 'IBBH')
    require(magic == 0xABADD00D and count <= 4096, 'Not a THAW PC texture dictionary')
    at, textures = 8, []
    for _ in range(count):
        magic, unknown, checksum, w,h,rw,rh,mips,depth,raw_compression,palette_depth = unpack(data, at, 'III4H4B')
        require(magic == 0xABADD00D and 0 < w <= 4096 and 0 < h <= 4096 and 0 < mips <= 16, 'Invalid texture header')
        at += 24
        compression = {2:1,3:5}.get(raw_compression, raw_compression)
        require(compression in (0,1,5), 'Unsupported texture compression')
        palette = None
        if compression == 0 and palette_depth:
            entries = unpack(data, at, 'I')[0]; at += 4
            palette = bytes(region(data, at, entries*4, 'texture palette')); at += entries*4
        levels = []
        for mip in range(mips):
            if compression:
                size = unpack(data, at, 'I')[0]
            else:
                stride, lines = unpack(data, at, 'HH')
                size = stride*lines
            at += 4
            levels.append(bytes(region(data, at, size, 'texture mip'))); at += size
        payload = levels[0]
        if compression:
            rgba = dxt_rgba(payload,w,h,compression)
        elif palette is not None and depth in (4,8):
            require(len(payload) >= (w*h*depth+7)//8, 'Truncated palette indices')
            indices = [payload[i] if depth==8 else (payload[i//2] >> ((i%2)*4))&15 for i in range(w*h)]
            require(all(i*4+4 <= len(palette) for i in indices), 'Invalid palette index')
            rgba = b''.join(palette[i*4:i*4+4] for i in indices)
        elif depth == 32:
            require(len(payload) == w*h*4, 'Unexpected BGRA stride')
            rgba = bytes(c for i in range(0,len(payload),4) for c in (payload[i+2],payload[i+1],payload[i],payload[i+3]))
        else:
            raise ValueError('Unsupported uncompressed texture')
        textures.append(({'checksum':checksum,'width':w,'height':h,'mip_count':mips,
                          'compression':compression,'source_mip_sha256':hashlib.sha256(payload).hexdigest(),
                          'filename':f'{checksum:08x}.png','scanlines':'original source order; use raw UVs'}, png_bytes(w,h,rgba)))
    require(at == len(data), 'Unexpected texture trailing data')
    return textures


def export(model_path, texture_path, output):
    require(not output.exists(), 'Output directory already exists')
    data, tex = model_path.read_bytes(), texture_path.read_bytes()
    result, textures = parse_model(data), parse_textures(tex)
    result['source'] = {'model':str(model_path.resolve()), 'model_sha256':hashlib.sha256(data).hexdigest(),
                        'textures':str(texture_path.resolve()), 'textures_sha256':hashlib.sha256(tex).hexdigest()}
    result['textures'] = [t[0] for t in textures]
    checksums = {t[0]['checksum'] for t in textures}
    result['unresolved_texture_checksums'] = sorted({p['texture_checksum'] for m in result['materials'] for p in m['passes'] if p['texture_checksum'] and p['texture_checksum'] not in checksums})
    output.mkdir(parents=True)
    for metadata, png in textures:
        (output/metadata['filename']).write_bytes(png)
    (output/'model.json').write_text(json.dumps(result,separators=(',',':'),allow_nan=False), encoding='utf-8')
    lines, mtl, vertex_offset = ['mtllib model.mtl'], [], 1
    for material in result['materials']:
        mtl += [f"newmtl m{material['checksum']:08x}", 'Kd 1 1 1']
        if material['passes']:
            texid = material['passes'][0]['texture_checksum']
            if texid in checksums:
                mtl += [f'map_Kd {texid:08x}.png']
    for number, mesh in enumerate(result['meshes']):
        lines += [f'o mesh_{number}', f"usemtl m{mesh['material_checksum']:08x}"]
        lines += ['v '+' '.join(map(str,p)) for p in mesh['positions']]
        uvs = mesh['uv_sets'][0] if mesh['uv_sets'] else [[0,0] for p in mesh['positions']]
        # OBJ convention bottom-left UV origin; PNG written in original row order.
        lines += [f'vt {u} {1-v}' for u,v in uvs]
        normals = mesh['normals'] or [[0,0,0] for p in mesh['positions']]
        lines += ['vn '+' '.join(map(str,n)) for n in normals]
        lines += ['f '+' '.join(f'{i+vertex_offset}/{i+vertex_offset}/{i+vertex_offset}' for i in t) for t in mesh['indices']]
        vertex_offset += len(mesh['positions'])
    (output/'model.obj').write_text('\n'.join(lines)+'\n', encoding='ascii')
    (output/'model.mtl').write_text('\n'.join(mtl)+'\n', encoding='ascii')
    return {'output':str(output.resolve()),'meshes':len(result['meshes']),
            'vertices':sum(len(m['positions']) for m in result['meshes']),
            'triangles':sum(len(m['indices']) for m in result['meshes']),
            'textures':len(textures),'weighted_vertices':sum(len(m['bone_indices']) for m in result['meshes']),
            'unresolved_texture_checksums':result['unresolved_texture_checksums']}


def self_test():
    assert strip_triangles([0,1,2,3,0x7FFF,4,5,6],7) == [[0,1,2],[1,3,2],[4,5,6]]
    assert strip_triangles([0,0,1,2],3) == [[0,2,1]]
    assert normal(1023) == [1,0,0]
    assert skin_weights(0x3FF) == [1,0,0,0]
    assert skin_weights(511 | (512 << 11)) == [0.4995112419128418, 0.5004887580871582, 0, 0]
    assert skin_weights(256 | (256 << 11) | (255 << 22)) == [0.2502443790435791, 0.2502443790435791, 0.4990215301513672, 0]
    for packed in (0, 0x400, 0x400 << 11, 0x200 << 22, float('nan'), -1, 1 << 32):
        try:
            skin_weights(packed)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid skin weights accepted')
    red = struct.pack('<HHI',0xF800,0,0)
    assert dxt_rgba(red,4,4,1) == bytes([255,0,0,255])*16
    transparent = struct.pack('<HHI',0,0xFFFF,0xFFFFFFFF)
    assert dxt_rgba(transparent,4,4,1) == bytes(64)
    half_alpha = bytes([128,0])+bytes(6)+red
    assert dxt_rgba(half_alpha,4,4,5) == bytes([255,0,0,128])*16
    for payload in (b'',bytes(64)):
        for parser in (parse_model,parse_textures):
            try:
                parser(payload)
            except ValueError:
                pass
            else:
                raise AssertionError('Malformed asset accepted')
    return {'native_skin_weight_regression':True, 'strip_winding_restart':True,'dxt1_color_transparency':True,
            'dxt5_alpha':True,'malformed_assets_rejected':True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model',type=Path)
    parser.add_argument('texture',type=Path)
    parser.add_argument('output',type=Path)
    args = parser.parse_args()
    print(json.dumps(export(args.model,args.texture,args.output),indent=2))


if __name__ == '__main__':
    main()
