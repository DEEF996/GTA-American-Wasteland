"""F7 gameplay using the local stock engine and optionally streamed GTA collision."""
from pathlib import Path
from collections import deque
from contextlib import ExitStack
from itertools import count as sessions_forever
import argparse
import json
import math
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vanilla_thaw'))
import inspect_rig
import preview_pose as pose
import preview_gameplay_move as movement
import moving_entry
import board_preview as board
from gameplay_pose import GameplayPose
from geometry import gta_to_thaw, thaw_to_gta, gta_velocity_to_thaw, thaw_velocity_to_gta
from keyboard_input import KeyboardInput, InputUnavailable, validate_bindings
from settings_menu import SettingsMenu
from camera_preview import CameraPublisher
from aim_input import AimInput, matches as aim_matches
import walking_aim
from hud_preview import HudPublisher
from attack import AttackPublisher
from audio_focus import bind_audio_to_game
from audio_mix import AudioMix
from game_effects import EffectsInput, SourcePacer
from slow_motion_input import SlowMotionInput
from streamed_scene import StreamedScene
from vehicle_runtime import VehicleRuntime
from frame_timings import FrameTimings
from probe_vehicle_world import VehicleWorld
from source_adjustment import SourceAdjustment
import probe_loader as host
import probe_scene
import probe_gta_scene
import probe_frames
import probe_audio
import probe_handoff
import probe_switch_skating
import probe_skater
import probe_trick_settings
from probe_gameplay_options import GameplayOptions
import probe_camera_recenter
from hud_state import HudSampler
from motion_contact import classify as source_contact
from probe_camera_projection import NativeCameraProjection
from probe_hud_layout import NativeHudLayout
from movement_ack import matches as acknowledged_sample


def continuous_host_schedule(previous, now):
    # Recover at most one tick of ordinary jitter; long stalls never create a
    # backlog. Rebasing every small overrun permanently slowed the source clock.
    following = previous + 1/60
    return now if now-following > 1/60 else following


def f7_start_requested(before, current):
    host.require(all(type(value) is int and 0 <= value <= 0xffffffff for value in (before, current)),
                 'Invalid F7 counter')
    return bool(((current-before) & 0xffffffff) & 1)


def entry_input_ready(state, calibration):
    ready = (state['control_eligible'] and state['flags'] & (8 | 128) == (8 | 128)
             and not state['flags'] & (64 | 256) and 0 <= state['age_ms'] <= 100)
    grounded = state['grounded'] and state['ped_motion_flags'] == 1
    airborne = (not state['grounded'] and state['ped_motion_flags'] == 2 and
                calibration.get('identity') == tuple(inspect_rig.snapshot_stamp(state).values()))
    return bool(ready and (grounded or airborne))


def check_retained_source(state, target_scene, driver, worker, native_scene=None):
    host.require(inspect_rig.snapshot_stamp(state) == target_scene and
                 math.isfinite(state['age_ms']) and state['age_ms'] >= 0 and not state['flags'] & 32,
                 'Cannot retain source after GTA scene change or read fault')
    host.require(driver.started and not driver.poisoned and not driver._stepping and
                 not worker.command_failed and (native_scene is None or not native_scene.poisoned),
                 'Cannot retain failed original engine')


def refresh_retained_epoch(observer, memory, prepared, calibration, state):
    """Accept a control epoch only after independently rechecking the same CJ rig.

    Paused GTA can leave its last read-only snapshot unchanged for minutes.
    Keeping a suspended source during that pause publishes no gameplay data.
    Foreground/freshness checks still apply before every movement/pose packet.
    """
    stamp, previous = inspect_rig.snapshot_stamp(state), prepared['target']['scene']
    target = prepared['target']
    for field in ('game_timer', 'frame_counter'):
        observed = state.get(field, target.get(field, 0))
        host.require(observed >= target.get(field, observed),
                     'Cannot retain source after GTA timeline changed')
        target[field] = observed
    if stamp == previous:
        return state
    host.require(all(stamp[field] == previous[field] for field in stamp if field != 'epoch') and
                 stamp['epoch'] > previous['epoch'] and not state['flags'] & 32,
                 'Cannot retain source after GTA player, area or timeline changed')
    # Epoch increments also reflect temporary menu/cutscene control masks. It
    # does not imply different clump/skin/node ownership: verify those directly.
    memory.total = 0
    pose.live_identity(memory, prepared)
    latest = observer.read()
    host.require(inspect_rig.snapshot_stamp(latest) == stamp and
                 not latest['flags'] & 32 and
                 latest.get('game_timer', 0) >= state.get('game_timer', 0) and
                 latest.get('frame_counter', 0) >= state.get('frame_counter', 0),
                 'GTA changed during retained rig verification')
    previous.update(stamp)
    calibration.clear()
    return latest


def collision_selection_matches(streamer, source_ticket, source_col, *,
                                completed_position=None, current_ready=False):
    current = streamer.current_ticket
    if (source_ticket.scene != current.scene or source_ticket.origin != current.origin or
            (source_ticket.radius, source_ticket.margin) != (current.radius, current.margin)):
        return False
    if source_ticket.active_streams != current.active_streams:
        return False
    if source_col == streamer.current_inputs['col_sha256']:
        # IPL request/streaming diagnostics can change while the checked static
        # collision remains byte-identical. That does not invalidate this step.
        return True
    if source_ticket.activation_sha256 != current.activation_sha256:
        return False
    if source_ticket.hour == current.hour:
        return True
    # A spatial swap changes COL bytes even when the clock changed no geometry.
    visibility = streamer.world.visibility_identity
    args = source_ticket.center, source_ticket.scene[4], source_ticket.active_streams
    if visibility(*args, source_ticket.hour) == visibility(*args, current.hour):
        return True
    # A completed original step belongs to its start-of-frame clock selection.
    # An actual hour boundary may then replace timed collision while that one
    # result waits for coverage. Publish it once only after the current world is
    # ready; the next source step uses that world's ticket. IPL/scene changes
    # above remain incompatible, and neither tile may leave the solved root out
    # of its physical extent (the successor also needs its normal safety margin).
    if not current_ready or completed_position is None:
        return False
    return (source_ticket.covers(completed_position, margin=0.) and
            current.covers(completed_position))


