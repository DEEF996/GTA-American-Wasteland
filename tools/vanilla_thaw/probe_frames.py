"""Retained original frame driver and its sixty-frame bounded probe wrapper.

The host supplies neutral input and calls original frame phases. It does not
implement movement, change owner flags, replace callbacks or draw the HUD.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct

import probe_loader as host
import probe_vm_idle
from probe_frame_clock import FrameClock, validate_max_frames

METADATA_SHA = "8c145122dd16bbd418e5d4874ccdcbb1e52f351335e6e8e7114ad6e32e32a41c"
FRAME_COUNT = 60
WHACKING_GETTER = (0x5F8B30, bytes.fromhex('8a511f33c084d20f95c0c20800'))
BAILING_GETTER = (0x5F2430, 13, 'bccedf3929835f1e975557a172a2d0f0b377ddc65dc2c1c75bcf42ef9ef21fcb')


def board_attack_flag(component_bytes):
    host.require(len(component_bytes) == 0x20 and component_bytes[0x1f] in (0, 1),
                 'Original board-smack component flag differs')
    return bool(component_bytes[0x1f])


def motion_state(state, walking):
    """Read one completed frame's component snapshot, including independent bail."""
    host.require(len(state) == 0x134 // 4 and len(walking) == 7 and
                 state[6] <= 7 and state[7] in (0, 1) and walking[6] <= 7 and
                 (state[0x78 // 4] & 0xff) in (0, 1),
                 'Original skater/walk state or bail flag differs')
    return dict(state=state[6], mode=state[7], walk_state=walking[6],
                walking_gate=state[0x130 // 4] & 0xff,
                bailing=bool(state[0x78 // 4] & 0xff))


def read_metadata():
    raw = Path(__file__).with_name("gameplay_frames.json").read_bytes()
    host.require(hashlib.sha256(raw).hexdigest() == METADATA_SHA, "Original frame metadata differs")
    value = json.loads(raw)
    host.require(value["format"] == "stock-gameplay-frames-pins-v1" and
                 value["stock_exe_sha256"] == host.FILES["THAW.exe"][1], "Original frame image differs")
    host.require(len(value["functions"]) == 12 and len({row[0] for row in value["functions"]}) == 12,
                 "Original frame function inventory differs")
    return value


def finite_vectors(raw):
    host.require(len(raw) == 64, "Native frame vector extent differs")
    values = struct.unpack("<16f", raw)
    host.require(all(math.isfinite(value) for value in values), "Original frame contains nonfinite vectors")
    return values


class FrameDriver:
    """One initialized native graph; failed steps cannot be resumed.

    The default remains the 600-frame probe. A larger explicit limit may use a
    NativeWorker.operation_budget around each step; this driver does not own
    the child lifetime or silently extend captured bootstrap deadlines.
    """
    def __init__(self, actor, initial, advance, counters, max_frames=600):
        self.max_frames = validate_max_frames(max_frames)
        self.actor, self.initial = actor, initial
        self.started, self.frame_count, self.poisoned = True, 0, False
        self._advance, self._counters = advance, counters
        self._stepping, self.last = False, None

    def step(self, packet=None):
        """Return sample, clock, input, raw 52-bone palette and actor matrix.

        Bytes in `palette` and floats in `actor_matrix` are read after the
        completed original frame, before the next native owner update.
        """
        from probe_frame_input import validate_packet
        host.require(self.started and not self.poisoned and not self._stepping,
                     "Original frame driver is unavailable, busy or poisoned")
        host.require(self.max_frames is None or self.frame_count < self.max_frames,
                     "Original frame driver reached its bounded clock limit")
        self._stepping = True
        try:
            if packet is not None:
                validate_packet(packet)
            result = self._advance(self.frame_count + 1, packet)
            host.require(result['sample']['frame'] == result['clock']['native_frame_count'] == self.frame_count + 1 and
                         result['clock']['frame_open'] is False, "Original frame driver did not complete the requested tick")
            self.frame_count += 1
            self.last = result
            return result
        except BaseException:
            self.poisoned = True
            raise
        finally:
            self._stepping = False

    def summary(self):
        return dict(frames=self.frame_count, actor=self.actor, initial=self.initial,
                    final=self.last['sample'] if self.last else self.initial,
                    started=self.started, frame_count=self.frame_count, max_frames=self.max_frames, poisoned=self.poisoned,
                    **self._counters, native_heap_stack_restored=not self.poisoned,
                    replacement_physics=False, owner_flags_written=False,
                    whole_task_manager_executed=False,
                    sound_update_executed=self._counters['original_sound_updates'] > 0,
                    hud_draw_executed=False, backend_ready=False, gta_actor_published=False)


def initialize(api, process, record, deadline, log, context, inputs, preloaded, system, *, max_frames=600):
    """Prepare the original registries/resources once, then return a frame driver."""
    max_frames = validate_max_frames(max_frames)
    from probe_frame_input import InputTransport
    from probe_audio_frame import AudioFrame
    import probe_fonts
    import probe_global_assets
    import probe_hud
    import probe_animation_banks
    import probe_frame_device
    import probe_local_player
    import probe_object_pools
    import probe_screen_effects
    import probe_level_registry
    from probe_model_frame import ModelFrame, DetachedBoards

    metadata = read_metadata()
    pins = {row[0]: row for row in metadata["functions"]}

    def read(address, size):
        return host.remote_read(api, process, address, size)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    for _, address, size, digest in pins.values():
        host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, "Pinned original frame function differs")
    host.require(read(WHACKING_GETTER[0], len(WHACKING_GETTER[1])) == WHACKING_GETTER[1],
                 'Original IsWhacking getter differs')
    host.require(hashlib.sha256(read(BAILING_GETTER[0], BAILING_GETTER[1])).hexdigest() == BAILING_GETTER[2],
                 'Original InBailState getter differs')

    arena_start, arena_end = word(0x84376c), word(0x843770)

    def words(pointer, count):
        host.require(pointer % 4 == 0 and arena_start <= pointer and pointer + count * 4 <= arena_end,
                     "Original frame object leaves its native arena")
        return struct.unpack(f"<{count}I", read(pointer, count * 4))

    def call(name, arguments=(), this=None):
        return host.native_call(api, process, pins[name], list(arguments), record, deadline, log, this)

    actor, manager = context["actor"], word(0x6dd6b0)
    model_frame = ModelFrame(read, words, actor, context['components'][0x286a8d26])
    detached_boards = DetachedBoards(words, actor, manager, model_frame)
    cleanup_task = words(manager, 0x44 // 4)[0x3c // 4]
    allocator, bottomup = 0x842b18, 0x8436b0

    def stack():
        depth, current = word(allocator + 0x10c), word(allocator + 0xc8)
        host.require(depth < 16 and current == (allocator + 0xc8 + depth * 4 if depth else 0),
                     "Original frame heap stack is invalid")
        return depth, current

    def preflight():
        host.require(word(0x6dd6b0) == manager and word(0x6dd3f4) and word(0x84388c) == allocator,
                     "Original frame managers changed")
        host.require(read(0x6dd4a4, 1) == b"\0" and read(0x6dcf98, 2) == bytes(2),
                     "Original owner/script dispatcher is still busy")
        probe_vm_idle.require_idle(system, context, preloaded)
        owner, task = words(manager, 0x44 // 4), words(cleanup_task, 8)
        tasks = words(word(0x6dd8d8), 0xb4 // 4)
        node = words(task[2], 5)
        host.require(0x68e000 <= owner[0] < 0x6bc000 and owner[0x3c // 4] == cleanup_task and
                     0x68e000 <= task[0] < 0x6bc000 and task[3] == tasks[0xb0 // 4] and
                     task[6] == pins["DeferredOwnerCleanup"][1] and task[7] == manager and
                     node[1] == cleanup_task and node[2] == 1000,
                     "Original cleanup task/list/owner identity differs")
        host.require(word(allocator + 0x118) == bottomup == word(allocator + 4) and
                     word(bottomup) == 0x6a2200 and word(bottomup + 0x24) == word(allocator + 0x110) != 0 and
                     word(bottomup + 0x28) == 1 and word(bottomup + 0x34) == 0x6a22b4 and
                     read(0x6a22b4, 9) == b"BottomUp\0", "Original scheduler BottomUp heap differs")
        stack()

    def owner_list(deferred=False):
        sentinel = manager + (0x28 if deferred else 4)
        header = words(sentinel, 5)
        host.require(0x68e000 <= header[0] < 0x6bc000 and header[1:3] == (0xffffffff, 0),
                     "Original owner-list sentinel differs")
        pointer, previous, seen, owners = header[3], sentinel, set(), set()
        while pointer != sentinel:
            host.require(pointer not in seen and len(seen) < 4096, "Original owner list cycles or exceeds its bound")
            seen.add(pointer)
            node = words(pointer, 5)
            owner = words(node[1], 0x3c // 4)
            host.require(node[4] == previous and node[1] not in owners, "Original owner list backlinks/identity differ")
            if deferred:
                host.require(node[1] + 0x38 == pointer and owner[2] & 1 and owner[3] == 0,
                             "Deferred owner is not genuinely killed and unregistered")
            else:
                host.require(owner[3] == manager, "Live owner is not registered with the actual ComponentManager")
            owners.add(node[1])
            previous, pointer = pointer, node[3]
        host.require(header[4] == previous, "Original owner list tail differs")
        return owners

    def sample(frame):
        values = words(actor, 0x35c // 4)
        host.require(values[0] == 0x6989e8 and values[3] == manager, "Original skater class or owner changed")
        vectors = finite_vectors(read(actor + 0x68, 48) + read(actor + 0xc8, 16))
        components = context["components"]
        # The original IsWhacking getter reads this exact byte after script update.
        tagging = words(components[0x380D909A], 8)
        host.require(tagging[1] == 0x380D909A and tagging[3] == actor,
                     'Original board-smack component identity changed')
        whacking = board_attack_flag(struct.pack('<8I', *tagging))
        state = words(components[0x43686585], 0x134 // 4)
        walking = words(components[0x726e85aa], 7)
        motion = motion_state(state, walking)
        skeleton = words(words(components[0x222756d5], 7)[6], 7)
        host.require(skeleton[5] == 52 and skeleton[1] % 16 == 0, "Original frame skeleton shape changed")
        palette = read(skeleton[1], 52 * 64)
        host.require(all(math.isfinite(value) for value in struct.unpack("<832f", palette)),
                     "Original frame skeleton contains nonfinite matrices")
        actor_matrix = finite_vectors(read(actor + 0x88, 64))
        visual_matrix, board_visible = model_frame.sample(skeleton[1], palette)
        board_pose = detached_boards.sample(owner_list(), board_visible)
        active = word(0x6dcf90)
        host.require(active <= 4096 and bool(active) == bool(word(0x6dcf8c)), "Original script registry/count differs")
        snapshot = dict(frame=frame, position=list(vectors[:3]), previous_position=list(vectors[4:7]),
                    velocity=list(vectors[12:15]), **motion, active_scripts=active,
                    board_attack=whacking,
                    board_visible=board_visible,
                    detached_board_id=board_pose['object_id'] if board_pose else 0,
                    palette_sha256=hashlib.sha256(palette).hexdigest())
        return snapshot, palette, actor_matrix, visual_matrix, board_pose

    preflight()
    host.require(actor in owner_list(), "Initialized actor is absent from the actual owner update list")
    probe_local_player.run(api, process, record, deadline, log, context, system)
    # Detached/bail boards share this original singleton. Initialize it once
    # for every frame driver, including both live and isolated probe callers.
    probe_object_pools.run(api, process, record, deadline, log, context, system, preloaded=preloaded)
    # Game::create_skater performs this whole registrar after ProfileInit.
    # Component ownership alone does not publish the local Players slot.
    players = word(0x6ea804)
    player_manager = words(players, 0x20c // 4)
    host.require(0x68e000 <= player_manager[0] < 0x6bc000 and word(0x6ea808) == 1 and
                 word(0x6dcf84) == 0, "Original Players context is absent or a script is executing")
    node, sentinel = actor + 0x128, players + 0x1f8
    before = words(actor, 0x35c // 4)
    original_node = words(node, 5)
    original_sentinel = words(sentinel, 5)
    host.require(before[6] == 0 and before[0xd8 // 4] & 0xff == 1 and before[0x2f0 // 4] == 0 and
                 0x68e000 <= original_node[0] < 0x6bc000 and original_node[1:] == (actor, 0, node, node) and
                 0x68e000 <= original_sentinel[0] < 0x6bc000 and original_sentinel[1:] == (0xffffffff, 0, sentinel, sentinel),
                 "Original local actor or Players list is already registered or has invalid links")
    host.require(call("PlayersLookup", (0,), players) == 0, "Original Players slot zero is already occupied")
    host.require(call("PlayersRegister", (actor, before[6]), players) & 0xff != 0,
                 "Original Players registrar rejected the real initialized actor")
    after = words(actor, 0x35c // 4)
    host.require(words(node, 5) == original_node[:3] + (sentinel, sentinel) and
                 words(sentinel, 5) == original_sentinel[:3] + (node, node) and
                 before[:0x134 // 4] == after[:0x134 // 4] and before[0x13c // 4:] == after[0x13c // 4:] and
                 call("PlayersLookup", (0,), players) == actor,
                 "Original player registration changed actor state or did not publish its genuine list node")
    log("frame_player_registered", actor=actor, players=players, slot=0, native_lookup_returns_actor=True,
        original_actor_fields_preserved_except_list_links=True, host_actor_or_list_writes=False)
    probe_fonts.run_platform(api, process, record, deadline, log)
    probe_fonts.run_sprites(api, process, record, deadline, log)
    probe_global_assets.run(api, process, record, deadline, log, inputs["global_assets"], preloaded, system)
    probe_fonts.run(api, process, record, deadline, log, inputs["fonts"], preloaded)
    probe_hud.run(api, process, record, deadline, log, inputs["hud"], preloaded)
    probe_animation_banks.run(api, process, record, deadline, log, inputs["animation_banks"], preloaded, system)
    device = probe_frame_device.run(api, process, record, deadline, log, context, system)
    probe_screen_effects.run(api, process, record, deadline, log, system)
    level_registry = probe_level_registry.run(api, process, record, deadline, log, system)
    import terrain_audio
    terrain_audio.register(api, process, record, deadline, log, system, inputs['audio_bank']['terrain_audio'])
    audio = AudioFrame(api, process, record, deadline, log, system, context=context, vm_sources=preloaded)
    clock = FrameClock(api, process, record, deadline, log, max_frames=max_frames)
    input_transport = InputTransport(api, process, record, deadline, log, context)
    initial, initial_palette, initial_matrix, initial_visual, initial_board = sample(0)
    from probe_body_correction import BodyWitness
    body_witness=BodyWitness(api,process,system,context,log)
    from probe_ground_trace import initialize as initialize_ground_trace
    ground_trace=initialize_ground_trace(api,process,system,context,log,body_witness=body_witness)
    counters = dict(original_cleanup_calls=0, original_owner_updates=0, original_sound_updates=0,
                    original_spawned_updates=0, frames_with_deferred_owners=0, neutral_input_only=True)

    def advance(frame, packet):
        if ground_trace is not None:ground_trace.begin(frame)
        clock.begin_frame()
        transport = input_transport.feed() if packet is None else input_transport.feed(packet)
        counters['neutral_input_only'] &= transport['neutral']
        log("frame_input", frame=frame, **transport)
        preflight()
        host.require(word(probe_level_registry.REGISTRY) == level_registry,
                     'Original level-node registry owner changed during gameplay')
        device.validate()
        pending = len(owner_list(deferred=True))
        counters['frames_with_deferred_owners'] += int(pending != 0)
        before_stack = stack()
        log("frame_phase", frame=frame, native_phase="deferred_cleanup", pending=pending)
        call("DeferredOwnerCleanup", (cleanup_task,))
        counters['original_cleanup_calls'] += 1
        owner_list(deferred=True)
        preflight()
        host.require(stack() == before_stack, "Original cleanup changed the active heap stack")
        log("frame_phase", frame=frame, native_phase="owner_components")
        call("ComponentManagerUpdate", this=manager)
        if ground_trace is not None:ground_trace.stage('components_after',frame)
        counters['original_owner_updates'] += 1
        host.require(read(0x6dd4a4, 1) == b"\0", "Original owner update retained its busy flag")
        device.validate()
        # Original 57FBE0 runs sound Poll/Update after owner components and
        # before spawned scripts, inside this same open simulation-clock frame.
        log("frame_phase", frame=frame, native_phase="sound_update")
        audio.update()
        counters['original_sound_updates'] += 1
        depth, current = stack()
        call("HeapContextPush", (bottomup,), allocator)
        host.require(word(allocator + 0x10c) == depth + 1 and
                     word(allocator + 0xc8) == allocator + 0xcc + depth * 4 and
                     word(allocator + 0xcc + depth * 4) == bottomup,
                     "Original scheduler did not select the genuine BottomUp heap")
        log("frame_phase", frame=frame, native_phase="spawned_scripts")
        call("SpawnedScriptsUpdate")
        if ground_trace is not None:ground_trace.stage('spawned_scripts_after',frame)
        counters['original_spawned_updates'] += 1
        call("HeapContextPop", this=allocator)
        host.require(stack() == (depth, current) and word(allocator + 0xcc + depth * 4) == 0xffffffff,
                     "Original spawned scheduler did not restore its heap context")
        preflight()
        device.validate()
        timing = clock.end_frame()
        host.require(timing["native_frame_count"] == frame, "Original frame count differs from the bounded host sequence")
        host.require(actor in owner_list(), "Original gameplay unregistered the controlled actor")
        host.require(call("PlayersLookup", (0,), players) == actor, "Original local Players slot no longer owns the controlled actor")
        current_sample, palette, actor_matrix, visual_matrix, board_pose = sample(frame)
        log("gameplay_frame", **current_sample, clock=timing, input=transport)
        return dict(sample=current_sample, palette=palette, actor_matrix=actor_matrix,
                    visual_matrix=visual_matrix, visual_evaluated=True, board_pose=board_pose, clock=timing, input=transport)

    driver = FrameDriver(actor, initial, advance, counters, max_frames=max_frames)
    driver.audio = audio
    driver.ground_trace=ground_trace
    driver.body_witness=body_witness
    driver.initial_pose = dict(sample=initial, palette=initial_palette, actor_matrix=initial_matrix,
                              visual_matrix=initial_visual, visual_evaluated=False, board_pose=initial_board)
    return driver


def run(api, process, record, deadline, log, context, inputs, preloaded, system):
    driver = initialize(api, process, record, deadline, log, context, inputs, preloaded, system)
    log("frames_begin", frames=FRAME_COUNT, actor=driver.actor, original_input="neutral host packet through stock decoders",
        initial=driver.initial, whole_task_manager_executed=False, hud_draw_executed=False)
    for _ in range(FRAME_COUNT):
        driver.step()
    log("frames_pass", **driver.summary())
