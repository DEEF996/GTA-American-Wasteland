"""Original stock audio frame phase and bounded bank play/stop proof.

Uses the existing original manager/bank and real DirectSound objects. No game
events, spatial attachment, hardware input or foreground changes are generated.
The isolated proof's two requested samples are explicitly test playback only.
"""
from contextlib import contextmanager
import hashlib
import struct

import probe_audio
import probe_audio_bank
import probe_loader as host
import probe_vm_idle
from probe_frame_clock import FrameClock

ALLOCATOR, BOTTOM_UP, CRITICAL = 0x842b18, 0x8436b0, 0x6e9ab4
PLATFORM_VOICES, ACTIVE_VOICES, ATTACHED = 0x6e6058, 0x6e64d8, 0x6e5ba8
PINS = {
    'Poll': ('OriginalAudioFramePoll', 0x441360, 74, '8e77381939af2f759246d3a349c9dc0b1d961fca97be760047cad2917ede6ca3'),
    # This protected entry is reached by the exact original caller below. The
    # whole OS-loaded image remains pinned; no transplanted old function body.
    'Update': ('OriginalAudioFrameUpdate', 0x442100, 6, 'aad803f92358d1298ab21bd2f3d88da1c43b68d678af34740eccc3ee2c596783'),
    'Order': ('OriginalAudioFrameOrder', 0x57fbe0, 193, 'b675a39b971181d84eb3fe037298a55eb5377e288e1dd4ceb4a37a7d129e7819'),
    'NoStreamEnd': ('OriginalAudioFrameStreamSentinel', 0x4b93c0, 4, '031a1d90a8a2c46993613c8b2828efd0c52f20f2852d5f2ff930bc6acc0090c8'),
    'Worker': ('OriginalAudioFrameWorker', 0x499480, 108, '6ec0d60b60b7930ffeae537f604640ad8f153887dae816c3c0f3f15e690b10ea'),
    'Push': ('OriginalAudioFramePush', 0x626260, 36, '0618d94e5fb6c4434feb1accaba72d1c90904bbc74e780a1683ce2f8944eebc3'),
    'Pop': ('OriginalAudioFramePop', 0x626290, 57, '4210c6cf2e7ebda9ca432215a40869161c4a36437e4536ad47444c8025bd0546'),
    'Volume': ('OriginalAudioVolumeCtor', 0x440270, 8, '6b32393fa1762b1130bef9b8db7596de23f5b19d9bd4bc3ca14f5343ed17727f'),
    'VolumeCaller': ('OriginalAudioVolumeCaller', 0x4441f5, 13, '6dc17df5c78304151276f81dcd52705a533927209a04e3e4bf9201f5656bbb9c'),
    'Play': ('OriginalAudioPlayPlatform', 0x443f30, 675, '1594d479d081e05a0a92f47767c7162e331f903efe1928d41a65165c4bf3173c'),
    'Stop': ('OriginalAudioStopPlatform', 0x443360, 27, '2296d650f9c2d8dd89670683ca4208112e753601d876bd5d5fc861a1d805b3c0'),
    'PlatformPoll': ('OriginalAudioPlatformPoll', 0x4433d0, 86, '830a8365ab5ad0579c9b1484a7c1dbe0a33659e72b8737918d9b8a1eed242992'),
    'StopAll': ('OriginalStopAllSoundsCallback', 0x48af60, 16, '3c86907d1c79f69710b654e373e3d9b3a6aa65a9563052698bece047e3b49d5b'),
    'StopAllMethod': ('OriginalStopAllSoundsMethodEntry', 0x440620, 6, 'a826ba9524e9c9f397cc369e90b9bb0bca645e209dd909b91cbe12a71676b47c'),
    'StopAllRegistration': ('OriginalStopAllSoundsRegistration', 0x6d33b0, 8, 'c4129b33f95a63d9738433fa0fab05a118892b056eab6825a111dca539b5e2f9'),
}


@contextmanager
def locked(system):
    # The existing original worker uses this same initialized critical section.
    # All calls execute serially on our one retained native command thread.
    system.call('kernel32.dll', 'EnterCriticalSection', (CRITICAL,))
    try:
        yield
    finally:
        system.call('kernel32.dll', 'LeaveCriticalSection', (CRITICAL,))


