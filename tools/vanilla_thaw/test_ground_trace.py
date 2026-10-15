import struct
import unittest
from unittest.mock import Mock,patch

import probe_ground_trace as m


class GroundTraceTests(unittest.TestCase):
    def test_default_does_not_read_or_patch(self):
        system=Mock()
        with patch.dict('os.environ',{},clear=True):
            self.assertIsNone(m.initialize(None,None,system,{},Mock()))
        self.assertEqual(system.mock_calls,[])

    def test_call_target_and_wrapper_retained_this(self):
        address=0x20000000;target=0x5d2e90
        code=m.wrapper(address,target,0x300000,0x310000,0x320000,0x330000,2)
        before=b'\x51'+m.copy_stub(0x300000,0x310000,0x320000,0x330000,2)
        self.assertEqual(code[:len(before)],before)
        self.assertEqual(code[len(before):len(before)+5],m.relative_call(address+len(before),target))
        self.assertEqual(code[-5:],b'\x8d\x64\x24\x04\xc3')
        # Both guards use the retained incoming this even if original ECX changes.
        self.assertEqual(code.count(b'\x81\x7c\x24\x24'+struct.pack('<I',0x310000)),2)

    def test_completed_record_and_corruption(self):
        a,c,s=0x300000,0x310000,0x320000
        vectors=[float(i) for i in range(32)]
        raw=struct.pack('<4I32f4I',1,a,c,s,*vectors,0,0,1,0x47545243)
        got=m.decode_record(raw,a,c,s)
        self.assertEqual(got['position'],tuple(vectors[:3]))
        self.assertEqual(got['velocity'],tuple(vectors[24:27]))
        self.assertTrue(got['bailing'])
        for offset,value in ((4,0x400000),(8,c+4),(12,s+4),(156,0),(0,9)):
            broken=bytearray(raw);struct.pack_into('<I',broken,offset,value)
            with self.subTest(offset=offset),self.assertRaises(RuntimeError):m.decode_record(broken,a,c,s)
        broken=bytearray(raw);struct.pack_into('<f',broken,16,float('nan'))
        with self.assertRaises(RuntimeError):m.decode_record(broken,a,c,s)
        self.assertIsNone(m.decode_record(bytes(m.RECORD),a,c,s))

    def test_record_ring_bound(self):
        self.assertEqual(m.STAGES[m.NATIVE_STAGES:],('components_after','spawned_scripts_after'))
        self.assertEqual(m.CALLS[-1],(0x5e89ba,0x5df9f0,4))
        self.assertEqual(m.CALLS[1],(0x5e0e1f,0x5d8650,2))
        self.assertNotIn(0x5e9024,[row[0] for row in m.CALLS])
        self.assertLessEqual(64+m.NATIVE_STAGES*m.RECORD,m.SLOT)
        self.assertLessEqual(64+2*m.SLOT,4096)
        trace=m.GroundTrace.__new__(m.GroundTrace)
        trace.system=Mock();trace.data=0x300000;trace.frame=0;trace.stages={}
        for frame in range(1,5):
            trace.begin(frame)
            trace.stages[frame]={'test':True}
            self.assertLessEqual(len(trace.stages),2)
        slots={call.args[0] for call in trace.system.write.call_args_list if len(call.args[1])==m.SLOT}
        self.assertEqual(len(slots),2)
        with self.assertRaises(RuntimeError):trace.begin(4)

    def test_debug_snapshot_uses_shared_body_records_without_second_call(self):
        trace=m.GroundTrace.__new__(m.GroundTrace)
        trace.system=Mock();trace.data=0x300000;trace.code=0x400000;trace.glue=b'code'
        trace.actor,trace.core,trace.state=0x110000,0x120000,0x130000
        trace.frame=1;trace.stages={};trace.patches=[]
        raw=struct.pack('<Q',1)+bytes(m.SLOT-8)
        trace.system.read.side_effect=lambda a,n:trace.glue if a==trace.code else raw
        before,after={'calls':1,'position':[1.,2.,3.]},{'calls':1,'position':[4.,5.,6.]}
        trace.body_witness=Mock()
        trace.body_witness.record.return_value={'before':before,'after':after}
        got=trace.snapshot()['frames'][0]
        self.assertEqual(got['stages']['body_push_before'],before)
        self.assertEqual(got['stages']['body_push_after'],after)
        trace.body_witness.record.assert_called_once_with(1)
        trace.system.write.assert_not_called();trace.system.worker.execute.assert_not_called()

    def test_original_pin_normalizes_only_exact_owned_nested_call(self):
        trace=m.GroundTrace.__new__(m.GroundTrace)
        trace.system=Mock();trace.code=0x400000;trace.glue=b'original once wrapper'
        old=m.relative_call(0x5e0e1f,0x5d8650);new=m.relative_call(0x5e0e1f,trace.code)
        trace.patches=[(0x5e0e1f,old,new)]
        code=b'xx'+new+b'unchanged'
        trace.system.read.side_effect=lambda a,n:trace.glue if a==trace.code else code
        self.assertEqual(trace.original_bytes(0x5e0e1d,len(code)),b'xx'+old+b'unchanged')
        code=b'yy'+new+b'other change'
        self.assertEqual(trace.original_bytes(0x5e0e1d,len(code)),b'yy'+old+b'other change')
        code=b'xx'+old+b'unchanged'
        with self.assertRaisesRegex(RuntimeError,'trace code changed'):
            trace.original_bytes(0x5e0e1d,len(code))
        code=b'xx'+new+b'unchanged'
        trace.system.read.side_effect=lambda a,n:b'wrong wrapper' if a==trace.code else code
        with self.assertRaisesRegex(RuntimeError,'trace code changed'):
            trace.original_bytes(0x5e0e1d,len(code))
        trace.system.write.assert_not_called()


if __name__=='__main__':unittest.main()
