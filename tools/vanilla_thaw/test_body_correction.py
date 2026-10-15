import copy
import math
import struct
import unittest
from unittest.mock import Mock

import probe_body_correction as m

BOARD=0x884ef81b
MATRIX=[-.9274460077285767,-.17994633316993713,.3278159499168396,0.,
        -.09289386868476868,.9599966406822205,.2641538381576538,0.,
        -.3622357249259949,.21453633904457092,-.9070609211921692,0.,0.,0.,0.,1.]


def fixture():
    # Two complete original calls (BodyPush then AdjustPhysics), real GTA COL.
    # research/stock-ground-adjust-01.jsonl; no simulated physics displacement.
    before=dict(frame=99,position=[90725.375,1903.9842529296875,41793.265625],
        velocity=[-242.24232482910156,-34.59047317504883,40.5214958190918],
        mode=0,state=0,walking_gate=0,bailing=True,board_visible=False,detached_board_id=BOARD)
    after=dict(before,frame=100,position=[90767.140625,1943.384033203125,41787.88671875],
        velocity=[-165.66061401367188,180.74241638183594,37.531028747558594])
    after['previous_position']=list(after['position'])
    phases=[dict(calls=1,position=s['position'],previous_position=s['position'],matrix=MATRIX,
        velocity=s['velocity'],core_previous=s['position'],state=0,mode=0,bailing=True) for s in (before,after)]
    record=dict(frame=100,dt=struct.unpack('<f',struct.pack('<f',1/60))[0],actor_id=0,before=phases[0],after=phases[1])
    completed=dict(core_previous=list(after['position']),matrix=MATRIX,dt=record['dt'])
    return before,after,record,completed