class FramePhase:
    def __init__(self, system, call, log, *, context=None, vm_sources=None):
        self.system, self.call, self.log = system, call, log
        self.sample_count = getattr(system, 'terrain_audio_samples', 113)
        host.require(self.sample_count in (113, 144), 'Unverified audio sample inventory')
        self.context, self.vm_sources = context, vm_sources
        self.owners = {p: system.word(p) for p in (probe_audio.MANAGER, probe_audio.DEVICE, probe_audio.MUTEX)}
        self.preflight()

    def preflight(self):
        s = self.system
        start, end = s.word(0x84376c), s.word(0x843770)
        manager = self.owners[probe_audio.MANAGER]
        host.require(start <= manager <= end - 8 and manager % 4 == 0 and
                     self.owners[probe_audio.DEVICE] >= 0x10000 and self.owners[probe_audio.MUTEX] != 0 and
                     all(s.word(p) == value for p, value in self.owners.items()) and
                     s.word(probe_audio.REFERENCES) == 1 and s.word(probe_audio.SAMPLES) == self.sample_count and
                     1 <= s.word(probe_audio.VOICES) <= 48 and s.read(probe_audio.HARDWARE_3D, 1) == b'\0' and
                     s.word(0x84388c) == ALLOCATOR and s.read(0x6dcf98, 2) == bytes(2) and
                     s.read(0x6dd4a4, 1) == b'\0',
                     'Original audio frame owners, bank or idle scheduler identity differs')
        if self.context is None:
            host.require(s.word(0x6dcf84) == 0, 'Original audio frame requires verified idle VM provenance')
        else:
            probe_vm_idle.require_idle(s, self.context, self.vm_sources)

    def update(self):
        """After owner update, before spawned scripts, within the open clock frame."""
        self.preflight()
        s, call = self.system, self.call
        depth, current = s.word(ALLOCATOR + 0x10c), s.word(ALLOCATOR + 0xc8)
        host.require(depth < 15 and current == (ALLOCATOR + 0xc8 + depth * 4 if depth else 0) and
                     s.word(ALLOCATOR + 0x118) == s.word(ALLOCATOR + 4) == BOTTOM_UP and
                     s.word(BOTTOM_UP) == 0x6a2200 and s.word(BOTTOM_UP + 0x24) == s.word(ALLOCATOR + 0x110) != 0 and
                     s.word(BOTTOM_UP + 0x28) == 1 and s.word(BOTTOM_UP + 0x34) == 0x6a22b4 and
                     s.read(0x6a22b4, 9) == b'BottomUp\0', 'Original audio frame BottomUp/context differs')
        prefix = s.read(ALLOCATOR + 0xc8, (depth + 1) * 4)
        bank = s.read(0x6e0f90, self.sample_count * 32)
        attached = s.word(ATTACHED)
        with locked(s):
            call('Poll', this=self.owners[probe_audio.MANAGER])
            call('Push', (BOTTOM_UP,), ALLOCATOR)
            try:
                host.require(s.word(ALLOCATOR + 0x10c) == depth + 1 and
                             s.word(ALLOCATOR + 0xc8) == ALLOCATOR + 0xcc + depth * 4 and
                             s.word(ALLOCATOR + 0xcc + depth * 4) == BOTTOM_UP,
                             'Original audio frame did not push its genuine BottomUp heap')
                call('Update', this=self.owners[probe_audio.MANAGER])
            finally:
                call('Pop', this=ALLOCATOR)
            host.require(s.word(ALLOCATOR + 0x10c) == depth and s.read(ALLOCATOR + 0xc8, len(prefix)) == prefix and
                         s.word(ALLOCATOR + 0xcc + depth * 4) == 0xffffffff,
                         'Original audio frame did not restore its heap context')
            active = s.word(ACTIVE_VOICES)
            host.require(active <= s.word(probe_audio.VOICES), 'Original active voice count exceeds capacity')
        self.preflight()
        host.require(s.read(0x6e0f90, len(bank)) == bank, 'Original audio frame changed immutable sound records')
        self.log('audio_frame_pass', original_poll=0x441360, original_update=0x442100,
                 original_frame_order_witness=0x57fbe0, bottom_up_restored=True,
                 attached_before=attached, attached_after=s.word(ATTACHED), native_active_voices=active,
                 host_audio_events_generated=False, test_playback_invoked=False,
                 worker_handle_liveness_verified=False, all_gameplay_SFX_proven=False)

    def stop_all(self):
        """Stop genuine SFX voices between sessions without advancing a frame.

        The manager, bank and original polling worker stay alive. This is not a
        worker shutdown or proof that a music/streaming session has been stopped.
        """
        self.preflight()
        s = self.system
        host.require(s.read(0x8459c8, 8) == s.read(0x8459d0, 8),
                     'StopAllSounds requires a completed native clock frame')
        vm_before = (probe_vm_idle.require_idle(s, self.context, self.vm_sources)
                     if self.context is not None else None)
        clock_before = s.read(0x8459c8, 16)
        bank = s.read(0x6e0f90, self.sample_count * 32)
        heap = s.read(ALLOCATOR + 0xc8, 0x48)
        output = s.allocate(bytes(4))

        def voices():
            count = s.word(probe_audio.VOICES)
            host.require(1 <= count <= 48, 'StopAllSounds voice capacity changed')
            raw = s.read(PLATFORM_VOICES, count * 24)
            rows, statuses = [], {}
            for i in range(count):
                pointer = struct.unpack_from('<I', raw, i * 24)[0]
                active = raw[i * 24 + 16]
                host.require(active <= 1 and (not active or pointer >= 0x10000) and
                             (pointer == 0 or pointer >= 0x10000 and pointer % 4 == 0),
                             'StopAllSounds voice record has invalid buffer ownership')
                if pointer and pointer not in statuses:
                    host.require(s.com('StopAllSounds:GetStatus', pointer, 9, (output,)) == 0,
                                 'StopAllSounds cannot query the real DirectSound voice')
                    statuses[pointer] = s.word(output)
                rows.append(dict(index=i, buffer=pointer, active=bool(active), status=statuses.get(pointer, 0)))
            host.require(sum(row['active'] for row in rows) == s.word(ACTIVE_VOICES),
                         'StopAllSounds native active count differs from its voice records')
            return rows

        with locked(s):
            before, attached_before = voices(), s.word(ATTACHED)
            # The pinned callback ignores both VM arguments, calls the actual
            # manager's StopAll method, then returns true. No fake Script/params.
            host.require(self.call('StopAll', (0, 0)) & 0xff == 1,
                         'Original StopAllSounds callback rejected the request')
            self.call('PlatformPoll')
            after = voices()
            host.require(s.word(ACTIVE_VOICES) == 0 and
                         all(not row['active'] and not row['status'] & 1 for row in after),
                         'Original StopAllSounds left a real native voice playing')
            attached_after = s.word(ATTACHED)
        self.preflight()
        host.require(s.read(0x8459c8, 16) == clock_before and s.read(0x6e0f90, len(bank)) == bank and
                     s.read(ALLOCATOR + 0xc8, len(heap)) == heap,
                     'Original StopAllSounds changed frame timing, bank or heap context')
        if vm_before is not None:
            host.require(probe_vm_idle.require_idle(s, self.context, self.vm_sources) == vm_before,
                         'Original StopAllSounds changed its admitted VM context')
        result = dict(voices_before=before, voices_after=after, attached_before=attached_before,
                      attached_after=attached_after, real_buffers_stopped=True, native_active_voices=0,
                      original_callback=0x48af60, original_method=0x440620, original_poll=0x4433d0,
                      native_gameplay_frames=0, audio_frame_updates=0, host_audio_state_writes=False,
                      original_audio_worker_retained=True, music_stream_shutdown_proven=False)
        self.log('audio_stop_all_pass', **result)
        return result