def accepted_upkeep_snapshot(state, ack, identity, f7, request, sequence, command):
    """Only refresh an already accepted sample of the still-owned F7 session."""
    return (state['f7_requests'] == f7 and state['control_eligible'] and
            state['flags'] & (8 | 64 | 128) == (8 | 64 | 128) and not state['flags'] & 32 and
            0 <= state['age_ms'] <= 100 and
            movement.scene(state) == identity and
            acknowledged_sample(ack, state, request, sequence, command, 1))


def adapter_diagnostic_current(status, state, ack, file_age_ns):
    """A periodic status from before a release cannot explain that release."""
    return (status['epoch'] == state['epoch'] and
            status['f7_requests'] == state['f7_requests'] and
            0 <= state['sequence']-status['sequence'] <= 128 and
            0 <= file_age_ns <= 1500000000 and
            status['hold_active'] == bool(state['flags'] & 64) and
            ack is not None and status['hold_active'] == ack['active'] and
            status['hold_request'] == ack['request'])


def native_release_evidence(observer, state, acknowledgement, *, driver=None):
    """Read the adapter's own status file before transport cleanup overwrites it."""
    result = dict(state=state)
    try:
        result['ack'] = acknowledgement.read()
    except (ValueError, OSError) as error:
        result['ack_error'] = str(error)
    try:
        path = observer.exe.parent/'thaw_gta/adapter-status.json'
        before = path.stat()
        status = json.loads(path.read_text(encoding='utf-8-sig'))
        after = path.stat()
        created_ns = (observer.created-116444736000000000)*100
        host.require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and
                     status['pid'] == observer.pid and before.st_mtime_ns >= created_ns,
                     'Adapter diagnostic changed or belongs to an earlier GTA lifetime')
        result['adapter'] = {key: value for key, value in status.items() if
            key in ('pid', 'sequence', 'epoch', 'f7_requests', 'hold_active', 'hold_request',
                    'hold_last_reason', 'hold_last_gate', 'hold_last_packet_state',
                    'hold_last_ped_state', 'hold_last_task', 'hold_last_task_vtable', 'hold_last_task_slot',
                    'move_applied_sequence', 'trajectory_source_tick', 'trajectory_source_time_us',
                    'move_last_position', 'move_last_velocity', 'hold_release_velocity', 'hold_release_observed',
                    'hold_rejection',
                    'pose_preview_rejected', 'pose_busy_reads', 'pose_busy_fallbacks', 'pose_interpolated',
                    'pose_active_rejected', 'pose_current_root_rejected', 'pose_packet_root_rejected',
                    'pose_anchor_rejected', 'pose_source_samples', 'pose_source_gap_ms_max', 'pose_presentation_rebases',
                    'board_rejected', 'board_faults', 'board_last_error',
                    'trajectory_contact', 'camera_active', 'camera_request', 'camera_accepted',
                    'camera_last_reason', 'camera_conflicts', 'camera_ownership_mismatch',
                    'camera_expected_fov', 'camera_observed_fov', 'camera_expected_eye', 'camera_observed_eye')}
        result['adapter_current'] = adapter_diagnostic_current(status, state,
            result.get('ack'), time.time_ns()-before.st_mtime_ns)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        result['adapter_error'] = str(error)
    trace = getattr(driver, 'ground_trace', None)
    if trace is not None:
        # Capture the rejected frame before retention advances the two-frame ring.
        try:
            result['source_trace'] = trace.snapshot()
        except (OSError, ValueError, RuntimeError) as error:
            result['source_trace_error'] = str(error)
    return result


def prepare_board(cache, converter, source_bytes, target, target_bytes, result):
    # Build the immutable bundle before the first native model update; the
    # completed driver.step below supplies the actual presentation transform.
    prepared = converter.evaluate(result['palette'], result['sample']['palette_sha256'])
    models = struct.unpack('<512f', prepared['model_bytes'])
    record = dict(palette_bits=list(struct.unpack('<832I', result['palette'])),
                  palette_sha256=result['sample']['palette_sha256'], time=1/60, active_bones=52)
    prepared.update(frame_index=0, report=dict(nodes=prepared['hierarchy']['nodes'], frames=[dict(
        source_palette_sha256=record['palette_sha256'], time=1/60,
        model_row_major=[list(models[i:i+16]) for i in range(0, 512, 16)])]))
    assets = cache / 'stock-assets/board_default'
    raw = (assets / 'model.json').read_bytes()
    host.require(board.sha(raw).hex() == board.MODEL_SHA, 'Original board model differs')
    textures = {}
    for filename, digest in board.PNG_HASHES.items():
        host.require(board.sha((assets / filename).read_bytes()).hex() == digest,
                     'Original board texture differs: ' + filename)
        textures[filename] = board.read_export_png(assets / filename)
    bundle, _ = board.build(json.loads(raw), textures, json.loads(source_bytes), target,
                            record, prepared, board.sha(target_bytes))
    (cache / 'board-preview.bin').write_bytes(bundle)
    converter.set_board(bundle)
    return prepared