class BodyCorrectionTests(unittest.TestCase):
    def test_original_body_and_adjust_history(self):
        before,after,record,completed=fixture()
        original=copy.deepcopy((before,after,record,completed))
        self.assertTrue(m.proves(before,after,MATRIX,record,completed,BOARD))
        shifted=dict(after,position=[after['position'][i]-2.5*MATRIX[i] for i in range(3)])
        self.assertTrue(m.proves(before,shifted,MATRIX,record,completed,BOARD))
        self.assertEqual((before,after,record,completed),original)
        # Four float32 ULPs plus .001in allows original final float publication,
        # never a broad allowance for a second unrelated movement.
        tolerance=m.tolerance(after['position'])
        self.assertLess(tolerance,.04)
        shifted=dict(after,position=[after['position'][0]+tolerance*1.01,*after['position'][1:]])
        self.assertFalse(m.proves(before,shifted,MATRIX,record,completed,BOARD))

    def test_no_bail_onset_air_walking_foreign_board_or_stale_frame(self):
        before,after,record,completed=fixture()
        for which,base in ((0,before),(1,after)):
            for change in (dict(mode=1),dict(state=1),dict(state=4),dict(bailing=False),
                           dict(board_visible=True),dict(detached_board_id=BOARD+1),
                           dict(walking_gate=1),dict(frame=base['frame']+1),dict(velocity=[math.nan,0.,0.])):
                with self.subTest(which=which,change=change):
                    samples=[before,after];samples[which]=dict(base,**change)
                    self.assertFalse(m.proves(*samples,MATRIX,record,completed,BOARD))
        self.assertFalse(m.proves(before,after,MATRIX,record,completed,None))

    def test_records_require_exact_current_original_once_and_all_stage_bounds(self):
        before,after,record,completed=fixture()
        def prove(r=record,c=completed):return m.proves(before,after,MATRIX,r,c,BOARD)
        for change in (dict(frame=99),dict(dt=1/30),dict(dt=math.nan),dict(before=None),dict(after=None)):
            self.assertFalse(prove(dict(record,**change)))
        for phase in ('before','after'):
            for change in (dict(calls=0),dict(calls=2),dict(state=1),dict(mode=1),dict(bailing=False),
                           dict(matrix=[math.inf]+MATRIX[1:])):
                with self.subTest(phase=phase,change=change):
                    self.assertFalse(prove(dict(record,**{phase:dict(record[phase],**change)})))
        self.assertFalse(prove(dict(record,after=dict(record['after'],position=before['position']))))
        self.assertFalse(prove(dict(record,before=dict(record['before'],position=[before['position'][0]-41,*before['position'][1:]]))))
        self.assertFalse(prove(dict(record,after=dict(record['after'],position=[before['position'][0]+80,*before['position'][1:]]))))
        self.assertFalse(prove(c=dict(completed,dt=0.)))
        self.assertFalse(prove(c=dict(completed,dt=math.nan)))
        self.assertFalse(prove(c=dict(completed,core_previous=[after['position'][0]+1,*after['position'][1:]])))
        wrong=copy.deepcopy(after);wrong['velocity'][0]+=1
        self.assertFalse(m.proves(before,wrong,MATRIX,record,completed,BOARD))

    def test_hot_path_and_first_sample_do_not_read_native_ring(self):
        before,after,record,completed=fixture()
        witness=Mock(bail_board=BOARD)
        witness.completed.return_value=(record,completed)
        tracker=m.BodyCorrection(witness)
        self.assertFalse(tracker.update(dict(sample=before,actor_matrix=MATRIX)))
        self.assertEqual(witness.mock_calls,[])
        self.assertTrue(tracker.update(dict(sample=after,actor_matrix=MATRIX)))
        witness.completed.assert_called_once_with(after)
        for frame in range(101,201):
            current=dict(after,frame=frame)
            self.assertFalse(tracker.update(dict(sample=current,actor_matrix=MATRIX)))
        witness.completed.assert_called_once()

    def test_native_wrapper_preserves_state_and_calls_original_once(self):
        address,actor,core,state,data=0x20000000,0x300000,0x310000,0x320000,0x330000
        before=b'\x51'+m.begin_stub(actor,core,data)+m.copy_stub(actor,core,state,data,0)
        code=m.wrapper(address,actor,core,state,data)
        call=m.relative_call(address+len(before),m.TARGET)
        self.assertEqual(code[:len(before)],before)
        self.assertEqual(code[len(before):len(before)+5],call)
        self.assertEqual(code.count(call),1)
        self.assertEqual(code[-5:],b'\x8d\x64\x24\x04\xc3')
        self.assertEqual(code.count(b'\x9c\x60'),3)
        self.assertEqual(code.count(b'\x61\x9d'),3)
        self.assertIn(b'\xa1'+struct.pack('<I',0x8459d0),m.begin_stub(actor,core,data))
        self.assertLessEqual(64+2*m.RECORD,m.SLOT)
        self.assertLessEqual(64+2*m.SLOT,4096)

    def test_owned_component_extent_and_kind_before_native_snapshot(self):
        witness=m.BodyWitness.__new__(m.BodyWitness)
        witness.actor,witness.core,witness.state=0x110000,0x120000,0x130000
        words={0x84376c:0x100000,0x843770:0x140000,0x110000:0x6989e8,
               0x12000c:0x110000,0x13000c:0x110000,0x12192c:0x130000}
        headers={0x120000:struct.pack('<4I',0x6a0d58,0x5bd63b29,0,0x110000),
                 0x130000:struct.pack('<4I',0x6a1200,0x43686585,0,0x110000)}
        witness.system=Mock(word=lambda a:words[a],read=lambda a,n:headers[a])
        witness._identity()
        words[0x843770]=0x13013f
        with self.assertRaisesRegex(RuntimeError,'ownership'):witness._identity()
        words[0x843770]=0x140000;words[0x12000c]=0x110004
        with self.assertRaisesRegex(RuntimeError,'ownership'):witness._identity()
        witness.system.write.assert_not_called();witness.system.worker.execute.assert_not_called()

    def test_ring_is_two_bounded_slots_and_stale_record_cannot_prove(self):
        witness=m.BodyWitness.__new__(m.BodyWitness)
        witness.actor,witness.core,witness.state=0x110000,0x120000,0x130000
        witness.data=0x140000;witness.actor_id=0
        witness.verify_patch=Mock();witness._identity=Mock()
        before,after,_,_=fixture()
        def phase(sample):
            floats=(*sample['position'],1.,*sample['position'],1.,*MATRIX,*sample['velocity'],0.,*sample['position'],1.)
            return struct.pack('<4I32f4I',1,witness.actor,witness.core,witness.state,*floats,0,0,1,0x47545243)
        raw=bytearray(m.SLOT)
        struct.pack_into('<QfI',raw,0,100,1/60,0)
        raw[64:64+m.RECORD]=phase(before);raw[64+m.RECORD:64+2*m.RECORD]=phase(after)
        witness.system=Mock(read=Mock(return_value=bytes(raw)))
        got=witness.record(100)
        self.assertEqual(got['frame'],100)
        self.assertEqual(got['after']['position'],tuple(after['position']))
        witness.system.read.assert_called_with(witness.data+64,m.SLOT)
        self.assertIsNone(witness.record(102))
        self.assertIsNone(witness.record(101))
        witness.system.read.assert_called_with(witness.data+64+m.SLOT,m.SLOT)
        for frame in (-1,0,2**64):
            with self.assertRaises(RuntimeError):witness.record(frame)
        struct.pack_into('<I',raw,12,1)
        witness.system.read.return_value=bytes(raw)
        with self.assertRaisesRegex(RuntimeError,'actor differs'):witness.record(100)
        witness.system.write.assert_not_called();witness.system.worker.execute.assert_not_called()


if __name__=='__main__':unittest.main()
