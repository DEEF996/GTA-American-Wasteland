import ctypes as C
import struct
import unittest
from types import SimpleNamespace
import preview_hold as h
from preview_pose import Publisher


class HoldTests(unittest.TestCase):
    def state(self):
        return dict(protocol="GTATHAW2",pid=17,creation_100ns=123,ped_address=0x20000,
                    epoch=3,area=0,ped_area=0,control_eligible=True,grounded=True,flags=25,age_ms=1)

    def test_packet_carries_only_session_lease_and_identity(self):
        s=self.state(); raw=h.packet(s,h.scene(s),99,5,1000,2000)
        self.assertEqual(len(raw),72)
        self.assertEqual(h.BODY.unpack(raw),(b"GTAHOLD1",1,72,17,3,123,99,5,1000,0x20000,0,0,2000))

    def test_scene_change_and_stale_ineligible_states_fail(self):
        s=self.state(); identity=h.scene(s)
        for key,value in (("pid",18),("creation_100ns",124),("ped_address",0x30000),("epoch",4),
                          ("area",1),("grounded",False),("control_eligible",False),("age_ms",101),("flags",17)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                h.packet(dict(s,**{key:value}),identity,99,1,1000,1000)

    def test_request_duration_and_sequence_are_bounded(self):
        s=self.state()
        for request,sequence,duration in ((0,1,1000),(99,0,1000),(99,1,0),(99,1,2001),(99,0x3fffffff,1000)):
            with self.subTest(request=request,sequence=sequence,duration=duration), self.assertRaises(ValueError):
                h.packet(s,h.scene(s),request,sequence,1000,duration)

    def test_reused_publisher_respects_small_mapping_boundaries(self):
        memory=C.create_string_buffer(88)
        C.memset(C.addressof(memory)+80,0xAC,8)
        writer=Publisher.__new__(Publisher)
        writer.packet_size=72; writer.view=C.addressof(memory)
        barriers=[]
        def exchange(address,value):
            C.c_uint32.from_address(address).value=value
            barriers.append(bytes(memory))
            return 1
        writer.exchange32=exchange
        s=self.state(); raw=h.packet(s,h.scene(s),99,5,1000,1000)
        writer.publish(raw,5)
        self.assertEqual(struct.unpack_from("<I",barriers[0])[0],9)
        self.assertEqual(barriers[1][4:76],raw)
        self.assertEqual(struct.unpack_from("<I",memory,76)[0],10)
        self.assertEqual(bytes(memory)[80:],b"\xAC"*8)
        with self.assertRaises(ValueError): writer.publish(raw+b"extra",6)


if __name__ == "__main__": unittest.main()