class AudioFrame(FramePhase):
    def __init__(self, api, process, record, deadline, log, system, *, context=None, vm_sources=None):
        for name, address, size, digest in PINS.values():
            host.require(hashlib.sha256(system.read(address, size)).hexdigest() == digest,
                         'Original audio frame entry differs: ' + name)
        def call(name, args=(), this=None):
            return host.native_call(api, process, PINS[name], list(args), record, deadline, log, this)
        super().__init__(system, call, log, context=context, vm_sources=vm_sources)


def read_pcm(system, buffer, expected):
    """Read both real COM Lock spans and always Unlock, including on refusal."""
    outputs = system.allocate(bytes(16))
    result = system.com('IDirectSoundBuffer::Lock', buffer, 11,
                        (0, 0, outputs, outputs + 4, outputs + 8, outputs + 12, 2))
    host.require(result == 0, 'Cannot lock actual native sound PCM')
    first, first_size, second, second_size = struct.unpack('<4I', system.read(outputs, 16))
    try:
        host.require(0 < expected <= 1024 * 1024 and first_size + second_size == expected and
                     all(size == 0 or 0x10000 <= pointer <= 0x100000000 - size
                         for pointer, size in ((first, first_size), (second, second_size))),
                     'Actual DirectSound PCM lock spans are invalid')
        return b''.join(system.read(pointer, size) for pointer, size in ((first, first_size), (second, second_size)) if size)
    finally:
        host.require(system.com('IDirectSoundBuffer::Unlock', buffer, 19, (first, first_size, second, second_size)) == 0,
                     'Cannot unlock actual native sound PCM')


def query_voice(system, buffer, size, frequency):
    output = system.allocate(bytes(16))
    for label, slot, args in (('GetStatus', 9, (output,)), ('GetFrequency', 8, (output + 4,)),
                              ('GetCurrentPosition', 4, (output + 8, output + 12))):
        host.require(system.com('IDirectSoundBuffer::' + label, buffer, slot, args) == 0,
                     'Actual DirectSound query failed: ' + label)
    status, rate, cursor, write_cursor = struct.unpack('<4I', system.read(output, 16))
    host.require(not status & 2 and rate == frequency and cursor < size and write_cursor < size,
                 'Actual native voice is lost or has changed rate/cursor bounds')
    return dict(status=status, frequency=rate, play_cursor=cursor, write_cursor=write_cursor)


