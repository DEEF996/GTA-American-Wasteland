"""Original HUD draw-object presentation, shared semantics with the WaW helper.

Port of tools/isolated_thaw/src/hud_presentation.rs's glyph/sprite conversion.
Consumes native final positions, transforms, color escapes and per-glyph effects;
does not calculate score, balance, countdowns, visibility or animation timing.
"""
import math
import struct


def require(ok, message):
    if not ok:
        raise ValueError(message)


def word(raw, at):
    return struct.unpack_from('<I', raw, at)[0]


def rotate(point, origin, angle):
    x, y = point[0] - origin[0], point[1] - origin[1]
    sin, cos = math.sin(angle), math.cos(angle)
    return [origin[0] + x * cos - y * sin, origin[1] + x * sin + y * cos]


def color_argb(rgba):
    return (rgba & 0xff00ff00) | ((rgba & 255) << 16) | ((rgba >> 16) & 255)


def code_index(byte):
    byte = bytes((byte,)).lower()[0]
    return byte - 48 if 48 <= byte <= 57 else byte - 97 + 10 if 97 <= byte <= 118 else 0


def quad(texture, rgba, points, uv):
    coordinates = ((uv[0], uv[1]), (uv[2], uv[1]), (uv[2], uv[3]), (uv[0], uv[3]))
    vertices = [[*point, *tex] for point, tex in zip(points, coordinates)]
    require(len(vertices) == 4 and all(all(math.isfinite(v) for v in row) and
            abs(row[0]) <= 4096 and abs(row[1]) <= 4096 and
            0 <= row[2] <= 1 and 0 <= row[3] <= 1 for row in vertices), 'Original HUD quad exceeds bounds')
    return dict(texture=texture, color=color_argb(rgba), vertices=vertices)


def text_quads(draw, text, effects, header, font):
    require(len(draw) == 0x4c and len(header) == 0x178 and len(text) <= 511 and
            (not effects or len(effects) == len(text) * 10), 'Original text field extents differ')
    x, y, sx, sy, ox, oy, angle = struct.unpack_from('<7f', draw, 0x28)
    priority, = struct.unpack_from('<f', draw, 0xc)
    require(all(math.isfinite(v) and abs(v) <= 1e6 for v in (x,y,sx,sy,ox,oy,angle,priority)),
            'Original text geometry is nonfinite or oversized')
    if not sx or not sy:
        return []
    tracking, space = struct.unpack_from('<2h', header, 0x134)
    cursor, color, index, glyph_number, result = x, word(draw, 0x44), 0, 0, []
    while index < len(text):
        special = None
        if text[index] == 92 and index + 1 < len(text):
            code = chr(text[index + 1]).lower()
            index += 2
            if code in ('c', 's'):
                require(index < len(text), 'Truncated original color/special glyph code')
                n = code_index(text[index])
                if code == 'c':
                    require(n <= 16, 'Original text palette index exceeds bounds')
                    index += 1
                    color = word(draw, 0x44) if n == 0 or draw[0x48] else word(header, 0x134 + n * 4)
                    if draw[0x49]:
                        color = (color & 0xffffff) | (word(draw, 0x44) & 0xff000000)
                    continue
                special = header[0x10c + n]
            elif code == '\\':
                index -= 1
            elif code == 'b':
                raise ValueError('Original button glyph requires its separately verified font binding')
            elif code in ('l', 'u'):
                index = min(len(text), index + (1 if code == 'l' else 2))
                continue
            else:
                continue
            if index >= len(text):
                break
        byte = text[index]
        index += 1
        if byte == 32 and special is None:
            cursor += (space + tracking) * sx
            glyph_number += 1
            continue
        glyph_index = special if special is not None else font['lookup'][byte]
        require(0 <= glyph_index < len(font['glyphs']), 'Original special glyph index exceeds font')
        glyph = font['glyphs'][glyph_index]
        top = y + (font['baseline'] - glyph['baseline']) * sy
        right, bottom, per_angle = cursor + glyph['width'] * sx, top + glyph['height'] * sy, 0
        if effects:
            require((glyph_number + 1) * 10 <= len(effects), 'Original glyph effect exceeds buffer')
            dx, dy, ex, ey, twist = struct.unpack_from('<5h', effects, glyph_number * 10)
            right = cursor + dx + glyph['width'] * sx * ex / 16384
            bottom = top + dy + glyph['height'] * sy * ey / 16384
            per_angle = twist / 16384
        points = [rotate(p, (x + ox, y + oy), angle) for p in
                  ((cursor, top), (right, top), (right, bottom), (cursor, bottom))]
        if per_angle:
            center = rotate(((cursor + right) * .5, (top + bottom) * .5), (x + ox, y + oy), angle)
            points = [rotate(p, center, per_angle) for p in points]
        result.append((priority, quad(font['texture_index'], color, points, glyph['uv'])))
        cursor = right + tracking * sx
        glyph_number += 1
    return result


