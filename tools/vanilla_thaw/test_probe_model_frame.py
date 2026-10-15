import struct
import unittest

from probe_model_frame import ModelFrame, DetachedBoards, PINS, MODEL, GEOM, INSTANCE, board_visibility


class ModelFrameTests(unittest.TestCase):
    def reader(self):
        memory=bytearray(0x4000)
        def write(address,*values):struct.pack_into('<'+'I'*len(values),memory,address,*values)
        def words(address,count):return struct.unpack_from('<'+'I'*count,memory,address)
        def read(address,size):
            if address in PINS:return PINS[address]
            return bytes(memory[address:address+size])
        identity=struct.pack('<16f',*[float(i%5==0) for i in range(16)])
        palette=identity*52;memory[0x2000:0x2000+len(palette)]=palette
        write(0x1000,0,0x286a8d26,0,0x3500);write(0x1208,0x1300)
        write(0x1300,MODEL);write(0x1310,0x1400,3,7);write(0x1344,3<<16)
        write(0x1400,0,0x1420,0x1440)
        for i,part in enumerate((0xa7a9d4b8,0x04e91519,0xe2b2dede)):
            node,item,geom,instance=0x1420+i*16,0x1500+i*32,0x1600+i*96,0x1800+i*128
            write(node,item,node+16 if i<2 else 0x1400,node-16 if i else 0x1400)
            write(item,geom,0,part)
            write(geom,GEOM);write(geom+0x50,instance)
            write(instance,INSTANCE);memory[instance+12:instance+76]=identity
            write(instance+0x4c,0x2000);write(instance+0x5c,52);write(instance+0x68,0x1300)
            write(instance+0x6c,0x3100+i*16)
        return ModelFrame(read,words,0x3500,0x1000),memory,write,palette

    def test_completed_original_frame_and_visibility_are_read_without_synthetic_mode_rules(self):
        reader,memory,write,palette=self.reader()
        matrix,visible=reader.sample(0x2000,palette)
        self.assertTrue(visible);self.assertEqual(matrix,tuple(float(i%5==0) for i in range(16)))
        write(0x1318,0)
        self.assertFalse(reader.sample(0x2000,palette)[1])
        struct.pack_into('<f',memory,0x1800+12+13*4,900.)
        self.assertEqual(reader.sample(0x2000,palette)[0][13],900.)

    def test_wrong_render_count_owner_list_and_palette_refused(self):
        for address,value in ((0x185c,51),(0x1868,0),(0x1428,0),(0x1800,0),(0x1650,0)):
            reader,memory,write,palette=self.reader();write(address,value)
            with self.subTest(address=address),self.assertRaises(RuntimeError):reader.sample(0x2000,palette)
        reader,memory,write,palette=self.reader();write(0x184c,0x2004)
        with self.assertRaisesRegex(RuntimeError,'palette differs'):reader.sample(0x2000,palette)

    def test_named_board_mask_missing_or_partial_refused(self):
        self.assertTrue(board_visibility([123,0xa7a9d4b8,0xe2b2dede],6))
        self.assertFalse(board_visibility([123,0xa7a9d4b8,0xe2b2dede],1))
        with self.assertRaises(RuntimeError):board_visibility([123],1)
        with self.assertRaises(RuntimeError):board_visibility([0xa7a9d4b8,0xe2b2dede],1)

    def detached(self):
        attached,memory,write,palette=self.reader()
        memory.extend(bytes(0x4000))
        attached.sample(0x2000,palette)
        write(0x5000,0x68f7b8,0,0,0x7000,0,0,0x12345678)
        write(0x511c,0x5200)
        components=((0x5200,0x286a8d26),(0x5480,0x222756d5),(0x54a0,0xffb39248),(0x54c0,0x7e747547))
        for index,(address,key) in enumerate(components):
            write(address,0,key,0,0x5000,components[index+1][0] if index<3 else 0)
        write(0x54c0,0x6a0a0c);write(0x54d8,0x3500,5)
        write(0x3500+0x194+5*0x18,0x12345678,1)
        write(0x5408,0x4000)
        write(0x4000,MODEL,0,52);write(0x4010,0x4100,3,7);write(0x4044,3<<16)
        write(0x4100,0,0x4120,0x4140)
        for i,part in enumerate((0xa7a9d4b8,0x04e91519,0xe2b2dede)):
            node,item,geom,instance=0x4120+i*16,0x4200+i*32,0x4300+i*96,0x4500+i*128
            write(node,item,node+16 if i<2 else 0x4100,node-16 if i else 0x4100)
            write(item,geom,0,part)
            write(geom,GEOM);write(geom+0x50,instance)
            memory[instance:instance+0x70]=memory[0x1800+i*128:0x1870+i*128]
            write(instance+0x68,0)
            write(instance+8,9)
            write(instance+0x6c,0x6100+i*16) # Native clone owns a copied scene, not the original pointer.
        return DetachedBoards(attached.words,0x3500,0x7000,attached),memory,write,palette

    def test_detached_board_uses_live_geometry_not_one_observed_generated_id(self):
        reader,memory,write,palette=self.detached()
        board=reader.sample([0x3500,0x5000],False)
        self.assertEqual(board['object_id'],0x12345678)
        self.assertEqual(board['palette'],palette[48*64:])
        self.assertIsNone(reader.sample([0x5000],True))
        self.assertIsNone(reader.sample([],False))
        write(0x5008,1)
        self.assertIsNone(reader.sample([0x5000],False))

    def test_detached_wrong_owner_tracker_and_disagreeing_parts_rejected(self):
        for address,value in ((0x500c,0),(0x520c,0),(0x4568,0x4000),(0x4508,0),
                              (0x54c0,0),(0x54dc,10),(0x3500+0x194+5*0x18,0),
                              (0x45dc,51),(0x4128,0),(0x4228,0xa7a9d4b8)):
            reader,memory,write,palette=self.detached();write(address,value)
            with self.subTest(address=address),self.assertRaises(RuntimeError):
                reader.sample([0x5000],False)
        reader,memory,write,palette=self.detached()
        struct.pack_into('<f',memory,0x4580+12+12*4,10.)
        with self.assertRaisesRegex(RuntimeError,'disagree'):reader.sample([0x5000],False)

    def test_another_skaters_projectile_is_not_selected_and_bail_uses_native_id(self):
        reader,memory,write,palette=self.detached();write(0x54d8,0x3600)
        self.assertIsNone(reader.sample([0x5000],False))
        write(0x5018,0x884ef81b)
        board=reader.sample([0x5000],False)
        self.assertEqual(board['object_id'],0x884ef81b)


if __name__=='__main__':unittest.main()
