"""Pure toggle/retention checks: no game, native calls or synthetic input."""
from types import SimpleNamespace
from contextlib import ExitStack, nullcontext, contextmanager
from pathlib import Path
import copy
import json
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

import live_session as live
from live_session import f7_start_requested, entry_input_ready, check_retained_source
from inspect_rig import snapshot_stamp
from test_preview_pose import fixture as pose_fixture


class LiveF7Tests(unittest.TestCase):
    def setUp(self):
        self.state = dict(pid=1, creation_100ns=2, ped_address=3, epoch=4, area=0, ped_area=0,
            age_ms=1, flags=1 | 8 | 16 | 128, grounded=True, ped_motion_flags=1, control_eligible=True)
        self.driver = SimpleNamespace(started=True, poisoned=False, _stepping=False)
        self.worker = SimpleNamespace(command_failed=False)
        self.scene = SimpleNamespace(poisoned=False)

    def test_release_captures_trace_even_when_adapter_status_is_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            observer = SimpleNamespace(exe=Path(folder)/'gta_sa.exe')
            ack = Mock(read=Mock(return_value={'active': False}))
            trace = Mock(snapshot=Mock(return_value={'frames': [31, 32]}))
            evidence = live.native_release_evidence(observer, self.state, ack,
                driver=SimpleNamespace(ground_trace=trace))
            self.assertEqual(evidence['source_trace'], {'frames': [31, 32]})
            self.assertEqual(evidence['ack'], {'active': False})
            self.assertIn('adapter_error', evidence)
            trace.snapshot.assert_called_once_with()

    def test_release_trace_failure_preserves_cleanup_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            observer = SimpleNamespace(exe=Path(folder)/'gta_sa.exe')
            ack = Mock(read=Mock(side_effect=OSError('ack unavailable')))
            trace = Mock(snapshot=Mock(side_effect=RuntimeError('trace ownership changed')))
            evidence = live.native_release_evidence(observer, self.state, ack,
                driver=SimpleNamespace(ground_trace=trace))
            self.assertEqual(evidence['source_trace_error'], 'trace ownership changed')
            self.assertEqual(evidence['ack_error'], 'ack unavailable')
            self.assertIs(evidence['state'], self.state)

    def test_release_without_trace_needs_no_source_access(self):
        with tempfile.TemporaryDirectory() as folder:
            observer = SimpleNamespace(exe=Path(folder)/'gta_sa.exe')
            ack = Mock(read=Mock(return_value={'active': False}))
            for driver in (None, SimpleNamespace(ground_trace=None), SimpleNamespace()):
                evidence = live.native_release_evidence(observer, self.state, ack, driver=driver)
                self.assertNotIn('source_trace', evidence)
                self.assertNotIn('source_trace_error', evidence)

    def test_bootstrap_counter_preserves_one_press_and_cancels_two(self):
        baseline = 12  # Captured by the supervisor before spawning/loading.
        self.assertFalse(f7_start_requested(baseline, 12))
        self.assertTrue(f7_start_requested(baseline, 13))
        self.assertFalse(f7_start_requested(baseline, 14))
        self.assertTrue(f7_start_requested(baseline, 15))
        self.assertTrue(f7_start_requested(0xffffffff, 0))
        self.assertFalse(f7_start_requested(0xffffffff, 1))

    def test_fast_off_on_retains_the_second_request(self):
        baseline = 0xfffffffe
        self.assertTrue(f7_start_requested(baseline, 0xffffffff))
        self.assertFalse(f7_start_requested(baseline, 0))
        self.assertTrue(f7_start_requested(baseline, 1))

    def test_focus_or_numlock_wait_keeps_the_pending_start(self):
        before, current = 2, 3
        for flag in (8, 128):
            self.state['flags'] &= ~flag
            self.assertTrue(f7_start_requested(before, current))
            self.assertFalse(entry_input_ready(self.state, {}))
            self.state['flags'] |= flag
            self.assertTrue(entry_input_ready(self.state, {}))
        self.assertFalse(f7_start_requested(before, current+1))

    def test_first_entry_needs_ground_and_air_reentry_needs_same_calibration(self):
        self.state.update(grounded=False, ped_motion_flags=2)
        self.assertFalse(entry_input_ready(self.state, {}))
        calibration = dict(identity=tuple(snapshot_stamp(self.state).values()), offset=1.)
        self.assertTrue(entry_input_ready(self.state, calibration))
        self.state['epoch'] += 1
        self.assertFalse(entry_input_ready(self.state, calibration))

    def test_normal_focus_or_lease_loss_retains_only_a_healthy_matching_source(self):
        target = snapshot_stamp(self.state)
        self.state['flags'] &= ~8
        check_retained_source(self.state, target, self.driver, self.worker, self.scene)
        for owner, field in ((self.driver, 'poisoned'), (self.driver, '_stepping'),
                             (self.worker, 'command_failed'), (self.scene, 'poisoned')):
            setattr(owner, field, True)
            with self.assertRaisesRegex(RuntimeError, 'failed original'):
                check_retained_source(self.state, target, self.driver, self.worker, self.scene)
            setattr(owner, field, False)
        check_retained_source(dict(self.state, age_ms=60000), target, self.driver, self.worker, self.scene)
        for changed in (dict(self.state, epoch=5), dict(self.state, flags=32), dict(self.state, age_ms=-1)):
            with self.assertRaisesRegex(RuntimeError, 'scene change or read fault'):
                check_retained_source(changed, target, self.driver, self.worker, self.scene)

    def test_control_epoch_retains_only_independently_verified_same_rig_and_timeline(self):
        prepared = dict(target=dict(scene=snapshot_stamp(self.state), game_timer=20, frame_counter=5))
        changed = dict(self.state, epoch=5, game_timer=21, frame_counter=6)
        memory, calibration = Mock(), dict(offset=1.)
        observer = SimpleNamespace(read=lambda: dict(changed))
        with patch.object(live.pose, 'live_identity') as verify:
            self.assertEqual(live.refresh_retained_epoch(observer, memory, prepared, calibration, changed), changed)
            verify.assert_called_once_with(memory, prepared)
        self.assertEqual(prepared['target']['scene'], snapshot_stamp(changed))
        self.assertEqual(calibration, {})
        for state in (dict(changed, ped_address=4), dict(changed, game_timer=0),
                      dict(changed, area=1, ped_area=1), dict(changed, flags=32, epoch=6)):
            with self.assertRaisesRegex(RuntimeError, 'Cannot retain'):
                live.refresh_retained_epoch(observer, memory, prepared, calibration, state)

    def run_recovery(self, *, source_error=False, rapid_toggle=False, exercise_upkeep=False,
                     world_buffer=False, epoch_change=False, actor_change=False, timeline_change=False,
                     menu_pause=False, space_scenario=None, motion_context=None, adjustment_frame=None,
                     slow_scale=None, timed_world_change=False, cancel_adjustment=False, post_ack_pause=False,
                     upkeep_ages=(25,25)):
        """Exercise the actual continuous run loop without game/process/native calls."""
        with tempfile.TemporaryDirectory() as folder, ExitStack() as patches:
            root = Path(folder)
            cache = root / 'thaw_gta'
            (cache / 'stock-assets').mkdir(parents=True)
            (cache / 'base-copy.json').write_text(json.dumps(dict(
                format='gta-thaw-isolated-copy-v1', game=str(root), source=str(root / 'original'))))
            (cache / 'stock-assets/rig_thps7_human.json').write_text('{}')
            scene = root / 'scene.COL'
            scene.write_bytes(b'col')
            scene.with_suffix('.json').write_text('{}')
            state = dict(self.state, protocol='GTATHAW2', sequence=5, f7_requests=3,
                         game_timer=100, frame_counter=20, clock_hour=13,
                         position=[0., 0., 1.], heading=0., raw_gta_velocity=[0., 0., 0.])
            model = dict(phase='idle', attempts=0, commits=0, focus_reads=0,
                         upkeep_scopes=0, upkeep_active=False, callback=None,
                         applied_sequence=2, pose_packets=[], move_packets=[])
            model['menu_frames'] = 0
            model['moves_while_geometry_pending'] = []
            model['keyboard_contexts'], model['recenters'], model['source_packets'] = [], [], []
            model['vehicle_polls'], model['vehicle_samples'], model['lifecycle'] = [], [], []
            model['adjustment_samples'] = []
            model['adjustment_order'] = []
            model['post_ack_paused'] = False
            model['post_ack_confirmed'] = False
            model['correction_acks'] = []
            model['native_alive'] = False
            model['source_times'] = []
            model['accepted_source'] = (0, 0)
            model['tick_ms'] = 1000
            audio_mix = Mock(close=Mock(return_value=[]))
            effect = (dict(sfx_volume=.25, time_scale=slow_scale, event_sequence=0, event_kind=0)
                      if slow_scale is not None else None)
            effects = Mock(available=slow_scale is not None, current=Mock(return_value=effect))

            def read():
                if model['phase'] == 'focus_wait':
                    model['focus_reads'] += 1
                    if model['focus_reads'] >= 3:
                        state['flags'] |= 8
                        model['phase'] = 'idle'
                if model['phase'] == 'active' and model['commits'] == 2:
                    state['f7_requests'] += 1
                    state['flags'] &= ~64
                    model['phase'] = 'off'
                return copy.deepcopy(state)

            observer = SimpleNamespace(exe=root / 'gta_sa.exe', api=SimpleNamespace(GetTickCount64=lambda: model['tick_ms']),
                                       read=read, close=Mock())
            driver = SimpleNamespace(started=True, poisoned=False, _stepping=False, frame_count=0,
                                     initial_pose={'sample': {}}, last=None, audio=SimpleNamespace(stop_all=Mock()),
                                     summary=Mock(return_value={}), step=Mock(), body_witness=Mock())
            if space_scenario is not None:
                driver.initial_pose['sample'] = dict(mode=space_scenario['mode'],
                    walking_gate=space_scenario.get('initial_gate',0))
            @contextmanager
            def waiting_callback(callback):
                self.assertFalse(model['upkeep_active'])
                self.assertTrue(callable(callback))
                model['upkeep_scopes'] += 1
                model['upkeep_active'] = True
                model['callback'] = callback
                try:
                    yield
                finally:
                    model['upkeep_active'] = False
                    model['callback'] = None

            worker = SimpleNamespace(command_failed=False, operation_budget=lambda *a, **k: nullcontext(),
                                     waiting_callback=waiting_callback)
            api = SimpleNamespace(_thaw_native_worker=worker)

            def begin(*a, **k):
                model['attempts'] += 1
                if model['attempts'] == 1:
                    raise live.moving_entry.EntryUnavailable('GTA did not acknowledge the entry preparation')
                return dict(captured=copy.deepcopy(state), identity=tuple(snapshot_stamp(state).values()),
                            request=1000, contact=live.movement.GROUND, f7_requests=state['f7_requests'])

            def commit(*a, **k):
                model['commits'] += 1
                model['phase'] = 'active'
                state['flags'] |= 64
                state['game_timer'] += 10
                state['frame_counter'] += 5
                return dict(sequence=6)

            def keyboard_read(*, skating=False):
                if model['phase'] == 'active':
                    if world_buffer:
                        from input_mapping import map_actions
                        if space_scenario is not None:
                            model['keyboard_contexts'].append(skating)
                            actions = ({'Grind'} if space_scenario.get('press_after_step') and driver.frame_count==0
                                       else {'CameraRecenter'})
                            if space_scenario.get('release_after_first') and len(model['keyboard_contexts'])>1:
                                actions=set()
                            if space_scenario.get('short_pulse'):
                                actions = {'Grab', 'Backward'} if len(model['keyboard_contexts']) == 2 else set()
                            return map_actions(actions,skating=skating)
                        return map_actions({'Grind'} if driver.frame_count == 0 else {'Ollie'})
                    if exercise_upkeep:
                        for elapsed in upkeep_ages:
                            model['tick_ms'] += elapsed
                            model['callback']()
                    state['flags'] &= ~64
                    if rapid_toggle:
                        state['f7_requests'] += 2  # Off/on while the active loop is between observations.
                        model['phase'] = 'idle'
                    else:
                        state['flags'] &= ~8
                        model['phase'] = 'focus_wait'
                    raise live.InputUnavailable('GTA lost keyboard focus')
                return SimpleNamespace(packet=bytes(48), recenter_camera=False, skitch_requested=False)

            rig, report = pose_fixture()
            prepared = live.pose.prepare(rig, json.dumps(report).encode(), 0)
            prepared['target']['scene'] = snapshot_stamp(state)
            prepared['target'].update(game_timer=state['game_timer'], frame_counter=state['frame_counter'])
            converter = Mock()
            from settings_menu import default_settings
            def menu_poll():
                if post_ack_pause and model['post_ack_paused']:
                    state['flags'] &= ~256  # F8 closed before the next loop observes its menu.
                if menu_pause and model['phase']=='active' and model['menu_frames']<3:
                    model['menu_frames']+=1
                    return True
                return False
            menu = Mock(settings=default_settings(), revision=1, poll=menu_poll)
            options = Mock(set_aerial_flips=lambda _: dict(deferred=False),
                           map_input=Mock(side_effect=lambda packet, sample: packet))
            vehicle_world = SimpleNamespace(poisoned=False)
            def vehicle_poll(observed, *, active=False):
                self.assertTrue(model['native_alive'])
                model['vehicle_polls'].append((active, driver.frame_count, model.get('active_updates')))
                # Pending optional vehicle shapes must not stop a source frame.
                return dict(ready=False, pending_shapes=1)
            def vehicle_after_step(sample):
                self.assertTrue(model['native_alive'])
                model['vehicle_samples'].append(sample['frame'])
                return dict(contact='skitch', vehicle_id=100+sample['frame'])
            def vehicle_park():
                self.assertTrue(model['native_alive'])
                model['lifecycle'].append('vehicle_park')
            def vehicle_close():
                self.assertTrue(model['native_alive'])
                model['lifecycle'].append('vehicle_close')
            vehicles = Mock(world=vehicle_world, poll=vehicle_poll, after_step=vehicle_after_step,
                            park=vehicle_park, close=vehicle_close)
            def adjustment_update(result):
                self.assertTrue(model['native_alive'])
                model['adjustment_samples'].append(result['sample']['frame'])
                return result['sample']['frame'] == adjustment_frame
            adjustment = Mock(update=adjustment_update)
            replacements = ((live.pose, 'Observer', observer), (live.inspect_rig, 'Memory', Mock()),
                (live.inspect_rig, 'inspect', dict(accepted=True)), (live, 'GameplayPose', converter),
                (live.probe_gta_scene, 'prepare_inputs', dict(coordinates=dict(origin_gta=[0., 0., 0.]))),
                (live, 'KeyboardInput', SimpleNamespace(read=keyboard_read)),
                (live.pose, 'Publisher', Mock()), (live.probe_frames, 'initialize', driver),
                (live.pose, 'validate_scene', None),
                (live, 'HudSampler', Mock()), (live, 'prepare_board', prepared),
                (live, 'SettingsMenu', menu),
                (live, 'AttackPublisher', Mock()),
                (live, 'EffectsInput', effects), (live, 'AudioMix', audio_mix),
                (live.probe_trick_settings, 'read_labels', {}),
                (live.probe_trick_settings, 'prepare', Mock(menu_catalogue={})),
                (live, 'GameplayOptions', options),
                (live, 'VehicleWorld', vehicle_world), (live, 'VehicleRuntime', vehicles),
                (live, 'SourceAdjustment', adjustment))
            for owner, name, result in replacements:
                patches.enter_context(patch.object(owner, name, return_value=result))
            for name in ('probe_particles.run',):
                patches.enter_context(patch(name))
            def recenter(*a,**k):
                model['recenters'].append((driver.frame_count,model.get('active_updates')))
            patches.enter_context(patch.object(live.probe_camera_recenter,'run',side_effect=recenter))
            if world_buffer:
                from dataclasses import replace
                from world_stream import Ticket, WorldStream
                inputs = dict(coordinates=dict(origin_gta=[0., 0., 0.]), col_sha256='old-col')
                ticket = Ticket('old', tuple(snapshot_stamp(state).values()), 13, (), 'activation',
                                (0., 0., 0.), (0., 0., 0.), 64., 16.)
                world = WorldStream.__new__(WorldStream)
                world.radius, world.timed_collision_instances = 64., []
                if timed_world_change:
                    world.timed_collision_instances = [(0, '', False,
                        (-4., -4., 0.), (4., 4., 8.), (14, 20))]
                streamer = SimpleNamespace(initial=lambda: inputs, current_inputs=inputs,
                    current_ticket=ticket, world=world, native=self.scene, close=Mock(), bind_native=Mock())
                model['active_updates'] = 0
                def stream_update(*a, **k):
                    ready = True
                    if k.get('active'):
                        model['active_updates'] += 1
                        pre_step_buffer = space_scenario is not None and space_scenario.get('pre_step_buffer')
                        if pre_step_buffer and model['active_updates'] == 1:
                            ready = False
                        elif model['active_updates'] == 2 and not pre_step_buffer:
                            if epoch_change:
                                streamer.current_ticket = replace(ticket, scene=tuple(snapshot_stamp(state).values()))
                            else:
                                ready = False  # The post-step hour changed while a neighbor prepares.
                        elif model['active_updates'] == 3:
                            streamer.current_ticket = replace(ticket, key='new', hour=14, center=(32., 0., 0.))
                            streamer.current_inputs = dict(inputs, col_sha256='new-col')
                    if not ready:
                        model['moves_while_geometry_pending'].append(tuple(model['move_packets']))
                    return dict(ready=ready,current_key=streamer.current_ticket.key,
                                reason='geometry_wait',pending_key='new',stage='compiling')
                streamer.update = stream_update
                patches.enter_context(patch.object(live, 'StreamedScene', return_value=streamer))
                patches.enter_context(patch.object(live.probe_scene, 'run', return_value={}))
                patches.enter_context(patch.object(live.probe_gta_scene, 'run', return_value={}))
                patches.enter_context(patch('probe_gta_rails.read_global_data', return_value={}))
                patches.enter_context(patch.object(live.pose, 'live_identity'))
                patches.enter_context(patch.object(live, 'HudSampler', return_value=Mock(read=lambda: {})))
                def step(packet):
                    if post_ack_pause and driver.frame_count == 1:
                        self.assertTrue(model['post_ack_confirmed'])
                    driver.frame_count += 1
                    model['source_times'].append(live.time.monotonic())
                    model['source_packet'] = packet
                    model['source_packets'].append(packet)
                    if epoch_change:
                        # Capture observed a changed control epoch before the
                        # native movement owner next clears its active bit.
                        state['epoch'] += 1
                        if actor_change:
                            state['ped_address'] += 1
                        if timeline_change:
                            state['game_timer'] -= 1  # Still above entry, below last observed active tick.
                        model['phase'] = 'idle'
                    result = dict(sample=dict(frame=driver.frame_count, position=[0., 0., 0.], velocity=[0., 0., 0.],
                        state=0, mode=space_scenario['mode'] if space_scenario else 0,
                        walk_state=0, walking_gate=0, bailing=False, board_attack=False,
                        board_visible=True, palette_sha256='source-palette'),
                        palette=b'original-palette', visual_matrix=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.],
                        actor_matrix=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.])
                    if motion_context is not None:
                        result['sample'].update(motion_context)
                    if space_scenario is not None:
                        driver.last = result
                    return result
                driver.step.side_effect = step
            def ack_read():
                return dict(protocol='GTAMACK1', age_ms=1, active=bool(state['flags'] & 64),
                    trajectory=True, preparing=False, entry_f7_requests=state['f7_requests'],
                    f7_requests=state['f7_requests'], request=1000,
                    applied_sequence=model['applied_sequence'], source_tick=model['accepted_source'][0],
                    source_time_us=model['accepted_source'][1],
                    collision_generation=1, **snapshot_stamp(state))
            def wait_for_correction(request, sequence, command, **kwargs):
                model['correction_acks'].append((driver.frame_count, request, sequence, dict(command)))
                model['adjustment_order'].append(('ack', command['source_tick']))
                if cancel_adjustment and command['source_tick'] == 0:
                    model['phase'] = 'off'
                    state['f7_requests'] += 1
                    state['flags'] &= ~64
                    raise ValueError('J changed during predecessor acknowledgement')
                if post_ack_pause and command['source_tick'] == 1:
                    if not model['post_ack_paused']:
                        model['post_ack_paused'] = True
                        state['flags'] |= 256
                        raise ValueError('F8 opened before adjustment acknowledgement')
                    model['post_ack_confirmed'] = True
            patches.enter_context(patch('movement_ack.MovementAck',
                return_value=Mock(read=ack_read, wait_for=wait_for_correction)))
            def record_move(raw, sequence):
                model['move_packets'].append(raw)
                model['applied_sequence'] = sequence
                model['accepted_source'] = live.movement.BODY.unpack(raw)[-7:-5]
                model['adjustment_order'].append(('publish', model['accepted_source'][0]))
                stop_tick = space_scenario.get('stop_after',1) if space_scenario else 1
                if world_buffer and live.movement.BODY.unpack(raw)[-7] == stop_tick:
                    model['phase'] = 'off'
                    state['f7_requests'] += 1
                    state['flags'] &= ~64
            def record_pose(raw, sequence):
                model['pose_packets'].append(raw)
            pose_publisher, mover = Mock(publish=record_pose), Mock(publish=record_move)
            if epoch_change:
                mover.clear.side_effect = lambda: state.update(flags=state['flags'] & ~64)
            patches.enter_context(patch.object(live.pose, 'Publisher', side_effect=[pose_publisher, mover]))
            patches.enter_context(patch.object(live.time, 'sleep'))
            if slow_scale is not None:
                clock = [100.]
                patches.enter_context(patch.object(live.time, 'monotonic', side_effect=lambda: clock[0]))
                patches.enter_context(patch.object(live.time, 'sleep',
                    side_effect=lambda seconds: clock.__setitem__(0, clock[0]+max(0.,seconds))))
            begin_mock = patches.enter_context(patch.object(live.moving_entry, 'begin', side_effect=begin))
            patches.enter_context(patch.object(live.moving_entry, 'commit', side_effect=commit))
            handoff = patches.enter_context(patch.object(live, 'align_entry',
                side_effect=RuntimeError('Original source failed') if source_error else
                    lambda *a, **k: (a[10]['captured'], a[10]['identity'], 1.)))
            def probe(*a, **k):
                model['native_alive'] = True
                model['lifecycle'].append('native_started')
                try:
                    if world_buffer:
                        live.probe_scene.run(api, None, None, None, Mock(), {})
                    live.probe_frames.run(api, None, None, None, Mock(), {}, {}, {}, Mock())
                finally:
                    model['native_alive'] = False
                    model['lifecycle'].append('native_destroyed')
            patches.enter_context(patch.object(live.host, 'probe', side_effect=probe))
            args = SimpleNamespace(pid=1, world_stream=world_buffer, continuous=True, f7_baseline=2,
                stop_file=SimpleNamespace(exists=lambda: model['phase'] == 'off'), scene=scene,
                camera=False, hud=False, report=root / 'test.jsonl', wait=30, seconds=5, sessions=1)
            if source_error or actor_change or timeline_change:
                with self.assertRaisesRegex(RuntimeError,
                        'GTA timeline changed' if timeline_change else
                        'player, area or timeline changed' if actor_change else 'Original source failed'):
                    live.run(args)
            else:
                live.run(args)
            if world_buffer:
                model['frames'] = json.loads(args.report.with_suffix('.frames.json').read_text())
                model['converted'] = [call.args for call in converter.evaluate.call_args_list]
            model['input_steps'] = options.map_input.call_count
            model['audio_steps'] = audio_mix.after_frame.call_count
            model['audio_updates'] = [call.args for call in audio_mix.update.call_args_list]
            audio_mix.close.assert_called_once()
            effects.close.assert_called_once()
            self.assertFalse(model['upkeep_active'])
            self.assertEqual(model['lifecycle'].count('vehicle_close'), 1)
            self.assertEqual(model['lifecycle'][-2:], ['vehicle_close', 'native_destroyed'])
            return model, begin_mock.call_count, handoff.call_count, driver

    def test_slow_motion_keeps_polling_and_transport_without_extra_source_or_audio_frames(self):
        model, _, _, driver = self.run_recovery(world_buffer=True, slow_scale=.3,
            space_scenario=dict(mode=0, stop_after=12, pre_step_buffer=True))
        self.assertEqual(driver.frame_count, 12)
        self.assertEqual(model['audio_steps'], driver.frame_count)
        self.assertGreater(len(model['keyboard_contexts']), driver.frame_count*2)
        self.assertGreater(len(model['move_packets']), driver.frame_count*2)
        self.assertGreater(model['source_times'][-1]-model['source_times'][0], .55)
        self.assertTrue(all(args == (.25, .3) for args in model['audio_updates']))
        ticks = [live.movement.BODY.unpack(raw)[-7] for raw in model['move_packets']]
        self.assertEqual(sorted(set(ticks)), list(range(13)))
        repeated = [live.movement.BODY.unpack(raw) for raw, before, tick in
                    zip(model['move_packets'][1:], ticks, ticks[1:]) if tick == before and tick > 0]
        self.assertTrue(repeated)
        self.assertTrue(all(not row[-8] & live.movement.BUFFERING_CONTACT_FLAG for row in repeated))

    def test_slow_motion_delivers_short_trick_press_and_release_between_source_frames(self):
        model, _, _, _ = self.run_recovery(world_buffer=True, slow_scale=.3,
            space_scenario=dict(mode=0, stop_after=8, short_pulse=True))
        packets = model['source_packets']
        presses = [i for i, packet in enumerate(packets) if struct.unpack_from('<I', packet, 0x24)[0] & 0x20]
        self.assertEqual(len(presses), 1)
        index = presses[0]
        self.assertEqual(packets[index][3], 255)  # The original backflip direction accompanies the press.
        self.assertEqual(struct.unpack_from('<I', packets[index+1], 0x24)[0], 0)

    def test_rejected_entry_and_focus_loss_retry_without_consuming_f7_or_restarting_source(self):
        model, begins, handoffs, driver = self.run_recovery()
        self.assertEqual((begins, handoffs, model['commits']), (3, 2, 2))
        self.assertEqual(model['phase'], 'off')
        self.assertEqual(model['focus_reads'], 3)
        self.assertEqual(model['upkeep_scopes'], 2)
        driver.step.assert_not_called()

    def test_rapid_off_on_during_input_loss_reenters_then_explicit_off_stays_off(self):
        model, begins, handoffs, driver = self.run_recovery(rapid_toggle=True)
        self.assertEqual((begins, handoffs, model['commits']), (3, 2, 2))
        self.assertEqual(model['phase'], 'off')
        self.assertEqual(model['focus_reads'], 0)

    def test_native_wait_upkeep_keeps_pose_and_move_on_the_same_accepted_clock(self):
        model, _, _, driver = self.run_recovery(exercise_upkeep=True)
        self.assertEqual(len(model['pose_packets']), 2)
        self.assertEqual(len(model['move_packets']), 2)
        for move, pose in zip(model['move_packets'], model['pose_packets']):
            move_header = live.movement.BODY.unpack(move)
            pose_header = live.pose.HEADER.unpack_from(pose)
            request, tick, micros = live.pose.SOURCE_CLOCK.unpack_from(pose, 5008)
            self.assertEqual((request, tick, micros), (move_header[6], *move_header[-7:-5]))
            self.assertEqual((pose_header[6], pose_header[7]), (move_header[7], move_header[8]))
            self.assertEqual(struct.unpack_from('<I', move, 100)[0],
                             live.movement.GROUND | live.movement.BUFFERING_CONTACT_FLAG)
        self.assertEqual(model['pose_packets'][0][48:], model['pose_packets'][1][48:])
        driver.step.assert_not_called()

    def test_native_wait_does_not_republish_fresh_motion_or_postpone_needed_upkeep(self):
        model,_,_,driver=self.run_recovery(exercise_upkeep=True,upkeep_ages=(1,18,1,1,19))
        # Fresh normal/heartbeat publication is enough. Skipping a callback
        # does not move its publication clock; upkeep is due at20ms and40ms.
        self.assertEqual(len(model['move_packets']),2)
        self.assertEqual([live.movement.BODY.unpack(raw)[8] for raw in model['move_packets']], [1020,1040])
        self.assertEqual(len(model['pose_packets']),2)
        driver.step.assert_not_called()

    def test_original_engine_error_is_not_treated_as_retryable_entry_rejection(self):
        model, begins, handoffs, driver = self.run_recovery(source_error=True)
        self.assertEqual((begins, handoffs, model['commits']), (2, 1, 0))

    def test_pending_frame_survives_equivalent_hour_and_tile_change_without_resampling_input(self):
        model, _, _, driver = self.run_recovery(world_buffer=True)
        driver.step.assert_called_once()
        self.assertEqual(model['active_updates'], 4)
        self.assertEqual(len(model['frames']), 1)
        frame = model['frames'][0]
        self.assertEqual((frame['frame'], frame['native_frame'], frame['digital_mask']), (1, 1, 16))
        self.assertEqual(struct.unpack_from('<I', model['source_packet'], 0x24)[0], 16)
        self.assertEqual(model['converted'], [(b'original-palette', 'source-palette')])
        self.assertEqual(live.movement.BODY.unpack(model['move_packets'][-1])[-7:-5], (1, 16667))
        self.assertEqual(live.pose.SOURCE_CLOCK.unpack_from(model['pose_packets'][-1], 5008), (1000, 1, 16667))

    def test_pending_completed_frame_survives_real_timed_collision_change_without_second_step(self):
        model, _, _, driver = self.run_recovery(world_buffer=True, timed_world_change=True)
        driver.step.assert_called_once()
        self.assertEqual(model['active_updates'], 4)
        self.assertEqual(model['input_steps'], 1)
        self.assertEqual(model['audio_steps'], 1)
        self.assertEqual(model['vehicle_samples'], [1])
        self.assertEqual(len(model['frames']), 1)
        self.assertEqual(model['frames'][0]['native_frame'], 1)
        self.assertEqual(live.movement.BODY.unpack(model['move_packets'][-1])[-7:-5], (1, 16667))

    def test_pending_geometry_reuses_same_vehicle_contact_and_never_polls_ahead_of_source(self):
        model, _, _, driver = self.run_recovery(world_buffer=True,
            space_scenario=dict(mode=1, press_after_step=True, stop_after=2))
        self.assertEqual(driver.step.call_count, 2)
        self.assertEqual(model['vehicle_samples'], [1, 2])
        self.assertEqual(model['adjustment_samples'], [1, 2])
        self.assertEqual([row for row in model['vehicle_polls'] if row[0]],
                         [(True, 0, 1), (True, 1, 5)])
        self.assertTrue(any(not row[0] for row in model['vehicle_polls']))
        self.assertEqual([frame['vehicle_contact'] for frame in model['frames']],
                         [dict(contact='skitch', vehicle_id=101), dict(contact='skitch', vehicle_id=102)])

    def test_menu_holds_same_source_clock_without_consuming_aerial_gesture_or_mode_request(self):
        model, _, _, driver = self.run_recovery(world_buffer=True, menu_pause=True)
        self.assertEqual(model['menu_frames'], 3)
        self.assertEqual(model['commits'], 1)
        self.assertEqual(model['input_steps'], 1)
        driver.step.assert_called_once()
        buffered = [live.movement.BODY.unpack(raw) for raw in model['move_packets']]
        self.assertEqual([row[-7:-5] for row in buffered[:3]], [(0, 0)]*3)
        self.assertEqual(buffered[-1][-7:-5], (1, 16667))

    def test_source_adjustment_survives_pending_geometry_with_both_acknowledgements(self):
        model, _, _, driver = self.run_recovery(world_buffer=True,
            motion_context=dict(state=1), adjustment_frame=1)
        driver.step.assert_called_once()
        self.assertEqual(model['adjustment_samples'], [1])
        for pending in model['moves_while_geometry_pending']:
            for raw in pending:
                self.assertFalse(struct.unpack_from('<I', raw, 100)[0] &
                                 live.movement.SOURCE_ADJUSTMENT_FLAG)
        self.assertEqual(struct.unpack_from('<I', model['move_packets'][-1], 100)[0],
                         live.movement.AIR | live.movement.SOURCE_ADJUSTMENT_FLAG)
        self.assertEqual(len(model['correction_acks']), 2)
        native_frame, request, sequence, command = model['correction_acks'][-1]
        self.assertEqual((native_frame, request, sequence), (1, 1000, model['applied_sequence']))
        self.assertEqual((command['source_tick'], command['source_time_us']), (1, 16667))
        self.assertIs(command['source_adjustment'], True)
        self.assertEqual(model['adjustment_order'][-3:], [('ack', 0), ('publish', 1), ('ack', 1)])

    def test_source_adjustment_keeps_completed_bail_and_walking_context(self):
        for context, contact in ((dict(state=0,bailing=True),live.movement.GROUND),
                                 (dict(state=1,bailing=True),live.movement.AIR),
                                 (dict(mode=1,walk_state=3),live.movement.CONSTRAINT)):
            with self.subTest(context=context):
                model,_,_,driver=self.run_recovery(world_buffer=True,
                    motion_context=context,adjustment_frame=1)
                driver.step.assert_called_once()
                self.assertEqual(model['adjustment_samples'],[1])
                self.assertEqual(struct.unpack_from('<I',model['move_packets'][-1],100)[0],
                    contact|live.movement.SOURCE_ADJUSTMENT_FLAG)
                self.assertEqual(model['adjustment_order'][-3:], [('ack',0),('publish',1),('ack',1)])

    def test_j_during_predecessor_ack_does_not_publish_adjustment_or_repeat_source(self):
        model,_,_,driver=self.run_recovery(world_buffer=True,
            motion_context=dict(state=1),adjustment_frame=1,cancel_adjustment=True)
        driver.step.assert_called_once()
        self.assertEqual(model['adjustment_samples'],[1])
        self.assertEqual(model['adjustment_order'][-1],('ack',0))
        self.assertTrue(all(live.movement.BODY.unpack(raw)[-7] == 0 for raw in model['move_packets']))

    def test_quick_f8_pause_after_publish_keeps_adjustment_until_ack_before_next_step(self):
        model,_,_,driver=self.run_recovery(world_buffer=True, adjustment_frame=1,post_ack_pause=True,
            space_scenario=dict(mode=0,stop_after=2))
        self.assertTrue(model['post_ack_confirmed'])
        self.assertEqual(driver.step.call_count,2)
        self.assertEqual(model['adjustment_samples'],[1,2])
        events=model['adjustment_order']
        first=events.index(('publish',1))
        self.assertEqual(events[first:first+4],[('publish',1),('ack',1),('ack',1),('publish',2)])

    def test_pending_geometry_cannot_publish_context_ahead_of_completed_source_clock(self):
        model,_,_,driver=self.run_recovery(world_buffer=True,
            motion_context=dict(mode=1,state=4,walk_state=3,bailing=True))
        driver.step.assert_called_once()
        samples=[live.movement.BODY.unpack(raw) for raw in model['move_packets']]
        self.assertEqual(model['active_updates'],4)
        self.assertTrue(model['moves_while_geometry_pending'])
        for pending in model['moves_while_geometry_pending']:
            for raw in pending:
                sample=live.movement.BODY.unpack(raw)
                self.assertEqual(sample[-7:-5],(0,0))
                self.assertEqual(sample[-4:],(0,0,0,0))
        self.assertEqual(samples[-1][-7:-5],(1,16667))
        self.assertEqual(samples[-1][-4:],(1,4,3,1))
        self.assertEqual(struct.unpack_from('<I',model['move_packets'][-1],100)[0],live.movement.CONSTRAINT)

    def test_skating_space_sends_original_transfer_chord_through_pending_frame(self):
        model,_,_,driver=self.run_recovery(world_buffer=True,space_scenario=dict(mode=0))
        driver.step.assert_called_once()
        self.assertEqual(model['keyboard_contexts'],[True,True])
        self.assertEqual(struct.unpack_from('<I',model['source_packet'],0x24)[0],0x0c)
        self.assertEqual(model['frames'][0]['digital_mask'],0x0c)
        self.assertEqual(model['recenters'],[])

    def test_walking_space_survives_geometry_wait_and_release_before_native_step(self):
        model,_,_,driver=self.run_recovery(world_buffer=True,
            space_scenario=dict(mode=1,pre_step_buffer=True,release_after_first=True))
        driver.step.assert_called_once()
        self.assertEqual(model['keyboard_contexts'],[False,False])
        self.assertEqual(model['recenters'],[(0,2)])
        self.assertEqual(struct.unpack_from('<I',model['source_packet'],0x24)[0],0)

    def test_walking_space_during_pending_sample_recenters_once_on_next_source_step(self):
        model,_,_,driver=self.run_recovery(world_buffer=True,
            space_scenario=dict(mode=1,press_after_step=True,stop_after=2))
        self.assertEqual(driver.step.call_count,2)
        self.assertEqual(model['keyboard_contexts'],[False,False,False])
        self.assertEqual(model['recenters'],[(1,5)])
        self.assertEqual([frame['digital_mask'] for frame in model['frames']],[16,0])

    def test_inhibited_native_skating_state_does_not_recenter_or_request_transfer(self):
        model,_,_,driver=self.run_recovery(world_buffer=True,
            space_scenario=dict(mode=0,initial_gate=1))
        driver.step.assert_called_once()
        self.assertEqual(model['keyboard_contexts'],[False,True])
        self.assertEqual(struct.unpack_from('<I',model['source_packet'],0x24)[0],0)
        self.assertEqual(model['recenters'],[])

    def test_control_epoch_during_source_sample_retains_engine_and_reenters_without_old_publication(self):
        model, begins, handoffs, driver = self.run_recovery(world_buffer=True, epoch_change=True)
        driver.step.assert_called_once()
        self.assertEqual((begins, handoffs, model['commits']), (3, 2, 2))
        self.assertEqual(model['active_updates'], 2)
        self.assertEqual(model['frames'], [])
        self.assertEqual(model['converted'], [])
        self.assertEqual(model['move_packets'], [])
        self.assertEqual(model['pose_packets'], [])

    def test_player_replacement_during_source_sample_still_rejects_retention(self):
        model, begins, handoffs, driver = self.run_recovery(world_buffer=True,
            epoch_change=True, actor_change=True)
        driver.step.assert_called_once()
        self.assertEqual(model['commits'], 1)
        self.assertEqual(model['frames'], [])
        self.assertEqual(model['move_packets'], [])
        self.assertEqual(model['pose_packets'], [])

    def test_control_epoch_cannot_retain_timeline_older_than_last_active_snapshot(self):
        model, _, _, driver = self.run_recovery(world_buffer=True,
            epoch_change=True, timeline_change=True)
        driver.step.assert_called_once()
        self.assertEqual(model['commits'], 1)
        self.assertEqual(model['frames'], [])
        self.assertEqual(model['move_packets'], [])
        self.assertEqual(model['pose_packets'], [])


if __name__ == '__main__':
    unittest.main()