def align_entry(api, process, record, deadline, log, context, system, observer, world, preloaded, entry, calibration=None, *, streamed=False):
    moving_entry.check(observer.read(), entry)
    initial, identity = entry['captured'], entry['identity']
    origin = world['coordinates']['origin_gta']
    host.require(streamed or math.dist(initial['position'][:2], origin[:2]) < 96,
                 'CJ left the loaded development scene')
    top = gta_to_thaw(initial['position'], origin=origin)
    grounded=entry['contact']==movement.GROUND
    if grounded:
        bottom = [top[0], top[1]-2*world['coordinates']['thaw_units_per_gta_unit'], top[2]]
        # Rejected entries can retry indefinitely; reuse one owned query buffer.
        if 'gta_entry_feeler' not in context:
            context['gta_entry_feeler'] = system.allocate(bytes(0xc0))
        feeler = context['gta_entry_feeler']
        host.require(host.native_call(api, process, probe_scene.PINS['feeler'], [],
            record, deadline, log, feeler) == feeler, 'Original entry feeler constructor failed')
        host.remote_write(api, process, feeler, struct.pack('<8f', *top, 1., *bottom, 1.))
        hit = host.native_call(api, process, probe_scene.PINS['query'], [0, 0, 0],
                              record, deadline, log, feeler)
        contact = struct.unpack('<4f', system.read(feeler+0x34, 16))
        normal = struct.unpack('<4f', system.read(feeler+0x24, 16))
        host.require(all(math.isfinite(v) for v in contact+normal),
                     'Original entry query returned a nonfinite contact')
        # GTA already captured unambiguous standing contact. A hillside is
        # valid ground too; the old .95 normal gate allowed only slopes <18deg.
        ground_hit = (hit & 255 and contact[3] == 1 and normal[1] > 0 and
            abs(sum(v*v for v in normal[:3])-1) < .001 and
            all(abs(contact[axis]-top[axis]) <= probe_gta_scene.POINT_TOLERANCE for axis in (0, 2)))
        if not ground_hit:
            log('session_entry_ground_rejected', hit=hit, top=top, contact=contact, normal=normal)
        moving_entry.require_entry(ground_hit, 'F7 entry needs static ground below CJ')
        source = [contact[0], contact[1]+.001, contact[2]]
        offset = initial['position'][2]-thaw_to_gta(source, origin=origin)[2]
        moving_entry.require_entry(.5 < offset < 1.5, 'CJ ground offset differs')
    else:
        host.require(calibration is not None and calibration.get('identity')==identity,
                     'Air entry has no ground calibration for this actor/scene')
        offset=calibration['offset']
        host.require(math.isfinite(offset) and .5<offset<1.5, 'Invalid measured CJ ground offset')
        position=list(initial['position']);position[2]-=offset
        source=gta_to_thaw(position,origin=origin) # Preserve height; never snap an airborne actor to a feeler hit.
    yaw = math.degrees(math.remainder(initial['heading']+math.pi, math.tau))
    probe_switch_skating.run(api, process, record, deadline, log, context, system,
                             vm_sources=preloaded)
    velocity = gta_velocity_to_thaw(initial['raw_gta_velocity'])
    probe_handoff.run(api, process, record, deadline, log, context, system, source, velocity, yaw, grounded,
                      vm_sources=preloaded)
    current = observer.read()
    moving_entry.check(current, entry)
    log('session_entry', gta_entry=initial, source_position=source,
        source_velocity=velocity, source_yaw_degrees=yaw, root_ground_offset=offset,
        prepared_request=entry['request'], grounded=grounded)
    return initial, identity, offset


