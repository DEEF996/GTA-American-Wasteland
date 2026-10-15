import copy
import math
import struct
import unittest
from unittest.mock import patch

import probe_air_correction as m

MATRIX=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.]
# Exact MOVE positions/raw velocities in attempt-0004 sessions 50/55/59.
# Their actor matrices and query normals were not captured: the tests below
# combine these records with the independently proved upright flat-wall basis.
WALL_RELEASES=(
    (50,15304,[1951.6820249999998,-2023.9402625,16.479149264306642],
     [0.,.36502097534179684,.16950287854003906],
     [1952.596425,-2023.6360578125,15.681870797509767],
     [0.,.3656631997070312,3.1005859375e-8]),
    (55,16032,[1951.6820249999998,-2021.5437328125,16.345085775537108],
     [0.,-.3646742368164062,.1907427293701172],
     [1952.596425,-2021.847540625,15.68187044350586],
     [0.,-.36540141723632813,3.1005859375e-8]),
    (59,16332,[1951.6820249999998,-2015.7060984375,16.635058773583985],
     [0.,.28412349462890624,.07485322174072265],
     [1952.596425,-2015.4693625,15.68187044350586],
     [0.,.2843877575683594,1.55029296875e-8]),
)


def sample(frame=18,**changes):
    value=dict(frame=frame,position=[0.,149.60629272460938,0.],velocity=[100.,82.3,0.],
        mode=0,state=1,walk_state=0,walking_gate=0,bailing=False,board_visible=True)
    value.update(changes)
    return value


class AirCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.before=sample()
        self.after=sample(19,position=[1.6666667461395264,80.48031616210938,0.],velocity=[100.,0.,0.])
        self.point=[1.6666667461395264,157.48031616210938,0.,1.]
        self.normal=[0.,-1.,0.,0.]

    def prove(self,before=None,after=None,matrix=None,height=77.,hit=True,point=None,normal=None):
        return m.proves_ceiling(before or self.before,after or self.after,matrix or MATRIX,
            height,hit,point or self.point,normal or self.normal)

    def test_actual_original_ceiling_witness(self):
        self.assertTrue(self.prove())
        self.assertFalse(self.prove(hit=False))
        # Its final Core.feeler had already become a floor query. That is no proof.
        self.assertFalse(self.prove(point=[1.6666667,0.,0.,1.],normal=[0.,1.,0.,0.]))

    def test_only_adjacent_air_skating_not_bail_or_walking(self):
        for key,value in (('frame',20),('mode',1),('state',0),('walking_gate',1),
                          ('bailing',True),('board_visible',False)):
            with self.subTest(key=key):
                self.assertFalse(self.prove(after=dict(self.after,**{key:value})))
        self.assertFalse(self.prove(before=dict(self.before,frame=16)))
        self.assertFalse(self.prove(before=dict(self.before,bailing=True)))

    def test_query_contact_must_match_actual_head_clearance(self):
        for height in (0.,-1.,79.,100.,math.inf,math.nan):
            self.assertFalse(self.prove(height=height))
        for point in ([1.8,157.480316,0.,1.], [1.6666667,157.480316,0.,0.],
                      [1.6666667,math.nan,0.,1.]):
            self.assertFalse(self.prove(point=point))
        for normal in ([1.,0.,0.,0.],[0.,1.,0.,0.],[0.,-1.,0.,1.],[0.,-.9,0.,0.]):
            self.assertFalse(self.prove(normal=normal))

    def test_does_not_authorize_air_teleport_or_horizontal_speed(self):
        for after in (dict(self.after,position=[1.6667,200.,0.]),
                      dict(self.after,position=[1.6667,60.,0.]),
                      dict(self.after,position=[4.,80.480316,0.]),
                      dict(self.after,velocity=[100.,1.,0.]),
                      dict(self.after,velocity=[101.,0.,0.])):
            self.assertFalse(self.prove(after=after))
        self.assertFalse(self.prove(before=dict(self.before,velocity=[100.,-10.,0.])))
        matrix=list(MATRIX);matrix[4]=.2
        self.assertFalse(self.prove(matrix=matrix))

    def test_ordinary_frames_never_query_and_first_seed_is_used(self):
        tracker=m.AirCorrection.__new__(m.AirCorrection)
        tracker.previous=copy.deepcopy(self.before)
        tracker.previous_matrix=list(MATRIX)
        with patch.object(tracker,'_query',return_value=True) as query:
            self.assertTrue(tracker.update(dict(sample=self.after,actor_matrix=MATRIX)))
            query.assert_called_once()
            query.reset_mock()
            ordinary=sample(20,position=[3.333333,80.2928,0.],velocity=[100.,-22.5,0.])
            self.assertFalse(tracker.update(dict(sample=ordinary,actor_matrix=MATRIX)))
            query.assert_not_called()

    def test_original_wallride_ceiling_release_requires_normal_and_query(self):
        before=sample(41,state=2,position=[58.055118560791016,201.51052856445312,-68.82060241699219],
                      velocity=[0.,33.97579574584961,-601.7534790039062])
        after=sample(42,position=[22.055118560791016,151.34646606445312,-78.8498306274414],
                     velocity=[0.,0.,-601.8199462890625])
        wall=list(MATRIX);wall[4:7]=[-1.,0.,0.]
        point=[40.055118560791016,228.34646606445312,-78.8498306274414,1.]
        def proves(a=before,b=after,n=wall,p=point,hit=True):
            return m.proves_ceiling(a,b,MATRIX,77.,hit,p,self.normal,n)
        self.assertTrue(proves())
        self.assertEqual(m.wall_snap(before,after,wall),36.)
        self.assertFalse(proves(hit=False))
        self.assertFalse(proves(p=[*after['position'],1.]))
        # All other support states, including a walking hang, remain excluded.
        for state in (0,1,3,4,5,6,7):
            self.assertFalse(proves(a=dict(before,state=state)))
        self.assertFalse(proves(a=dict(before,mode=1,walk_state=3)))
        self.assertFalse(proves(a=dict(before,bailing=True)))
        self.assertFalse(proves(a=dict(before,frame=40)))
        self.assertFalse(proves(n=MATRIX))
        inverse=list(wall);inverse[4]=1.
        self.assertFalse(proves(n=inverse))
        self.assertFalse(proves(b=dict(after,position=[21.8,after['position'][1],after['position'][2]])))
        # Single 18-inch release is covered, but the query must then be at root XZ.
        single=dict(after,position=[40.055118560791016,*after['position'][1:]])
        self.assertEqual(m.wall_snap(before,single,wall),18.)
        self.assertTrue(proves(b=single))

    def test_captured_wall_release_projection_residuals_keep_original_velocity(self):
        def source(values,scale):
            return [values[0]*scale,values[2]*scale,-values[1]*scale]
        wall=[0.,-1.,0.,0.,1.,0.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.]
        for session,frame,bp,bv,ap,av in WALL_RELEASES:
            with self.subTest(session=session):
                before=sample(frame,state=2,position=source(bp,1/.0254),velocity=source(bv,50/.0254))
                after=sample(frame+1,position=source(ap,1/.0254),velocity=source(av,50/.0254))
                original=copy.deepcopy(after)
                self.assertGreater(after['velocity'][1],0)
                self.assertTrue(m.candidate(before,after,MATRIX,wall))
                self.assertEqual(m.wall_snap(before,after,wall),36.)
                point=m.query_position(before,after,wall);point[1]+=77.;point.append(1.)
                self.assertTrue(m.proves_ceiling(before,after,MATRIX,77.,True,point,self.normal,wall))
                self.assertFalse(m.proves_ceiling(before,after,MATRIX,77.,False,point,self.normal,wall))
                self.assertFalse(m.proves_ceiling(before,after,MATRIX,77.,True,point,[0.,1.,0.,0.],wall))
                for vertical in (-after['velocity'][1],0.):
                    changed=copy.deepcopy(after);changed['velocity'][1]=vertical
                    self.assertTrue(m.candidate(before,changed,MATRIX,wall))
                for vertical in (m.MAX_CEILING_VERTICAL_SPEED*1.01,-m.MAX_CEILING_VERTICAL_SPEED*1.01,
                                 .01,-.01,22.5,-22.5):
                    changed=copy.deepcopy(after);changed['velocity'][1]=vertical
                    self.assertFalse(m.candidate(before,changed,MATRIX,wall))
                self.assertEqual(after,original)

    def test_air_residual_tolerance_does_not_accept_actual_falling(self):
        for vertical in (3.0517578125e-5,-6.103515625e-5):
            changed=dict(self.after,velocity=[100.,vertical,0.])
            self.assertTrue(self.prove(after=changed))
        for vertical in (.001,-.001,22.5,-22.5):
            self.assertFalse(self.prove(after=dict(self.after,velocity=[100.,vertical,0.])))

    def test_glue_uses_original_getter_and_balances_x87_and_arguments(self):
        code=m.height_stub(0x120000,0x140000,0x160000)
        self.assertIn(b'\x68'+struct.pack('<I',0x120000)+b'\x6a\x01\x68'+struct.pack('<I',0x542cf0c7),code)
        self.assertIn(b'\xb8'+struct.pack('<I',0x58ef60)+b'\xff\xd0\x83\xc4\x0c',code)
        self.assertIn(b'\xd9\x1d'+struct.pack('<I',0x160000),code)
        for pointer in (0,0x120001,0x100000000):
            with self.assertRaises(RuntimeError): m.height_stub(pointer,0x140000,0x160000)

    def slope_fixture(self):
        # Actual unchanged GTA roof + stock solver, stock-spine-41.jsonl.
        before=sample(18,position=[108340.90625,1145.6148681640625,56152.25390625],
                      velocity=[.021702807396650314,0.,-81.4764175415039])
        after=sample(19,position=[108340.90625,1086.7061767578125,56136.52734375],
                     velocity=[.021702807396650314,17.539339065551758,-71.67909240722656])
        point=[108340.90625,1160.1903076171875,56150.89453125,1.]
        normal=[0.,-.9713433980941772,-.23768043518066406,0.]
        return before,after,point,normal

    def test_original_real_roof_slope_projection_and_clearance(self):
        before,after,point,normal=self.slope_fixture()
        def prove(**kwargs):
            values=dict(previous=before,current=after,matrix=MATRIX,height=77.,gravity=-1350.,
                        hit=True,point=point,normal=normal,blocked=False)
            values.update(kwargs)
            return m.proves_slope(**values)
        self.assertTrue(prove())
        self.assertFalse(m.candidate(before,after,MATRIX)) # Flat marker stays strict.
        for changes in (dict(hit=False),dict(blocked=True),dict(height=70.),dict(gravity=-900.),
                        dict(normal=[0.,1.,0.,0.]),dict(normal=[0.,-1.,0.,0.]),
                        dict(point=[point[0]+.2,*point[1:]]),dict(gravity=math.nan)):
            self.assertFalse(prove(**changes))
        for changes in (dict(frame=20),dict(state=0),dict(mode=1),dict(bailing=True),
                        dict(board_visible=False),dict(position=[108340.7,*after['position'][1:]]),
                        dict(velocity=[.0217028,0.,-71.67909])):
            self.assertFalse(prove(current=dict(after,**changes)))
        self.assertFalse(prove(previous=dict(before,state=2)))
        self.assertFalse(prove(previous=dict(before,bailing=True)))

    def test_slope_marker_is_per_completed_frame_and_query_only_on_candidate(self):
        before,after,_,_=self.slope_fixture()
        tracker=m.AirCorrection.__new__(m.AirCorrection)
        tracker.previous=before;tracker.previous_matrix=list(MATRIX)
        with patch.object(tracker,'_query',return_value=True) as query:
            self.assertTrue(tracker.update(dict(sample=after,actor_matrix=MATRIX)))
            self.assertTrue(tracker.last_sloped)
            query.assert_called_once_with(before,dict(sample=after,actor_matrix=MATRIX),sloped=True)
            ordinary=dict(after,frame=20,position=[after['position'][0],after['position'][1],after['position'][2]-1])
            self.assertFalse(tracker.update(dict(sample=ordinary,actor_matrix=MATRIX)))
            self.assertFalse(tracker.last_sloped)
            self.assertEqual(query.call_count,1)

    def test_actual_bail_air_ceiling_keeps_exact_projection_and_owned_board(self):
        # Real GTA COL, independent native rays and one whole original AirUpdate:
        # stock-air-bail-ceiling-01 reproduces frame1480 position/velocity exactly.
        board=0x884ef81b
        before=sample(1479,position=[88238.5546875,603.6300048828125,62591.3984375],
            velocity=[26.081867218017578,-356.87762451171875,-369.68743896484375],
            bailing=True,board_visible=False,detached_board_id=board)
        after=sample(1480,position=[88240.390625,536.8733520507812,62582.984375],
            velocity=[17.02544403076172,13.603790283203125,-355.0651550292969],
            bailing=True,board_visible=False,detached_board_id=board)
        matrix=[-.9991562962532043,-.014746252447366714,-.03832967206835747,0.,
            -.016276579350233078,.9990701675415039,.03992481529712677,0.,
            .03770529106259346,.040515005588531494,-.9984672665596008,0.,0.,0.,0.,1.]
        point=[88238.7265625,613.79833984375,62585.890625,1.]
        normal=[.02302338182926178,-.9990435242652893,-.03717300668358803,0.]
        original=copy.deepcopy((before,after,matrix))
        def prove(**changes):
            values=dict(previous=before,current=after,matrix=matrix,height=77.,gravity=-1350.,
                        hit=True,point=point,normal=normal,blocked=False,bail_board=board)
            values.update(changes)
            return m.proves_slope(**values)
        self.assertTrue(prove())
        self.assertFalse(m.candidate(before,after,matrix)) # Flat path stays non-bail.
        for changes in (dict(bail_board=None),dict(bail_board=board+1),dict(hit=False),dict(blocked=True),
                        dict(height=70.),dict(gravity=-900.),dict(normal=[0.,-1.,0.,0.]),
                        dict(point=[point[0]+.2,*point[1:]])):
            with self.subTest(changes=changes): self.assertFalse(prove(**changes))
        for which,base in (('previous',before),('current',after)):
            for changes in (dict(bailing=False),dict(board_visible=True),dict(detached_board_id=board+1),
                            dict(detached_board_id=None),dict(mode=1),dict(state=0),dict(walking_gate=1),
                            dict(frame=base['frame']+1)):
                with self.subTest(which=which,changes=changes):
                    self.assertFalse(prove(**{which:dict(base,**changes)}))
        self.assertFalse(prove(current=dict(after,velocity=[17.025444,0.,-355.065155])))
        self.assertFalse(prove(current=dict(after,position=[after['position'][0]+.2,*after['position'][1:]])))
        self.assertEqual((before,after,matrix),original)
        tracker=m.AirCorrection.__new__(m.AirCorrection)
        tracker.previous=before;tracker.previous_matrix=matrix;tracker.bail_board=board
        with patch.object(tracker,'_query',return_value=True) as query:
            result=dict(sample=after,actor_matrix=matrix)
            self.assertTrue(tracker.update(result))
            self.assertTrue(tracker.last_sloped)
            query.assert_called_once_with(before,result,sloped=True)

    def test_shallow_slope_gets_query_after_flat_normal_rejection(self):
        before=sample(position=[0.,100.,0.],velocity=[0.,10.,0.])
        start,incoming=m.ballistic(before,-1350.)
        normal=[.0002,-math.sqrt(1-.0002**2),0.,0.];point=[0.,100.,0.,1.]
        end=[start[0],start[1]+60.,start[2]]
        amount=sum((point[i]-end[i])*normal[i] for i in range(3))
        dot=sum(incoming[i]*normal[i] for i in range(3))
        after=sample(19,position=[start[i]+amount*normal[i] for i in range(3)],
                     velocity=[incoming[i]-dot*normal[i] for i in range(3)])
        self.assertTrue(m.candidate(before,after,MATRIX))
        self.assertFalse(m.proves_ceiling(before,after,MATRIX,60.,True,point,normal))
        self.assertTrue(m.proves_slope(before,after,MATRIX,60.,-1350.,True,point,normal,False))
        tracker=m.AirCorrection.__new__(m.AirCorrection)
        tracker.previous=before;tracker.previous_matrix=list(MATRIX)
        with patch.object(tracker,'_query',side_effect=[False,True]) as query:
            self.assertTrue(tracker.update(dict(sample=after,actor_matrix=MATRIX)))
            self.assertTrue(tracker.last_sloped)
            self.assertEqual(query.call_count,2)

    def test_unproved_jump_retains_bounded_source_diagnostics(self):
        before,after,_,_=self.slope_fixture()
        after=dict(after,bailing=True)
        tracker=m.AirCorrection.__new__(m.AirCorrection)
        tracker.previous=before;tracker.previous_matrix=list(MATRIX);tracker.diagnostics=0
        with patch.object(tracker,'_snapshot',return_value=(b'frame',b'motion',b'state',b'core')) as snapshot:
            tracker.log=lambda phase,**data: self.assertEqual((phase,data['core_hex']),('source_large_displacement',b'core'.hex()))
            self.assertFalse(tracker.update(dict(sample=after,actor_matrix=MATRIX)))
            snapshot.assert_called_once_with(after)
            self.assertEqual(tracker.diagnostics,1)
            tracker.diagnostics=8;tracker.previous=before
            self.assertFalse(tracker.update(dict(sample=after,actor_matrix=MATRIX)))
            self.assertEqual(snapshot.call_count,1)

    def test_extra_diagnostics_only_runs_for_bounded_unproved_jump(self):
        from unittest.mock import Mock
        before,after,_,_=self.slope_fixture()
        after=dict(after,bailing=True)
        tracker=m.AirCorrection.__new__(m.AirCorrection)
        tracker.previous=before;tracker.previous_matrix=list(MATRIX);tracker.diagnostics=0
        tracker.extra_diagnostics=Mock(return_value={'vehicle_sequence':123})
        tracker.log=Mock()
        tracker._snapshot=Mock(return_value=(b'frame',b'motion',b'state',b'core'))
        near=dict(before,frame=before['frame']+1,position=[v+.1 for v in before['position']])
        tracker.update(dict(sample=near,actor_matrix=MATRIX,visual_matrix=MATRIX,palette=b'previous palette'))
        tracker.extra_diagnostics.assert_not_called()
        tracker.previous=before
        tracker.update(dict(sample=after,actor_matrix=MATRIX,visual_matrix=MATRIX,palette=b'current palette'))
        tracker.extra_diagnostics.assert_called_once_with()
        data=tracker.log.call_args.kwargs
        self.assertEqual(data['extra'],{'vehicle_sequence':123})
        self.assertEqual(data['previous_palette_hex'],b'previous palette'.hex())
        self.assertEqual(data['palette_hex'],b'current palette'.hex())
        self.assertEqual(data['previous_visual_matrix'],MATRIX)
        tracker.diagnostics=8;tracker.previous=before
        tracker.update(dict(sample=after,actor_matrix=MATRIX))
        self.assertEqual(tracker.extra_diagnostics.call_count,1)
        tracker.diagnostics=0;tracker.previous=before
        tracker.extra_diagnostics.side_effect=RuntimeError('Contact outside owned arena')
        self.assertFalse(tracker.update(dict(sample=after,actor_matrix=MATRIX)))
        self.assertEqual(tracker.log.call_args.kwargs['extra'],
                         {'capture_error':'Contact outside owned arena'})

    def test_ground_trace_is_only_read_for_bounded_rare_diagnostics(self):
        from unittest.mock import Mock
        before,after,_,_=self.slope_fixture()
        after=dict(after,bailing=True)
        tracker=m.AirCorrection.__new__(m.AirCorrection)
        tracker.previous=before;tracker.previous_matrix=list(MATRIX);tracker.diagnostics=0
        tracker.ground_trace=Mock()
        tracker.ground_trace.snapshot.return_value={'frames':[before['frame'],after['frame']]}
        tracker._snapshot=Mock(return_value=(b'frame',b'motion',b'state',b'core'))
        tracker.log=Mock()
        near=dict(before,frame=before['frame']+1,position=[v+.1 for v in before['position']])
        tracker.update(dict(sample=near,actor_matrix=MATRIX))
        tracker.ground_trace.snapshot.assert_not_called()
        tracker.previous=before
        self.assertFalse(tracker.update(dict(sample=after,actor_matrix=MATRIX)))
        self.assertEqual(tracker.log.call_args.kwargs['extra']['ground_trace'],
                         {'frames':[before['frame'],after['frame']]})
        tracker.bail_diagnostics=4;tracker.previous=before
        tracker.update(dict(sample=after,actor_matrix=MATRIX))
        self.assertEqual(tracker.ground_trace.snapshot.call_count,1)
        tracker.bail_diagnostics=0;tracker.previous=before
        tracker.ground_trace.snapshot.side_effect=RuntimeError('Trace callsite differs')
        self.assertFalse(tracker.update(dict(sample=after,actor_matrix=MATRIX)))
        self.assertEqual(tracker.log.call_args.kwargs['extra'],
                         {'ground_trace_error':'Trace callsite differs'})

    def test_opt_in_bail_keeps_four_captures_including_first_air_bail_after_other_eight(self):
        from unittest.mock import Mock
        before,after,_,_=self.slope_fixture()
        before=dict(before,bailing=False);after=dict(after,bailing=True)
        tracker=m.AirCorrection.__new__(m.AirCorrection)
        tracker.previous_matrix=list(MATRIX);tracker.ground_trace=Mock()
        tracker._snapshot=Mock(return_value=(b'frame',b'motion',b'state',b'core'))
        tracker.log=Mock()
        def feed(old,new):
            tracker.previous=old
            self.assertFalse(tracker.update(dict(sample=new,actor_matrix=MATRIX)))
        for _ in range(9):feed(dict(before,state=0),dict(after,state=0,bailing=False))
        self.assertEqual(tracker.log.call_count,8)
        # First airborne bail, continued Ground bail, Air->Ground, and Rail bail.
        for old_state,new_state,old_bail in ((1,1,False),(0,0,True),(1,0,True),(4,4,False),(0,0,True)):
            feed(dict(before,state=old_state,bailing=old_bail),dict(after,state=new_state))
        self.assertEqual(tracker.log.call_count,12)
        self.assertEqual(tracker.ground_trace.snapshot.call_count,12)
        self.assertEqual((tracker.diagnostics,tracker.bail_diagnostics),(8,4))

    def test_default_bail_has_no_extra_budget_and_foreign_context_cannot_use_it(self):
        from unittest.mock import Mock
        before,after,_,_=self.slope_fixture()
        before=dict(before,bailing=True);after=dict(after,state=0,bailing=True)
        tracker=m.AirCorrection.__new__(m.AirCorrection)
        tracker.previous_matrix=list(MATRIX);tracker.ground_trace=None
        tracker._snapshot=Mock(return_value=(b'frame',b'motion',b'state',b'core'))
        tracker.log=Mock()
        for _ in range(12):
            tracker.previous=before
            self.assertFalse(tracker.update(dict(sample=after,actor_matrix=MATRIX)))
        self.assertEqual(tracker.log.call_count,8)
        self.assertEqual(getattr(tracker,'bail_diagnostics',0),0)
        tracker.ground_trace=Mock()
        for old,new in ((dict(before,mode=1),after),(before,dict(after,mode=1)),
                        (before,dict(after,bailing=False))):
            tracker.previous=old
            self.assertFalse(tracker.update(dict(sample=new,actor_matrix=MATRIX)))
        self.assertEqual(tracker.log.call_count,8)
        tracker.ground_trace.snapshot.assert_not_called()

    def test_query_snapshot_rejects_foreign_actor_before_native_calls(self):
        from unittest.mock import Mock
        tracker=m.AirCorrection.__new__(m.AirCorrection)
        tracker.context=dict(actor=0x10000,components={0x5bd63b29:0x20000,0x43686585:0x30000})
        words={0x10000:0x6989e8,0x20004:0x5bd63b29,0x2000c:0x10000,
               0x30004:0x43686585,0x3000c:0x10000}
        system=Mock(word=lambda address:words[address]);tracker.system=system
        before,_,_,_=self.slope_fixture()
        words[0x2000c]=0x40000
        with self.assertRaisesRegex(RuntimeError,'identity changed'): tracker._snapshot(before)
        system.read.assert_not_called();system.write.assert_not_called()
        system.worker.execute.assert_not_called()

    def test_query_snapshot_rejects_changed_actor_board_identity(self):
        from unittest.mock import Mock
        tracker=m.AirCorrection.__new__(m.AirCorrection)
        tracker.context=dict(actor=0x10000,components={0x5bd63b29:0x20000,0x43686585:0x30000})
        tracker.bail_board=0x884ef81b
        words={0x10000:0x6989e8,0x10018:1,0x20004:0x5bd63b29,0x2000c:0x10000,
               0x30004:0x43686585,0x3000c:0x10000}
        system=Mock(word=lambda address:words[address]);tracker.system=system
        with self.assertRaisesRegex(RuntimeError,'board identity changed'):
            tracker._snapshot(self.before)
        system.read.assert_not_called();system.write.assert_not_called()
        system.worker.execute.assert_not_called()


if __name__=='__main__': unittest.main()
