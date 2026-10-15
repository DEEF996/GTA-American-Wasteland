import hashlib
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import probe_vehicle_transform as m


class VehicleTransformTests(unittest.TestCase):
    def fixture(self):
        data={0x420000:b'position',0x420100:b'orientation'}
        pins={name:(name,address,len(raw),hashlib.sha256(raw).hexdigest())
              for name,address,raw in (('GroupSetPosition',0x420000,data[0x420000]),
                                      ('GroupSetOrientation',0x420100,data[0x420100]))}
        world=SimpleNamespace(pins=pins,record=0x200000,deadline=123.,poisoned=False,
            api=object(),process=1,log=Mock(),write=Mock())
        worker=Mock();world.system=SimpleNamespace(worker=worker)
        def prepare(code):data[0x300000]=code;return 0x300000
        worker.prepare_command.side_effect=prepare;worker.execute.return_value=(0,0)
        record=[0]*18;record[0]=6;record[-1]=2
        data[world.record]=struct.pack('<18I',*record)
        world.read=Mock(side_effect=lambda address,size:data[address][:size])
        return world,data,record

    def test_stub_two_exact_thiscalls_no_stack_cleanup_between_callee_clean_calls(self):
        code=m.transform_stub(0x420000,0x420100,0x100000,0x110000,0x200000)
        pack=lambda x:struct.pack('<I',x)
        calls=[b'\x68'+pack(pointer)+b'\xb9'+pack(0x110000)+b'\xb8'+pack(target)+b'\xff\xd0'
               for pointer,target in ((0x100068,0x420000),(0x100088,0x420100))]
        self.assertLess(code.index(calls[0]),code.index(calls[1]))
        self.assertEqual(code.count(b'\xff\xd0'),2)
        self.assertTrue(code.endswith(b'\xc7\x05'+pack(0x200000)+pack(6)+m.host.STUB))
        self.assertNotIn(b'\x83\xc4',code)
        self.assertLess(len(code),4096)
        for value in (0,0x10001,0xfffffff0,True):
            with self.assertRaises(RuntimeError):m.transform_stub(0x420000,0x420100,value,0x110000,0x200000)

    def test_success_validates_both_pins_and_executes_one_command(self):
        world,data,_=self.fixture()
        m.apply(world,'Group',0x100000,0x110000)
        worker=world.system.worker
        worker.check_call_access.assert_called_once();worker.prepare_command.assert_called_once()
        worker.execute.assert_called_once_with(0x300000,123.)
        self.assertEqual(world.write.call_args_list,[((0x200024,bytes(36)),),((0x2001c4,bytes(24)),)])
        self.assertEqual([c.args[0] for c in world.read.call_args_list],
                         [0x420000,0x420100,0x200000,0x300000,0x420000,0x420100])
        self.assertFalse(world.poisoned)

    def test_foreign_pin_or_worker_access_fails_before_command_or_record_mutation(self):
        for problem in ('pin','access'):
            world,data,_=self.fixture()
            if problem=='pin':data[0x420100]=b'corruption!'
            else:world.system.worker.check_call_access.side_effect=RuntimeError('busy')
            with self.assertRaises(RuntimeError):m.apply(world,'Group',0x100000,0x110000)
            world.write.assert_not_called();world.system.worker.execute.assert_not_called()

    def test_native_failure_is_poisoned_and_keeps_fault_stage(self):
        world,data,record=self.fixture();record[0]=2;record[10]=0x420100
        data[world.record]=struct.pack('<18I',*record)
        with patch.object(m.host,'read_fault_stack',return_value={'stack':[1,2]}):
            with self.assertRaisesRegex(RuntimeError,'stage=2'):
                m.apply(world,'Group',0x100000,0x110000)
        self.assertTrue(world.poisoned)
        self.assertEqual(world.log.call_args.kwargs['eip'],0x420100)
        self.assertEqual(world.log.call_args.kwargs['stack'],[1,2])

    def test_postcall_pin_and_glue_changes_or_execute_failure_poison(self):
        for problem in ('pin','glue','execute'):
            world,data,_=self.fixture()
            def execute(*args):
                if problem=='execute':raise RuntimeError('worker failed')
                address=0x420100 if problem=='pin' else 0x300000
                data[address]=bytes(len(data[address]))
                return 0,0
            world.system.worker.execute.side_effect=execute
            with self.assertRaises(RuntimeError):m.apply(world,'Group',0x100000,0x110000)
            self.assertTrue(world.poisoned)


if __name__=='__main__':unittest.main()