def prove_playback(frame, inputs):
    """Two requested original samples; this is not a gameplay-trigger test."""
    s, call, log = frame.system, frame.call, frame.log
    host.require(hashlib.sha256(inputs['source']).hexdigest() == probe_audio_bank.SOURCE_SHA and
                 hashlib.sha256(inputs['bank']).hexdigest() == probe_audio_bank.BANK_SHA,
                 'Original audio playback inputs changed after whole-bank verification')
    samples = probe_audio_bank.parse_samples(inputs['source'], inputs['bank'])
    probe_audio_bank.validate_table(s, samples, inputs['bank'], s.word(0x84376c), s.word(0x843770))
    rows = []
    for key, loop in ((0x6a8a32d5, False), (0xc603a0ce, True)):
        frame.preflight()
        selected = [(i, row) for i, row in enumerate(samples) if row['checksum'] == key]
        host.require(len(selected) == 1 and bool(selected[0][1]['flags'] & 8) == loop and
                     s.word(ACTIVE_VOICES) == s.word(ATTACHED) == 0 and s.word(0x6e5b98) == 0,
                     'Bounded audio playback requires an idle original bank and exact one-shot/loop samples')
        index, sample = selected[0]
        record = 0x6e0f90 + index * 32
        descriptor = s.word(record + 20)
        volume = s.allocate(bytes(40))
        call('Volume', this=volume)
        # Declared API inputs, never writes to manager, voice or bank fields.
        s.write(volume, struct.pack('<2f', sample['volume'], sample['volume']))
        pitch = struct.unpack('<I', struct.pack('<f', sample['pitch']))[0]
        with locked(s):
            host.require(all(s.read(PLATFORM_VOICES + i * 24 + 16, 1) == b'\0' for i in range(s.word(probe_audio.VOICES))),
                         'A native voice became active before bounded test playback')
            attempted = False
            try:
                attempted = True
                native_return = call('Play', (0xffffffff, record, volume, pitch, 0)) & 0xff
                buffer = s.word(PLATFORM_VOICES)
                host.require(buffer >= 0x10000 and s.word(PLATFORM_VOICES + 20) == descriptor and
                             s.read(PLATFORM_VOICES + 16, 1) == b'\1' and s.word(ACTIVE_VOICES) == 1,
                             'Original platform playback did not own the expected first voice')
                expected = inputs['bank'][sample['offset'] + 128:sample['offset'] + 128 + sample['pcm_bytes']]
                pcm = read_pcm(s, buffer, len(expected))
                host.require(pcm == expected, 'Actual DirectSound PCM differs from original bank sample')
                before = query_voice(s, buffer, len(expected), sample['frequency'])
                host.require(bool(before['status'] & 4) == loop, 'Actual DirectSound looping flag differs from original sample')
                # A hidden cooperative window may prevent audible/cursor motion.
                # Record those OS observations without manufacturing success.
                s.call('kernel32.dll', 'Sleep', (20,))
                after = query_voice(s, buffer, len(expected), sample['frequency'])
            finally:
                if attempted:
                    call('Stop', (0,))
                    call('PlatformPoll')
            stopped = query_voice(s, buffer, len(expected), sample['frequency'])
            host.require(not stopped['status'] & 1 and s.word(ACTIVE_VOICES) == 0 and
                         s.read(PLATFORM_VOICES + 16, 1) == b'\0', 'Original stop/poll did not stop the real native voice')
        row = dict(checksum=key, original_loop=loop, source_pcm_sha256=hashlib.sha256(expected).hexdigest(),
                   actual_buffer_pcm_identical=True, native_return_al=native_return,
                   before=before, after=after, stopped=stopped,
                   cursor_advanced=before['play_cursor'] != after['play_cursor'])
        rows.append(row)
        log('audio_playback_sample_pass', **row, original_stop_poll=True,
            human_audibility_verified=False, hidden_window_may_mute=True, gameplay_trigger_generated=False)
    return rows


def run(api, process, record, deadline, log, inputs, system):
    """Separate bounded proof after whole bank, before actor/first frame clock."""
    frame = AudioFrame(api, process, record, deadline, log, system)
    clock = FrameClock(api, process, record, deadline, log)
    for _ in range(3):
        clock.begin_frame()
        frame.update()
        clock.end_frame()
    playback = prove_playback(frame, inputs)
    log('audio_frame_playback_pass', original_frame_phases=3, requested_samples=len(playback),
        real_OS_buffers_verified=True, original_stop_poll=True, human_audibility_verified=False,
        full_game_loop_executed=False, spatial_audio_and_all_gameplay_SFX_proven=False,
        host_foreground_or_window_changes=False, lifetime='existing graph retained until bounded child exit')
