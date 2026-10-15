import copy
import math
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import moving_entry as m
from geometry import gta_velocity_to_thaw


def state(**kw):
    return dict(dict(protocol='GTATHAW2',pid=17,creation_100ns=123,ped_address=0x20000,
        epoch=3,area=0,ped_area=0,control_eligible=True,grounded=True,ped_motion_flags=1,
        flags=25,age_ms=1,sequence=4,f7_requests=8,position=[2240.,-1261.,23.],
        heading=.4,raw_gta_velocity=[.12,-.08,.01]),**kw)


class Clock:
    value=0.
    def now(self): return self.value
    def sleep(self,seconds): self.value+=seconds
    def ticks(self): return 1000+round(self.value*1000)


class Observer:
    def __init__(self, states, clock):
        self.states=list(states)
        self.api=SimpleNamespace(GetTickCount64=clock.ticks)
    def read(self):
        if len(self.states)>1: return copy.deepcopy(self.states.pop(0))
        return copy.deepcopy(self.states[0])


class EntryTests(unittest.TestCase):
    def begin(self, after=None):
        clock=Clock();publisher=Mock()
        captured=state(flags=25|m.OWNED|m.PREPARING,sequence=6,position=[2240.02,-1261.01,23.],
                       raw_gta_velocity=[.15,-.07,.01]) if after is None else after
        observer=Observer([state(),state(),captured],clock)
        entry=m.begin(observer,publisher,3000,clock=clock.now,sleep=clock.sleep)
        return entry,observer,publisher,clock

    def test_capture_real_tick_and_commit_preserves_all_velocity_components(self):
        entry,observer,publisher,clock=self.begin()
        self.assertNotEqual(entry['captured']['position'],state()['position'])
        prepared=publisher.publish.call_args.args[0]
        self.assertEqual(struct.unpack_from('<I3Q',prepared,100),(0,0,0,1))
        self.assertEqual(struct.unpack_from('<4I',prepared,128),(0,0,0,0))
        current=entry['captured']
        observer.states=[current,dict(current,flags=current['flags']&~m.PREPARING,sequence=7)]
        m.commit(observer,publisher,entry,clock=clock.now,sleep=clock.sleep)
        calls=publisher.publish.call_args_list
        self.assertEqual([call.args[1] for call in calls],[1,2])
        raw=calls[1].args[0]
        self.assertEqual(struct.unpack_from('<I3Q',raw,100),(1,0,0,1))
        self.assertEqual(struct.unpack_from('<4I',raw,128),(0,0,0,0))
        values=struct.unpack_from('<7f',raw,72)
        for actual,expected in zip(values,(*current['position'],current['heading'],*current['raw_gta_velocity'])):
            self.assertAlmostEqual(actual,expected,places=3)
        self.assertNotEqual(gta_velocity_to_thaw(current['raw_gta_velocity']),[0,0,0])
        publisher.clear.assert_not_called()

    def test_reject_air_and_unsupported_or_stale_capture(self):
        for change in (dict(grounded=False),dict(ped_motion_flags=3),dict(age_ms=101),
                       dict(raw_gta_velocity=[math.inf,0,0]),dict(raw_gta_velocity=[1.3,0,0])):
            s=state(**change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                m.prepare_packet(s,m.movement.scene(state()),9,1000,3000,1)
        for change in (dict(flags=25|m.OWNED),dict(sequence=4),dict(f7_requests=9),dict(epoch=4)):
            s=state(flags=25|m.OWNED|m.PREPARING,sequence=6);s.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError): self.begin(s)

    def test_prepare_published_once_timeout_clears_without_zero_velocity_command(self):
        clock=Clock();publisher=Mock();observer=Observer([state()],clock)
        with self.assertRaisesRegex(ValueError,'acknowledge'):
            m.begin(observer,publisher,3000,clock=clock.now,sleep=clock.sleep)
        self.assertEqual(publisher.publish.call_count,1)
        publisher.clear.assert_called_once()
        self.assertEqual(struct.unpack_from('<I',publisher.publish.call_args.args[0],100)[0],0)

    def test_normal_capture_rejection_is_typed_but_invalid_protocol_is_not_retryable(self):
        clock=Clock();publisher=Mock();observer=Observer([state()],clock)
        with self.assertRaises(m.EntryUnavailable):
            m.begin(observer,publisher,3000,clock=clock.now,sleep=clock.sleep)
        publisher.clear.assert_called_once()
        observer=Observer([state(protocol='invalid')],Clock())
        with self.assertRaises(ValueError) as failure:
            m.begin(observer,Mock(),3000)
        self.assertNotIsInstance(failure.exception,m.EntryUnavailable)

    def test_original_failure_or_foreign_motion_cannot_commit(self):
        for changed in (dict(position=[2241.,-1261.,23.]),dict(raw_gta_velocity=[0.,0.,0.]),
                        dict(heading=.8),dict(f7_requests=9),dict(flags=25)):
            entry,observer,publisher,clock=self.begin()
            observer.states=[dict(entry['captured'],**changed)]
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                m.commit(observer,publisher,entry,clock=clock.now,sleep=clock.sleep)
            self.assertEqual(publisher.publish.call_count,1)
            publisher.clear.assert_called_once()

    def test_source_handoff_deadline_cannot_be_renewed(self):
        entry,observer,publisher,clock=self.begin()
        clock.value=.5
        with self.assertRaisesRegex(ValueError,'deadline'):
            m.commit(observer,publisher,entry,clock=clock.now,sleep=clock.sleep)
        self.assertEqual(publisher.publish.call_count,1)
        publisher.clear.assert_called_once()

    def test_commit_requires_actual_game_ack_and_clears_on_timeout(self):
        entry,observer,publisher,clock=self.begin()
        with self.assertRaisesRegex(ValueError,'commit'):
            m.commit(observer,publisher,entry,clock=clock.now,sleep=clock.sleep)
        self.assertEqual(publisher.publish.call_count,2)
        publisher.clear.assert_called_once()

    def test_alignment_calls_original_handoff_with_transferred_velocity(self):
        import live_session as live
        entry,observer,publisher,clock=self.begin()
        captured=entry['captured'];origin=list(captured['position']);origin[2]-=1
        system=SimpleNamespace(allocate=lambda data: 0x10000, read=lambda at,n:
            struct.pack('<4f',0,0,0,1) if at==0x10034 else struct.pack('<4f',0,1,0,0))
        world=dict(coordinates=dict(origin_gta=origin,thaw_units_per_gta_unit=100/2.54))
        with patch.object(live.host,'native_call',side_effect=[0x10000,1]), \
             patch.object(live.host,'remote_write'),patch.object(live.probe_switch_skating,'run'), \
             patch.object(live.probe_handoff,'run') as handoff:
            result=live.align_entry(None,None,None,None,Mock(),{},system,observer,world,{},entry)
        self.assertEqual(handoff.call_args.args[8],gta_velocity_to_thaw(captured['raw_gta_velocity']))
        self.assertTrue(handoff.call_args.args[10])
        self.assertEqual(result[0],captured)
        self.assertTrue(.5<result[2]<1.5)

    def test_ground_alignment_reuses_owned_feeler_but_reconstructs_before_each_query(self):
        import live_session as live
        entry,observer,publisher,clock=self.begin()
        captured=entry['captured'];origin=list(captured['position']);origin[2]-=1
        allocate=Mock(return_value=0x10000)
        system=SimpleNamespace(allocate=allocate, read=lambda at,n:
            struct.pack('<4f',0,0,0,1) if at==0x10034 else struct.pack('<4f',0,1,0,0))
        world=dict(coordinates=dict(origin_gta=origin,thaw_units_per_gta_unit=100/2.54))
        context={}
        with patch.object(live.host,'native_call',side_effect=[0x10000,1,0x10000,1]) as native, \
             patch.object(live.host,'remote_write'),patch.object(live.probe_switch_skating,'run'), \
             patch.object(live.probe_handoff,'run'):
            for _ in range(2):
                live.align_entry(None,None,None,None,Mock(),context,system,observer,world,{},entry)
        allocate.assert_called_once_with(bytes(0xc0))
        self.assertEqual(context['gta_entry_feeler'],0x10000)
        self.assertEqual([call.args[2] for call in native.call_args_list],
            [live.probe_scene.PINS['feeler'],live.probe_scene.PINS['query']]*2)

    def test_grounded_hillside_entry_accepts_actual_cunteground05_contact_at_world_origin(self):
        import live_session as live
        entry,observer,_,_=self.begin()
        # Exact failed live entry and front hit from original cunteground05,
        # instance13968 triangle296; 33.09deg and root height1.002598m.
        entry['captured']['position']=[1898.515258789,-306.487670898,17.516260147]
        observer.states=[entry['captured']]
        contact=(74744.6953125,650.1441836783076,12066.443359375,1.)
        normal=(.5443485772476084,.8377891052753074,.042356127425727245,0.)
        system=SimpleNamespace(allocate=lambda data: 0x10000, read=lambda at,n:
            struct.pack('<4f',*(contact if at==0x10034 else normal)))
        world=dict(coordinates=dict(origin_gta=[0,0,0],thaw_units_per_gta_unit=100/2.54))
        with patch.object(live.host,'native_call',side_effect=[0x10000,1]), \
             patch.object(live.host,'remote_write'),patch.object(live.probe_switch_skating,'run'), \
             patch.object(live.probe_handoff,'run') as handoff:
            result=live.align_entry(None,None,None,None,Mock(),{},system,observer,world,{},entry,streamed=True)
        self.assertAlmostEqual(result[2],1.00257248,places=5)  # Original .001-inch contact lift.
        self.assertTrue(handoff.call_args.args[10])

    def test_ground_entry_uses_native_point_tolerance_but_rejects_walls_undersides_and_missing_hits(self):
        import live_session as live
        for hit,normal,dx,accepted in ((1,(0,1,0,0),.05,True),
                (0,(0,1,0,0),0,False),(1,(1,0,0,0),0,False),
                (1,(0,-1,0,0),0,False),(1,(0,.8,0,0),0,False),
                (1,(0,1,0,0),.2,False)):
            with self.subTest(hit=hit,normal=normal,dx=dx):
                entry,observer,_,_=self.begin()
                origin=list(entry['captured']['position']);origin[2]-=1
                system=SimpleNamespace(allocate=lambda data:0x10000,read=lambda at,n:
                    struct.pack('<4f',*((dx,0,0,1) if at==0x10034 else normal)))
                world=dict(coordinates=dict(origin_gta=origin,thaw_units_per_gta_unit=100/2.54))
                with patch.object(live.host,'native_call',side_effect=[0x10000,hit]), \
                     patch.object(live.host,'remote_write'),patch.object(live.probe_switch_skating,'run'), \
                     patch.object(live.probe_handoff,'run') as handoff:
                    if accepted:
                        live.align_entry(None,None,None,None,Mock(),{},system,observer,world,{},entry)
                        handoff.assert_called_once()
                    else:
                        with self.assertRaisesRegex(m.EntryUnavailable,'static ground below CJ'):
                            live.align_entry(None,None,None,None,Mock(),{},system,observer,world,{},entry)
                        handoff.assert_not_called()

    def test_air_requires_same_scene_calibration_before_any_publication(self):
        air=state(grounded=False,ped_motion_flags=2,raw_gta_velocity=[.12,-.08,.11])
        identity=m.movement.scene(air)
        for calibration in (None,dict(identity=identity,offset=math.nan),
                            dict(identity=identity,offset=2),dict(identity=(0,)*6,offset=1)):
            publisher=Mock();clock=Clock();observer=Observer([air],clock)
            with self.subTest(calibration=calibration),self.assertRaisesRegex(ValueError,'calibration'):
                m.begin(observer,publisher,3000,calibration=calibration,clock=clock.now,sleep=clock.sleep)
            publisher.publish.assert_not_called()
            publisher.clear.assert_called_once()

    def test_air_commit_retains_air_contact_and_height_without_ground_feeler(self):
        import live_session as live
        clock=Clock();publisher=Mock()
        air=state(grounded=False,ped_motion_flags=2,position=[2240.,-1261.,26.],raw_gta_velocity=[.12,-.08,.11])
        captured=dict(air,sequence=6,flags=air['flags']|m.OWNED|m.PREPARING)
        calibration=dict(identity=m.movement.scene(air),offset=.98)
        observer=Observer([air,captured],clock)
        entry=m.begin(observer,publisher,3000,calibration=calibration,clock=clock.now,sleep=clock.sleep)
        self.assertEqual(struct.unpack_from('<I3Q',publisher.publish.call_args.args[0],100),(3,0,0,1))
        system=SimpleNamespace(allocate=Mock(),read=Mock())
        world=dict(coordinates=dict(origin_gta=[2240.,-1261.,22.],thaw_units_per_gta_unit=100/2.54))
        with patch.object(live.host,'native_call') as native,patch.object(live.host,'remote_write'), \
             patch.object(live.probe_switch_skating,'run'),patch.object(live.probe_handoff,'run') as handoff:
            live.align_entry(None,None,None,None,Mock(),{},system,observer,world,{},entry,calibration)
        native.assert_not_called();system.allocate.assert_not_called();system.read.assert_not_called()
        self.assertAlmostEqual(handoff.call_args.args[7][1],(26-.98-22)*100/2.54)
        self.assertEqual(handoff.call_args.args[8],gta_velocity_to_thaw(air['raw_gta_velocity']))
        self.assertFalse(handoff.call_args.args[10])
        observer.states=[captured,dict(captured,flags=captured['flags']&~m.PREPARING,sequence=7)]
        m.commit(observer,publisher,entry,clock=clock.now,sleep=clock.sleep)
        self.assertEqual(struct.unpack_from('<I3Q',publisher.publish.call_args.args[0],100),(2,0,0,1))
        self.assertEqual(struct.unpack_from('<4I',publisher.publish.call_args.args[0],128),(0,0,0,0))


if __name__=='__main__': unittest.main()