def sprite_quads(draw, image, texture):
    require(len(draw) == 0x50 and len(image) == 16, 'Original sprite field extents differ')
    x, y = struct.unpack_from('<2f', draw, 0x20)
    width, height = struct.unpack_from('<2H', draw, 0x28)
    sx, sy, shx, shy, ax, ay, angle = struct.unpack_from('<7f', draw, 0x2c)
    priority, = struct.unpack_from('<f', draw, 0xc)
    require(all(math.isfinite(v) and abs(v) <= 1e6 for v in (x,y,sx,sy,shx,shy,ax,ay,angle,priority)),
            'Original sprite geometry is nonfinite or oversized')
    if not sx or not sy:
        return []
    left, top = -ax * abs(sx), -ay * abs(sy)
    right, bottom = left + width * abs(sx), top + height * abs(sy)
    points = []
    for px, py in ((left,top),(right,top),(right,bottom),(left,bottom)):
        px, py = rotate((px + py * shx, py + px * shy), (0,0), angle)
        points.append((px + x, py + y))
    used = struct.unpack_from('<2H', image, 12)
    uv = [0, 1, used[0] / texture['width'], 1 - used[1] / texture['height']]
    if sx < 0: uv[0], uv[2] = uv[2], uv[0]
    if sy < 0: uv[1], uv[3] = uv[3], uv[1]
    if draw[0x4c]: uv[1], uv[3] = uv[3], uv[1]
    if draw[0x4d]: uv[0], uv[2] = uv[2], uv[0]
    return [(priority, quad(texture['index'], word(draw,0x48), points, uv))]


class Assets:
    def __init__(self, metadata):
        require(metadata['format'] == 'gta-thaw-private-hud-assets-v1' and metadata['canvas'] == [640,448],
                'Unsupported original HUD asset metadata')
        self.textures, self.fonts = metadata['textures'], metadata['fonts']
        require(0 < len(self.textures) <= 64 and self.fonts, 'Invalid HUD atlas inventory')
        for i, texture in enumerate(self.textures):
            require(texture['index'] == i and 0 < texture['width'] <= 1024 and 0 < texture['height'] <= 1024,
                    'Invalid HUD atlas dimensions/index')
        for font in self.fonts.values():
            require(len(font['lookup']) == 256 and 0 < len(font['glyphs']) <= 256 and
                    all(type(i) is int and 0 <= i < len(font['glyphs']) for i in font['lookup']) and
                    0 <= font['texture_index'] < len(self.textures), 'Invalid HUD font character map')

    def validate_source(self, draw):
        if draw['kind'] == 'text':
            header = bytes.fromhex(draw['font_header_hex'])
            require(len(header) == 0x178, 'Original font header extent differs')
            height, baseline = struct.unpack_from('<2I', header)
            matches = [font for font in self.fonts.values() if (font['height'],font['baseline']) == (height,baseline)
                       and bytes(font['lookup']) == header[12:268]]
            require(len(matches) == 1, 'Original text has no unique matching stock font atlas')
            return matches[0]
        require(draw['kind'] == 'sprite', 'Unsupported original draw kind')
        matches = [t for t in self.textures if t['kind'] == 'image' and t['key'] == draw['image_key']]
        require(len(matches) == 1, f'Original sprite {draw["image_key"]:08x} is absent from verified atlas')
        image = bytes.fromhex(draw['image_header_hex'])
        require(len(image) == 16 and struct.unpack_from('<2H', image, 8) ==
                (matches[0]['width'],matches[0]['height']), 'Original sprite dimensions differ from atlas')
        return matches[0]

    def convert(self, frame, native_read):
        require(frame.get('original_layout_update') is True and frame['canvas'] == [640,448] and
                len(frame['draws']) <= 1024, 'Quads require a completed original HUD layout')
        output = []
        for item in frame['draws']:
            asset = self.validate_source(item)
            raw = bytes.fromhex(item['draw_hex'])
            if item['kind'] == 'sprite':
                output.extend(sprite_quads(raw, bytes.fromhex(item['image_header_hex']), asset))
            else:
                header = bytes.fromhex(item['font_header_hex'])
                expected = b''.join(struct.pack('<4f3h2B', *g['uv'], g['width'], g['height'],
                                    g['baseline'], *g['extra_bytes']) for g in asset['glyphs'])
                require(native_read(word(header,8), len(expected)) == expected,
                        'Original live glyph metrics differ from the selected stock font')
                output.extend(text_quads(raw, bytes.fromhex(item['text_hex']),
                    bytes.fromhex(item['effects_hex']), header, asset))
            require(len(output) <= 1024, 'Original HUD exceeds 1024 quads')
        output.sort(key=lambda row: row[0])
        return [row[1] for row in output]
