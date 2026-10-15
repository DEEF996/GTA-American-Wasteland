import math
import struct
import unittest
import hud_quads as h


def font_fixture():
    font = dict(height=20, baseline=15, texture_index=0, lookup=[0] * 256,
                glyphs=[dict(uv=[0,0,1,1], width=6, height=10, baseline=11, extra_bytes=[0,0])])
    header = bytearray(0x178)
    struct.pack_into('<3I', header, 0, 20, 15, 0x10000)
    struct.pack_into('<2hI', header, 0x134, -1, 8, 0x80643219)
    draw = bytearray(0x4c)
    struct.pack_into('<7fI', draw, 0x28, 100,200,2,3,0,0,0,0x80808080)
    return font, header, draw


class QuadTests(unittest.TestCase):
    def test_existing_original_font_advance_baseline_and_palette(self):
        font, header, draw = font_fixture()
        rows = h.text_quads(draw, b'A \\c1A', b'', header, font)
        self.assertEqual(rows[0][1]['vertices'][0], [100,212,0,0])
        self.assertEqual(rows[1][1]['vertices'][0], [124,212,0,0])
        self.assertEqual(rows[1][1]['color'], h.color_argb(0x80643219))

    def test_original_sprite_anchor_rotation_and_uv_direction(self):
        raw, image = bytearray(0x50), bytearray(16)
        struct.pack_into('<2f2H7f', raw, 0x20, 250,224,128,64,.5,.5,0,0,64,32,-math.pi/2)
        struct.pack_into('<2H', image, 12, 128,64)
        point = h.sprite_quads(raw,image,dict(index=0,width=128,height=64))[0][1]['vertices'][0]
        self.assertAlmostEqual(point[0],234,places=4)
        self.assertAlmostEqual(point[1],256,places=4)
        self.assertEqual(point[2:],[0,1])

    def test_native_per_glyph_effect_and_shadow_alpha_override(self):
        font, header, draw = font_fixture()
        draw[0x49] = 1
        effects = struct.pack('<5h', 4,6,8192,16384,0) * 5
        row = h.text_quads(draw,b'\\c1A',effects[:40],header,font)[0][1]
        self.assertEqual(row['color'] >> 24, 0x80)
        self.assertEqual(row['vertices'][2][:2],[110,248])

    def test_bad_native_geometry_and_unverified_buttons_refused(self):
        font, header, draw = font_fixture()
        for text in (b'\\c',b'\\s',b'\\cvA',b'\\b0'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                h.text_quads(draw,text,b'',header,font)
        struct.pack_into('<f',draw,0x28,math.nan)
        with self.assertRaises(ValueError): h.text_quads(draw,b'A',b'',header,font)

    def test_live_metrics_are_verified_against_private_original_font(self):
        font, header, draw = font_fixture()
        assets = h.Assets(dict(format='gta-thaw-private-hud-assets-v1',canvas=[640,448],
            textures=[dict(index=0,width=64,height=64,kind='font',key=1)],fonts={'synthetic':font}))
        item = dict(kind='text',font_header_hex=header.hex(),draw_hex=draw.hex(),
                    text_hex=b'A'.hex(),effects_hex='')
        frame = dict(original_layout_update=True,canvas=[640,448],draws=[item])
        expected = struct.pack('<4f3h2B',0,0,1,1,6,10,11,0,0)
        self.assertEqual(len(assets.convert(frame,lambda at,n: expected)),1)
        with self.assertRaisesRegex(ValueError,'metrics'):
            assets.convert(frame,lambda at,n: bytes(n))


if __name__ == '__main__': unittest.main()
