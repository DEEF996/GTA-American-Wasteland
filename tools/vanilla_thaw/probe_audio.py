"""Original stock audio-device/manager bootstrap on the owned hidden child.

The original factory owns its constructor and worker. No manager, voice, sample
or hardware-input fields are synthesized. The complete graph is retained until
child termination: reusable worker stop/join and bank playback are not proved.
"""
import hashlib
import struct

import probe_loader as host

MUTEX, MUTEX_EXIT = 0x764dec, 0x68ce60
DEVICE, MANAGER, REFERENCES = 0x84aa8c, 0x6e5b90, 0x6e5b94
SAMPLES, HARDWARE_3D, VOICES = 0x6e5ba4, 0x6e64dc, 0x6e64e0
PRESERVED = ((0x84376c, 8), (0x84388c, 4), (0x6dd6b0, 8), (0x6dcf84, 16),
             (0x6dcf98, 2), (0x6dd4a4, 1), (0x844230, 4), (0x84aa7c, 4))
PINS = {
    "MutexCRT": ("OriginalAudioMusicMutexCRT", 0x688df0, 29,
        "66f13acc75e4a950bbab521430fffde02c70df02b7a324b4aa38fdef7a956826"),
    "MutexExit": ("OriginalAudioMusicMutexExit", MUTEX_EXIT, 13,
        "d1a774038ad3f436aca39af7e7f80571d25debad67103c104336b695488d9b9f"),
    "DeviceInit": ("OriginalDirectSoundDeviceInit", 0x669310, 37,
        "5814252be76988a363913100119855e4720578957e78f9aece92f0978ad628e4"),
    "Manager": ("OriginalSoundManagerSingleton", 0x4428d0, 74,
        "369621f717aa5783e0547bc156f6ea5566470e7bc1d4bfe0c426d7028a4056c8"),
    "Ctor": ("OriginalSoundManagerCtorDispatch", 0x442880, 6,
        "d30b03bdfe517312d6a903ebd2b5a1e737c89b4f416fffdeac940161ceb59d25"),
    "PlatformInit": ("OriginalAudioPlatformInit", 0x443120, 407,
        "e976c918a02548e4b708cc50c4f19e38fd046ff481e1e259fcea10910b880f29"),
    "WorkerStart": ("OriginalAudioWorkerStartDispatch", 0x4994f0, 6,
        "c6729834b5ea73011312808c79ba975f9fa991c3eabe9ceb6d7dabbc2bfc5437"),
    "Worker": ("OriginalAudioWorker", 0x499480, 108,
        "6ec0d60b60b7930ffeae537f604640ad8f153887dae816c3c0f3f15e690b10ea"),
    "CRTTable": ("OriginalAudioCRTProvenance", 0x6bc000, 552,
        "d26b0becf984e0aa0629dd6688b0ed5cf96276fa5dbfb1f354d221af695d278c"),
}


def callbacks(system):
    begin, end = system.word(0x8505f0), system.word(0x8505ec)
    host.require(begin >= 0x10000 and begin % 4 == end % 4 == 0 and 0 <= end - begin <= 1024,
                 "Audio requires the existing bounded original CRT exit table")
    return struct.unpack(f"<{(end - begin) // 4}I", system.read(begin, end - begin))


def software_voice_capacity(result, raw):
    host.require(result == 0 and len(raw) == 96, "Genuine DirectSound GetCaps failed")
    caps = struct.unpack("<24I", raw)
    host.require(caps[0] == 96 and caps[14] == 0 and caps[8] <= 0x7fffffff,
                 "Original audio proof requires the actual software-only DirectSound device")
    # This is only the guard for the original 443120 branch, not a field write.
    return caps[8] - 5 if 16 < caps[8] <= 53 else 48


def initialize(system, call, log):
    read, word = system.read, system.word
    before = {address: read(address, size) for address, size in PRESERVED}
    prefix = callbacks(system)
    host.require(all(word(address) == 0 for address in (MUTEX, DEVICE, MANAGER, REFERENCES, SAMPLES)) and
                 MUTEX_EXIT not in prefix, "Original audio bootstrap requires unowned cold services")
    host.require(word(0x844230) == 7 and word(0x6dcf84) == 0 and
                 read(0x6dcf98, 2) == bytes(2) and read(0x6dd4a4, 1) == b"\0" and
                 read(0x6c0584, 2) == b"\1\1",
                 "Audio bootstrap requires idle stock PC owner phases and unchanged original stream/music flags")
    start, end = word(0x84376c), word(0x843770)
    host.require(0x10000 <= start < end <= 0xffffffff, "Original audio arena is absent")
    window = system.window
    owner_pid = system.allocate(bytes(4))
    host.require(window != 0 and system.call("user32.dll", "IsWindow", (window,)) != 0 and
                 system.call("user32.dll", "IsWindowVisible", (window,)) == 0 and
                 system.call("user32.dll", "GetWindowThreadProcessId", (window, owner_pid)) == system.worker.tid,
                 "Audio must use the existing hidden window on its retained native worker")
    log("audio_begin", window=window, scope="original device and manager; no sample bank or playback")
    call("MutexCRT")
    host.require(word(MUTEX) != 0 and callbacks(system) == prefix + (MUTEX_EXIT,),
                 "Original audio mutex or destructor registration differs")
    device_result = call("DeviceInit", (window,))
    device = word(DEVICE)
    host.require(device_result == 0 and device >= 0x10000 and device % 4 == 0,
                 "Original DirectSound initializer did not publish its real system device")
    caps = system.allocate(struct.pack("<24I", 96, *([0] * 23)))
    result = system.com("IDirectSound8::GetCaps", device, 4, (caps,))
    capacity = software_voice_capacity(result, read(caps, 96))
    manager = call("Manager", (1,))
    host.require(start <= manager <= end - 8 and manager % 4 == 0 and word(MANAGER) == manager and
                 word(REFERENCES) == 1 and word(DEVICE) == device and word(SAMPLES) == 0 and
                 read(HARDWARE_3D, 1) == b"\0" and word(VOICES) == capacity,
                 "Whole original audio factory did not preserve its software device and publish the expected manager/capacity")
    host.require(callbacks(system) == prefix + (MUTEX_EXIT,) and
                 all(read(address, len(value)) == value for address, value in before.items()) and
                 read(0x6c0584, 2) == b"\1\1",
                 "Original audio initialization changed existing window, allocator, VM or owner identity")
    log("audio_device_manager_pass", manager=manager, device=device, mutex=word(MUTEX),
        original_voice_capacity=capacity, original_factory=True, original_worker_start_path=True,
        worker_handle_liveness_verified=False, original_music_mutex_destructor_appended=True,
        real_hidden_window=window, window_global_modified=False, hardware_input_initialized=False,
        sample_count=0, global_bank_loaded=False, playback_invoked=False,
        host_manager_or_voice_field_writes=False, lifetime="retained until bounded child termination",
        reusable_worker_stop_join_verified=False, backend_ready=False)
    return {"manager": manager, "device": device, "voice_capacity": capacity}


def run(api, process, record, deadline, log, system):
    for name, address, size, digest in PINS.values():
        host.require(hashlib.sha256(system.read(address, size)).hexdigest() == digest,
                     "Original audio provenance differs: " + name)
    host.require(system.word(0x6bc000 + 51 * 4) == PINS["MutexCRT"][1],
                 "Audio mutex initializer is not original CRT member 51")

    def call(name, args=()):
        return host.native_call(api, process, PINS[name], list(args), record, deadline, log)

    return initialize(system, call, log)