def run(args):
    def stopped():
        return args.stop_file is not None and args.stop_file.exists()

    if stopped():
        return
    observer = pose.Observer(args.pid)
    memory = publisher = mover = camera = hud_publisher = streamer = acknowledgement = rails = menu = attack = aim_input = vehicles = effects = audio_mix = None
    old_scene, old_audio, old_frames = probe_scene.run, probe_audio.run, probe_frames.run
    old_viewports = probe_skater.viewports
    display, ground_calibration, scene_ownership = {}, {}, {}
    try:
        cold_before = getattr(args, 'f7_baseline', None)
        if cold_before is None:
            cold_before = observer.read()['f7_requests']
        cache = observer.exe.parent / 'thaw_gta'
        host.require((cache / 'base-copy.json').is_file(), 'Isolated GTA copy required')
        memory = inspect_rig.Memory(observer)
        target = dict(format='gta-thaw-cj-rig-inspection-v1', read_only=True,
                      executable=str(observer.exe), **inspect_rig.inspect(observer, memory))
        host.require(target['accepted'], 'CJ rig is not compatible')
        target_bytes = json.dumps(target).encode()
        source_bytes = (cache / 'stock-assets/rig_thps7_human.json').read_bytes()
        converter = GameplayPose(source_bytes, target_bytes)
        if args.world_stream:
            from map_preparation import database_path
            streamer = StreamedScene(observer, database_path(observer.exe.parent))
            world = streamer.initial()
        else:
            world = probe_gta_scene.prepare_inputs(args.scene.read_bytes(),
                json.loads(args.scene.with_suffix('.json').read_bytes()))
        keyboard = KeyboardInput(observer)
        menu = SettingsMenu(observer, cache / 'settings.json')
        labels = probe_trick_settings.read_labels(cache / 'stock-common-audio-resources/manifest.json')
        publisher = pose.Publisher(observer)
        mover = pose.Publisher(observer, transport='MOVE_V3', packet_size=movement.BODY.size)
        attack = AttackPublisher(observer)
        effects = EffectsInput(observer)
        if args.world_stream or args.continuous:
            from movement_ack import MovementAck
            acknowledgement = MovementAck(observer)
        if args.camera:
            camera = CameraPublisher(observer)
            aim_input = AimInput(observer)
        if args.hud:
            hud_publisher = HudPublisher(observer, cache / 'stock-hud/manifest.json')

        def game_scene(api, process, record, deadline, log, inputs):
            previous = old_scene(api, process, record, deadline, log, inputs)
            current = probe_gta_scene.run(api, process, record, deadline, log, world, previous)
            scene_ownership.update(current=current)
            return current

        def game_audio(api, process, record, deadline, log, system):
            result = old_audio(api, process, record, deadline, log, system)
            display.update(bind_audio_to_game(system, result['device'], observer))
            log('audio_gta_cooperative_window', **display)
            return result

        def game_viewports(api, process, record, deadline, log, system):
            return old_viewports(api, process, record, deadline, log, system,
                display_window=display['window'], display_pid=observer.pid)

        def drive_frames(api, process, record, deadline, log, context, inputs, preloaded, system):
            nonlocal rails, vehicles, audio_mix
            driver = probe_frames.initialize(api, process, record, deadline, log,
                context, inputs, preloaded, system,
                max_frames=None if args.continuous else args.sessions*(math.ceil(args.seconds*60)+2))
            audio_mix = AudioMix(driver.audio)
            log('game_effects_transport', available=effects.available,
                slow_motion_seconds=5, minimum_time_scale=.3, mouse_uses_real_time=True)
            import probe_particles
            probe_particles.run(api, process, record, deadline, log, context, system, preloaded=preloaded)
            vehicle_world = VehicleWorld(api, process, record, deadline, log, system,
                context=context, vm_sources=preloaded)
            vehicles = VehicleRuntime(observer, vehicle_world, world['coordinates']['origin_gta'], log=log)
            if streamer:
                from probe_gta_rails import read_global_data
                streamer.bind_native(api, process, record, deadline, log, scene_ownership['current'],
                    context=context, global_data=read_global_data(cache / 'stock-common-audio-resources'))
                if 'rails' in streamer.current_inputs:
                    from probe_world_rails import RailStream
                    rails = RailStream(api, process, record, deadline, log, context, system,
                        scene_native=streamer.native, directory=cache / 'stock-common-audio-resources',
                        preloaded=preloaded, dynamic_rails=vehicle_world)
            hud_reader = HudSampler(api, process, context, system, vm_sources=preloaded)
            worker = api._thaw_native_worker
            with worker.operation_budget(None if args.continuous else
                    time.monotonic()+args.sessions*(args.wait+args.seconds)+5, command_timeout=.5):
                trick_settings = probe_trick_settings.prepare(api, process, record, deadline, log,
                    context, system, vm_sources=preloaded, labels=labels)
                options = GameplayOptions(api, process, record, deadline, log,
                    context, system, vm_sources=preloaded)
                menu.set_profile(trick_settings.menu_catalogue)
                settings_revision = 0

                def update_settings():
                    nonlocal settings_revision
                    opened = menu.poll()
                    if not opened and settings_revision != menu.revision:
                        value = menu.settings
                        keyboard.bindings = validate_bindings(value['bindings'])
                        trick_settings.apply(value['normal_tricks'], value['special_tricks'])
                        result = options.set_aerial_flips(value['aerial_flips'])
                        options.set_grind_camera_lowering(value['grind_camera_lowering'])
                        options.set_infinite_balance(value['infinite_balance'])
                        if not result.get('deferred', False):
                            settings_revision = menu.revision
                            log('settings_applied', revision=settings_revision, stats=[10]*10,
                                aerial_flips=value['aerial_flips'],
                                grind_camera_lowering=value['grind_camera_lowering'],
                                infinite_balance=value['infinite_balance'],
                                normal_overrides=len(value['normal_tricks']),
                                special_overrides=len(value['special_tricks']))
                    return opened

                update_settings()
                camera_reader = (NativeCameraProjection(api, process, record, deadline, log,
                    context, system, vm_sources=preloaded) if camera or hud_publisher else None)
                hud_layout = (NativeHudLayout(api, process, record, deadline, log,
                    context, system, vm_sources=preloaded) if hud_publisher else None)
                prepared = prepare_board(cache, converter, source_bytes, target, target_bytes, driver.initial_pose)
                if args.continuous:
                    state = refresh_retained_epoch(observer, memory, prepared, ground_calibration, observer.read())
                    check_retained_source(state, prepared['target']['scene'], driver, worker,
                                          streamer.native if streamer else None)
                    before = cold_before
                else:
                    pose.validate_scene(observer, observer.read(), prepared)
                    before = observer.read()['f7_requests']
                samples = deque(maxlen=600) if args.continuous else []
                for session in sessions_forever(1) if args.continuous else range(1, args.sessions+1):
                    if stopped():
                        break
                    before = play(session, before, driver, hud_reader, camera_reader, hud_layout, prepared,
                                  api, process, record, deadline, log, context, preloaded, system, samples,
                                  update_settings, options)

        def live_frames(api, process, record, deadline, log, context, inputs, preloaded, system):
            try:
                return drive_frames(api, process, record, deadline, log, context, inputs, preloaded, system)
            finally:
                # Native owners must be parked before host.probe destroys the
                # child process; compiler/mapping cleanup follows in all cases.
                failure = sys.exc_info()[1]
                if audio_mix is not None:
                    try:
                        cleanup_errors = audio_mix.close()
                        if cleanup_errors:
                            log('audio_mix_cleanup_errors', messages=cleanup_errors)
                    except BaseException as cleanup_error:
                        if failure is None:
                            raise
                        log('audio_mix_cleanup_error', message=str(cleanup_error), original_error=str(failure))
                if vehicles is not None:
                    try:
                        vehicles.close()
                    except BaseException as cleanup_error:
                        if failure is None:
                            raise
                        log('vehicles_cleanup_error', message=str(cleanup_error), original_error=str(failure))

        def play(session, before, driver, hud_reader, camera_reader, hud_layout, prepared,
                 api, process, record, deadline, log, context, preloaded, system, samples, update_settings,
                 gameplay_options):
            heartbeat_scope = None
            timings = FrameTimings(log, native_worker=api._thaw_native_worker)
            recent_commands = deque(maxlen=4)
            def retained(state):
                state = refresh_retained_epoch(observer, memory, prepared, ground_calibration, state)
                check_retained_source(state, prepared['target']['scene'], driver, api._thaw_native_worker,
                                      streamer.native if streamer else None)
                host.require(rails is None or not rails.poisoned, 'Cannot retain failed original rail service')
                return state

            def released(state):
                changed = inspect_rig.snapshot_stamp(state) != prepared['target']['scene']
                if args.continuous and (not state['flags'] & 64 or not state['flags'] & 8
                                        or not state['control_eligible'] or changed):
                    log('session_native_release', session=session,
                        recent_commands=list(recent_commands),
                        **native_release_evidence(observer, state, acknowledgement, driver=driver))
                    retained(state)
                    return ('control_epoch_changed' if changed else
                            'lease_released' if not state['flags'] & 64 else 'input_unavailable')
                if args.continuous:
                    # Keep the retention watermark at the last observed live
                    # tick, not merely at the beginning of a long ride.
                    refresh_retained_epoch(observer, memory, prepared, ground_calibration, state)
                return None

            try:
                args.report.with_suffix('.ready').write_text(f'session={session}; J: start; J: stop\n', encoding='utf-8')
                log('session_ready', session=session, wait_seconds=None if args.continuous else args.wait,
                    maximum_seconds=None if args.continuous else args.seconds,
                    scene='streamed_world' if streamer else str(args.scene), numlock_required=True)
                until = time.monotonic()+args.wait
                if args.continuous:
                    while True:
                        state = observer.read()
                        if stopped():
                            return before
                        if update_settings():
                            time.sleep(.01)
                            continue
                        state = retained(state)
                        if not entry_input_ready(state, ground_calibration):
                            # A minimized/menu game has no new snapshots. Keep
                            # its original engine idle and wait for fresh input.
                            vehicles.park()
                            time.sleep(.01)
                            continue
                        ready = streamer.update(source_frame=driver.frame_count)['ready'] if streamer else True
                        if ready and rails:
                            # A paused previous session can still report OnRail;
                            # native references protect it, not its stale sample.
                            ready = rails.update(streamer.current_inputs)['ready']
                        if ready:
                            vehicles.poll(state, active=False)
                        if f7_start_requested(before, state['f7_requests']) and ready and entry_input_ready(state, ground_calibration):
                            try:
                                keyboard.read()
                            except InputUnavailable:
                                pass  # Preserve the pending toggle across a focus/NumLock race.
                            else:
                                break
                        time.sleep(.01)
                while observer.read()['f7_requests'] == before:
                    if stopped():
                        return before
                    update_settings()
                    host.require(args.continuous or time.monotonic() < until, 'F7 wait timed out')
                    if streamer:
                        streamer.update(source_frame=driver.frame_count)
                    else:
                        vehicles.poll(observer.read(), active=False)
                    time.sleep(.01)
                if streamer:
                    # Prepare at the current CJ position before the finite entry capture.
                    requested = observer.read()['f7_requests']
                    while True:
                        ready = streamer.update(source_frame=driver.frame_count)['ready']
                        if ready and rails:
                            ready = rails.update(streamer.current_inputs)['ready']
                        if ready:
                            break
                        if stopped():
                            return before
                        if observer.read()['f7_requests'] != requested:
                            return before
                        time.sleep(.01)
                try:
                    keyboard.read()  # Refuse unsupported focus/Num Lock before ownership.
                except InputUnavailable:
                    if not args.continuous:
                        raise
                    retained(observer.read())
                    return before  # Keep the pending start for the next idle iteration.
                queued = observer.read()
                if args.continuous and not f7_start_requested(before, queued['f7_requests']):
                    return before
                duration = movement.CONTINUOUS_DURATION_MS if args.continuous else round(args.seconds*1000)
                entry = moving_entry.begin(observer, mover, duration, calibration=ground_calibration)
                if args.continuous and entry['f7_requests'] != queued['f7_requests']:
                    return before  # No source handoff has run; reassess the new toggle.
                if streamer and not streamer.update(entry['captured'],
                        source_frame=driver.frame_count)['ready']:
                    log('session_entry_deferred', reason='collision_changed_after_capture')
                    return before
                initial, identity, offset = align_entry(api, process, record, deadline, log,
                    context, system, observer, world, preloaded, entry, ground_calibration, streamed=bool(streamer))
                if streamer and not streamer.update(source_frame=driver.frame_count)['ready']:
                    log('session_entry_deferred', reason='collision_changed_during_handoff')
                    return before
                request = entry['request']
                acknowledged = moving_entry.commit(observer, mover, entry)
                if entry['contact']==movement.GROUND:
                    ground_calibration.update(identity=identity,offset=offset)
                log('session_accepted', request=request, acknowledged_sequence=acknowledged['sequence'])
                gameplay_options.reset_input()
                args.report.with_suffix('.accepted').write_text(str(session), encoding='utf-8')
                started = time.monotonic()
                scheduled = started - 1/60
                stop_reason = 'duration'
                count = 0
                host_frames, transport_sequence = 0, 2
                command = moving_entry.sample(entry['captured'], entry['contact'])
                last_motion_publish_ms = observer.api.GetTickCount64()
                recent_commands.append(dict(sequence=transport_sequence, **command))
                pending = source_camera = source_layout = None
                buffering = False
                buffer_started = None
                recenter_down = False
                recenter_pending = False
                menu_paused = False
                source_pacer = SourcePacer()
                slow_input = SlowMotionInput()
                origin = world['coordinates']['origin_gta']
                source_adjustment = SourceAdjustment(driver, context, origin=origin, offset=offset)
                awaiting_adjustment = False
                def confirm_adjustment():
                    nonlocal awaiting_adjustment
                    try:
                        acknowledgement.wait_for(request, transport_sequence, command, generation=1)
                    except (ValueError, TimeoutError):
                        current = observer.read()
                        if current['f7_requests'] != initial['f7_requests']:
                            return 'F7'
                        reason = released(current)
                        if reason:
                            return reason
                        if current['flags'] & 256:
                            return 'menu'
                        raise
                    awaiting_adjustment = False
                    log('source_adjustment_accepted', native_frame=driver.frame_count,
                        source_tick=command['source_tick'], previous_position=adjustment_previous['position'],
                        position=command['position'], mode=command['source_mode'], state=command['source_state'],
                        bailing=bool(command['source_bailing']))
                    return None

                def keep_accepted_state(*, report=True, buffered=True):
                    nonlocal transport_sequence, last_motion_publish_ms
                    if stopped():
                        return
                    try:
                        current = observer.read()
                        ack = acknowledgement.read()
                    except (OSError, ValueError):
                        return
                    if not accepted_upkeep_snapshot(current, ack, identity, initial['f7_requests'],
                                                    request, transport_sequence, command):
                        return
                    transport_sequence += 1
                    now_ms = observer.api.GetTickCount64()
                    mover.publish(movement.packet(current, identity, request, transport_sequence,
                        now_ms, duration, command, 1, interactive=True, buffered=buffered), transport_sequence)
                    last_motion_publish_ms = now_ms
                    publisher.publish(pose.packet(prepared, current, transport_sequence, now_ms,
                        request=request, source_tick=command['source_tick'],
                        source_time_us=command['source_time_us']), transport_sequence)
                    if camera and source_camera:
                        camera.publish(current, identity, request, now_ms, command['source_tick'],
                                       command['source_time_us'], source_camera, origin=origin)
                    if report:
                        log('native_wait_transport_upkeep', source_tick=command['source_tick'],
                            sequence=transport_sequence, accepted_sequence=ack['applied_sequence'])
                def keep_waiting_state():
                    # Normal 60 Hz publishing already renews this lease. Only
                    # upkeep a genuinely delayed native operation; rebuilding
                    # the same pose every 20 ms otherwise competes with gameplay.
                    age = observer.api.GetTickCount64() - last_motion_publish_ms
                    if not 0 <= age < 20:
                        keep_accepted_state()
                if args.continuous:
                    heartbeat_scope = ExitStack()
                    heartbeat_scope.enter_context(api._thaw_native_worker.waiting_callback(keep_waiting_state))
                while args.continuous or time.monotonic()-started < args.seconds-.1:
                    if stopped():
                        stop_reason = 'STOP'
                        break
                    timings.begin()
                    scheduled = (continuous_host_schedule(scheduled, time.monotonic())
                                 if args.continuous else started+host_frames/60)
                    remaining = scheduled-time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    timings.mark('sleep')
                    host_frames += 1
                    state = observer.read()
                    if state['f7_requests'] != initial['f7_requests']:
                        stop_reason = 'F7'
                        break
                    release_reason = released(state)
                    if release_reason:
                        stop_reason = release_reason
                        break
                    host.require(state['flags'] & 64, 'GTA released movement ownership')
                    pose.validate_scene(observer, state, prepared)
                    host.require(args.continuous or time.monotonic()-scheduled < .1,
                                 'Native simulation fell behind')
                    effect = effects.current(state, request)
                    time_scale = effect['time_scale'] if effect else 1.
                    if effect:
                        audio_mix.update(effect['sfx_volume'], time_scale,
                            event_sequence=effect['event_sequence'], event_kind=effect['event_kind'])
                    elif effects.available:
                        audio_mix.suspend()
                    else:
                        audio_mix.update(1., 1.)  # Backward-compatible with the installed pre-effects ASI.
                    if update_settings():
                        if not menu_paused:
                            driver.audio.stop_all()
                            attack.clear()
                            vehicles.park()
                            menu_paused = True
                        keep_accepted_state()
                        source_pacer.pause(time.monotonic())
                        slow_input.clear()
                        continue
                    menu_paused = False
                    if awaiting_adjustment:
                        # F8 can interrupt the post-publication ACK and close
                        # again before this loop. Never overwrite that sample.
                        reason = confirm_adjustment()
                        if reason == 'menu':
                            continue
                        if reason:
                            stop_reason = reason
                            break
                    previous_sample = (driver.last or driver.initial_pose)['sample']
                    try:
                        mapped = keyboard.read(skating=(previous_sample.get('mode') == 0 and
                                                        previous_sample.get('walking_gate') == 0))
                    except InputUnavailable:
                        if not args.continuous:
                            raise
                        retained(observer.read())
                        stop_reason = 'input_unavailable'
                        break
                    if mapped.recenter_camera and not recenter_down:
                        recenter_pending = True
                    recenter_down = mapped.recenter_camera
                    slow_input.observe(mapped, time.monotonic(), scale=time_scale,
                        context=(previous_sample.get('mode'), previous_sample.get('walking_gate')))
                    timings.mark('input')
                    stream = (streamer.update(state, position=pending['position'] if pending else command['position'],
                        active=True, source_frame=driver.frame_count) if streamer else dict(ready=True))
                    timings.mark('world')
                    if stream['ready'] and rails:
                        rail_status = rails.update(streamer.current_inputs,
                            pending['result']['sample'] if pending else
                            driver.last['sample'] if count and driver.last else None)
                        if not rail_status['ready']:
                            stream = dict(stream, ready=False, reason='original_rail_graph_preparing',
                                rail_stage=rail_status['stage'])
                        else:
                            # Prepare only the resource while its future COL is
                            # staged; the matching COL must precede rail parsing.
                            future = streamer.pending_result
                            rails.prewarm(future['inputs'] if future else None)
                    timings.mark('rails')
                    if not stream['ready']:
                        source_pacer.pause(time.monotonic())
                        slow_input.clear()
                        # Never mark a not-yet-consumed source sample as buffered.
                        if not buffering:
                            try:
                                acknowledgement.wait_for(request, transport_sequence, command, generation=1)
                            except (ValueError, TimeoutError):
                                if observer.read()['f7_requests'] != initial['f7_requests']:
                                    stop_reason = 'F7'
                                    break
                                release_reason = released(observer.read())
                                if release_reason:
                                    stop_reason = release_reason
                                    break
                                raise
                            buffering = True
                            buffer_started = time.monotonic()
                            log('world_buffer_begin', frame=count, reason=stream['reason'], position=command['position'],
                                current_key=stream['current_key'], pending_key=stream['pending_key'],
                                stage=stream['stage'], clock_hour=state['clock_hour'])
                        state = observer.read()
                        if state['f7_requests'] != initial['f7_requests']:
                            stop_reason = 'F7'
                            break
                        release_reason = released(state)
                        if release_reason:
                            stop_reason = release_reason
                            break
                        transport_sequence += 1
                        now_ms = observer.api.GetTickCount64()
                        mover.publish(movement.packet(state, identity, request, transport_sequence,
                            now_ms, duration, command, 1, interactive=True, buffered=True), transport_sequence)
                        last_motion_publish_ms = now_ms
                        publisher.publish(pose.packet(prepared, state, transport_sequence, now_ms,
                            request=request, source_tick=command['source_tick'],
                            source_time_us=command['source_time_us']), transport_sequence)
                        if camera and source_camera:
                            camera.publish(state, identity, request, now_ms, command['source_tick'],
                                command['source_time_us'], source_camera, origin=origin)
                        if hud_publisher and source_layout:
                            hud_publisher.publish(state, identity, request, observer.api.GetTickCount64(), count,
                                command['source_time_us'], source_layout, hud_layout.read)
                        continue
                    if buffering:
                        log('world_buffer_end', frame=count, position=command['position'],
                            seconds=time.monotonic()-buffer_started, current_key=stream['current_key'])
                        buffering = False
                    if pending is None:
                        if not source_pacer.due(time.monotonic(), time_scale):
                            keep_accepted_state(report=False, buffered=False)
                            continue
                        mapped = slow_input.consume(time.monotonic())
                        if recenter_pending:
                            if previous_sample.get('mode') == 1 and previous_sample.get('walking_gate') == 0:
                                probe_camera_recenter.run(api, process, record, deadline, log, context,
                                    system, vm_sources=preloaded)
                            recenter_pending = False
                        selection = streamer.current_ticket if streamer else None
                        source_col = streamer.current_inputs['col_sha256'] if streamer else None
                        previous_sample = (driver.last or driver.initial_pose)['sample']
                        if aim_input and walking_aim.walking(previous_sample):
                            try:
                                aim = aim_input.read()
                            except (OSError, ValueError):
                                aim = None  # An unavailable view does not end the owned THAW session.
                            if aim and aim_matches(aim, state, request):
                                original = camera_reader.reader.sample()
                                try:
                                    rotated = walking_aim.rotate_packet(
                                        mapped.packet, original['forward_thaw'], aim['forward_gta'])
                                except ValueError:
                                    pass  # A vertical original view has no horizontal walking basis.
                                else:
                                    mapped = mapped._replace(packet=rotated)
                        # Never mutate native car transforms while reusing a
                        # completed pending source sample. Shape preparation is
                        # asynchronous and its readiness does not gate skating.
                        timings.mark('mapping')
                        vehicles.poll(state, active=True)
                        vehicles.prepare_input(mapped.skitch_requested)
                        timings.mark('vehicles')
                        result = driver.step(gameplay_options.map_input(mapped.packet, previous_sample))
                        audio_mix.after_frame()
                        adjusted_source = source_adjustment.update(result)
                        timings.mark('source')
                        vehicle_contact = vehicles.after_step(result['sample'])
                        timings.mark('vehicle_contact')
                        hud = hud_reader.read()
                        timings.mark('hud_sample')
                        next_camera = camera_reader.publish() if camera_reader else None
                        timings.mark('camera_source')
                        next_layout = hud_layout.update() if hud_layout else None
                        timings.mark('hud_layout')
                    else:
                        result, hud = pending['result'], pending['hud']
                        next_camera, next_layout = pending['camera'], pending['layout']
                        selection, source_col = pending['selection'], pending['source_col']
                        mapped = pending['mapped']
                        vehicle_contact = pending['vehicle_contact']
                        adjusted_source = pending['adjusted_source']
                    sample = result['sample']
                    try:
                        contact = source_contact(sample)
                    except ValueError:
                        log('source_contact_rejected', sample=sample)
                        raise
                    position = thaw_to_gta(sample['position'], origin=origin)
                    position[2] += offset
                    host.require(streamer or math.dist(position[:2], origin[:2]) < 96,
                                 'Session reached the loaded scene boundary')
                    if streamer:
                        coverage = streamer.update(position=position, active=True, source_frame=driver.frame_count)
                        # GTA can revoke this session during a native step or
                        # tile staging. Retain only its independently verified
                        # rig; never publish the old sample under a new epoch.
                        state = observer.read()
                        if state['f7_requests'] != initial['f7_requests']:
                            stop_reason = 'F7'
                            break
                        release_reason = released(state)
                        if release_reason:
                            stop_reason = release_reason
                            break
                        if not coverage['ready']:
                            pending = dict(result=result, hud=hud, camera=next_camera, layout=next_layout,
                                position=position, selection=selection, source_col=source_col, mapped=mapped,
                                vehicle_contact=vehicle_contact, adjusted_source=adjusted_source)
                            continue
                        if not collision_selection_matches(streamer, selection, source_col,
                                completed_position=position, current_ready=coverage['ready']):
                            current = streamer.current_ticket
                            visibility = streamer.world.visibility_identity
                            visibility_args = selection.center, selection.scene[4], selection.active_streams
                            log('source_collision_selection_rejected', frame=sample['frame'],
                                source_ticket=vars(selection), current_ticket=vars(current),
                                source_col=source_col, current_col=streamer.current_inputs['col_sha256'],
                                source_visibility=visibility(*visibility_args, selection.hour),
                                current_visibility=visibility(*visibility_args, current.hour))
                            raise RuntimeError('World activation changed during the original physics sample; '
                                               'reacquire its current collision')
                        if selection.hour != streamer.current_ticket.hour:
                            log('source_collision_clock_boundary', frame=sample['frame'],
                                source_hour=selection.hour, current_hour=streamer.current_ticket.hour,
                                source_col=source_col, current_col=streamer.current_inputs['col_sha256'],
                                completed_position=position, current_world_ready=True,
                                original_step_repeated=False)
                    if adjusted_source:
                        # The bounded adjustment refers to the immediately prior
                        # source frame. A slow GTA render may not have consumed
                        # that normal packet yet; keep it published until ACK.
                        pending = dict(result=result, hud=hud, camera=next_camera, layout=next_layout,
                            position=position, selection=selection, source_col=source_col, mapped=mapped,
                            vehicle_contact=vehicle_contact, adjusted_source=adjusted_source)
                        adjustment_previous = command
                        try:
                            acknowledgement.wait_for(request, transport_sequence, command, generation=1)
                        except (ValueError, TimeoutError):
                            current = observer.read()
                            if current['f7_requests'] != initial['f7_requests']:
                                stop_reason = 'F7'
                                break
                            release_reason = released(current)
                            if release_reason:
                                stop_reason = release_reason
                                break
                            if current['flags'] & 256:
                                continue  # Retain this completed frame across F8.
                            raise
                    pending = None
                    source_camera, source_layout = next_camera, next_layout
                    count += 1
                    timings.mark('coverage')
                    try:
                        converter.evaluate(result['palette'], sample['palette_sha256'],
                            actor_matrix=result['actor_matrix'], actor_position=sample['position'],
                            visual_matrix=result['visual_matrix'], board_visible=sample['board_visible'],
                            board_pose=result.get('board_pose'), world_origin=origin)
                    except ValueError as error:
                        capture = args.report.with_suffix('.pose-failure.json')
                        try:
                            detached = result.get('board_pose')
                            if detached is not None:
                                detached = dict(detached, palette=detached['palette'].hex())
                            capture.write_text(json.dumps(dict(sample=sample, actor_matrix=result['actor_matrix'],
                                visual_matrix=result['visual_matrix'],
                                board_pose=detached, world_origin=origin,
                                palette_hex=result['palette'].hex(), message=str(error))), encoding='utf-8')
                        except OSError as capture_error:
                            log('source_pose_capture_error', message=str(capture_error))
                        log('source_pose_rejected', frame=sample['frame'], message=str(error), capture=str(capture))
                        raise
                    timings.mark('pose')
                    memory.total = 0
                    pose.live_identity(memory, prepared)
                    state = observer.read()
                    if state['f7_requests'] != initial['f7_requests']:
                        stop_reason = 'F7'
                        break
                    release_reason = released(state)
                    if release_reason:
                        stop_reason = release_reason
                        break
                    host.require(state['flags'] & 64, 'GTA released ownership during the native step')
                    timings.mark('identity')
                    command = dict(source_tick=count, source_time_us=round(count*1e6/60),
                        position=position, velocity=thaw_velocity_to_gta(sample['velocity']),
                        heading=movement.source_heading(dict(actor_matrix_bits=struct.unpack(
                            '<16I', struct.pack('<16f', *result['actor_matrix'])))),
                        contact=movement.source_contact_code(contact), source_adjustment=adjusted_source,
                        **movement.source_context(sample))
                    transport_sequence += 1
                    now_ms = observer.api.GetTickCount64()
                    mover.publish(movement.packet(state, identity, request, transport_sequence,
                        now_ms, duration, command, 1, interactive=True), transport_sequence)
                    last_motion_publish_ms = now_ms
                    publisher.publish(pose.packet(prepared, state, transport_sequence, now_ms,
                        request=request, source_tick=command['source_tick'],
                        source_time_us=command['source_time_us']), transport_sequence)
                    recent_commands.append(dict(sequence=transport_sequence, native_frame=sample['frame'], **command))
                    attack.publish(state, identity, request, command['source_tick'], command['source_time_us'],
                        sample['board_attack'], walking=sample['mode']==1 and sample['walking_gate']==0)
                    if camera:
                        camera.publish(state, identity, request, now_ms,
                            count, command['source_time_us'], source_camera, origin=origin)
                    hud_quads = (hud_publisher.publish(state, identity, request,
                        observer.api.GetTickCount64(), count, command['source_time_us'],
                        source_layout, hud_layout.read) if hud_publisher else 0)
                    timings.mark('publish')
                    samples.append(dict(session=session, frame=count, native_frame=sample['frame'], input=list(mapped.packet[:4]),
                        digital_mask=struct.unpack_from('<I', mapped.packet, 0x24)[0],
                        position=position, velocity=command['velocity'], state=sample['state'],
                        mode=sample['mode'], walk_state=sample['walk_state'], contact=contact,
                        hud=hud, hud_quads=hud_quads, vehicle_contact=vehicle_contact))
                    if adjusted_source:
                        # Retain this original adjustment until GTA consumes it.
                        awaiting_adjustment = True
                        reason = confirm_adjustment()
                        if reason == 'menu':
                            continue
                        if reason:
                            stop_reason = reason
                            break
                # F7 releases immediately; never keep publishing after a stop.
                timings.flush()
                if stop_reason != 'F7':
                    time.sleep(1/60)
                log('session_released', session=session, reason=stop_reason, frames=count,
                    recent_commands=list(recent_commands),
                    seconds=time.monotonic()-started,
                    native_acceptance=native_release_evidence(observer, state, acknowledgement),
                    driver=driver.summary())
                # Keep the supervisor's initial parity across temporary lease/input loss.
                # Every F7 edge is reconsidered by the idle gate, including rapid off/on.
                return before if args.continuous else observer.read()['f7_requests']
            except moving_entry.EntryUnavailable as exc:
                if not args.continuous:
                    raise
                retained(observer.read())
                log('session_entry_deferred', reason=str(exc), retry_after_seconds=.1)
                time.sleep(.1)
                return before
            finally:
                timings.close()
                gameplay_options.reset_input()
                if heartbeat_scope is not None:
                    heartbeat_scope.close()
                mover.clear()
                attack.clear()
                publisher.clear()
                if camera: camera.clear()
                if hud_publisher: hud_publisher.clear()
                # Native voices can loop while no source frames are advancing.
                # Stop them through the original sound manager before GTA waits.
                failure = sys.exc_info()[1]
                try:
                    if audio_mix is not None:
                        audio_mix.suspend()
                    if (not driver.poisoned and not api._thaw_native_worker.command_failed
                            and (rails is None or not rails.poisoned)):
                        if not vehicles.world.poisoned:
                            vehicles.park()
                        driver.audio.stop_all()
                except Exception as cleanup_error:
                    if failure is None:
                        raise
                    # A failed original update leaves its busy/clock flags set.
                    # Preserve that primary error instead of masking it with a
                    # cleanup preflight refusal; child teardown owns its voices.
                    log('session_audio_cleanup_error', message=str(cleanup_error),
                        original_error=str(failure))
                finally:
                    args.report.with_suffix('.frames.json').write_text(json.dumps(list(samples)), encoding='utf-8')

        probe_scene.run, probe_audio.run, probe_frames.run = game_scene, game_audio, live_frames
        probe_skater.viewports = game_viewports
        host.probe(cache / 'stock-worker/manifest.json', args.report,
            qb_manifest=cache / 'stock-qb/manifest.json',
            resources_manifest=cache / 'stock-common-audio-resources/manifest.json',
            game_profile_manifest=cache / 'stock-profile/manifest.json',
            skater_assets_manifest=cache / 'stock-assets/manifest.json', scene=True, core=True,
            frames=True, compact_log=args.continuous)
    finally:
        probe_scene.run, probe_audio.run, probe_frames.run = old_scene, old_audio, old_frames
        probe_skater.viewports = old_viewports
        if camera: camera.close()
        if aim_input: aim_input.close()
        if effects: effects.close()
        if menu: menu.close()
        if attack: attack.close()
        if hud_publisher: hud_publisher.close()
        if mover: mover.close()
        if publisher: publisher.close()
        if memory: memory.close()
        if acknowledgement: acknowledgement.close()
        if streamer: streamer.close()
        observer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', required=True, type=int)
    parser.add_argument('--scene', type=Path)
    parser.add_argument('--world-stream', action='store_true', help='Prepare current static map collision from the local world database')
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--wait', type=float, default=30)
    parser.add_argument('--sessions', type=int, default=1, help='Bounded J-toggle sessions using one original engine')
    parser.add_argument('--camera', action='store_true', help='Publish original camera to the opt-in native adapter')
    parser.add_argument('--hud', action='store_true', help='Draw the original HUD using local stock fonts and sprites')
    parser.add_argument('--continuous', action='store_true', help='Keep gameplay active until toggled off with J; finite per-call and heartbeat guards remain')
    parser.add_argument('--stop-file', type=Path, help='Cooperative local shutdown signal from the owning supervisor')
    parser.add_argument('--f7-baseline', type=int, help='Supervisor counter captured before continuous backend startup')
    args = parser.parse_args()
    if (args.pid <= 0 or not math.isfinite(args.seconds) or not 1 <= args.seconds <= 30
            or not math.isfinite(args.wait) or not 1 <= args.wait <= 120
            or not 1 <= args.sessions <= 10 or (not args.world_stream and (args.scene is None or not args.scene.is_absolute()))
            or (args.world_stream and not args.continuous) or not args.report.is_absolute()
            or (args.stop_file is not None and not args.stop_file.is_absolute())
            or (args.f7_baseline is not None and (not args.continuous or not 0 <= args.f7_baseline <= 0xffffffff))
            or args.report.suffix != '.jsonl' or args.report.exists()
            or args.report.with_suffix('.ready').exists()):
        parser.error('Use positive PID, absolute scene/new .jsonl report, 1..10 sessions, 1..30 s per session and 1..120 s wait')
    run(args)


if __name__ == '__main__': main()
