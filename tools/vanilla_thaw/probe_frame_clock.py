"""Original stock 60 Hz simulation clock, on the retained native worker.

The host supplies a simulation tick, never a replacement time delta. Hardware
timer threads and the original main loop remain stopped. Clock data belongs to
the bounded child and is discarded when that child exits.
"""
import hashlib
import math
import struct
from fractions import Fraction

import probe_loader as host

PINS = {
    "reset": ("OriginalClock_reset", 0x62c110, 158, "e74cae3e512d40ce0386c802f0664f7016eec5f1f215f9d3ebe81d75a245cae1"),
    "vblank": ("OriginalClock_vblank", 0x62c1c0, 57, "9d95ff4b4acc917eb286f0f60ea4d63dafaf5e9b789ca4677b8c1b530579a5cd"),
    "begin_frame": ("OriginalClock_begin_frame", 0x62c200, 116, "612f91f884212d4e6f4f1b8100bad048b5328d379c9ec04f05fa902b0bfc4c4f"),
    "end_frame": ("OriginalClock_end_frame", 0x62c300, 219, "be57f03e016daa5c3ab22612f516a380eccfe075421868e18c9d600e19b3a690"),
    "seconds_per_frame": ("OriginalClock_seconds_per_frame", 0x62c420, 13, "f09d9524ea84f9874699e76bc169f17d715b667e06ae09ba352f911f79054c61"),
    "milliseconds": ("OriginalClock_milliseconds", 0x62c4c0, 50, "d98d3d72225d4f01010e8ee3db9c04b1b22d2703930661eef825a629a18cd470"),
    "integer_conversion": ("OriginalClock_ftol2", 0x66c230, 117, "5333b186c02836974c6f792303aeb2c00d856316b93ccbbe65f51def6ae661b4"),
    "gameplay_delta": ("OriginalClock_gameplay_delta", 0x62c500, 13, "b874145bdf3407a1645aa4a75e9f0e86f11438ad967c39599963c6e74503bf34"),
}


def round_x87_positive(value, precision, rounding):
    """Exact binary rounding of the positive finite operands in this getter."""
    if not value:
        return Fraction(0)
    exponent = value.numerator.bit_length() - value.denominator.bit_length()
    if value < Fraction(2) ** exponent:
        exponent -= 1
    unit = Fraction(2) ** (exponent - precision + 1)
    scaled = value / unit
    whole, remainder = divmod(scaled.numerator, scaled.denominator)
    if ((rounding == 0 and (remainder * 2 > scaled.denominator or
                           (remainder * 2 == scaled.denominator and whole & 1))) or
            (rounding == 2 and remainder)):
        whole += 1
    return whole * unit


def expected_milliseconds(timer_frames, control_word):
    """Validate the original result, never supply or replace a gameplay clock.

    62C4C0 divides float1000 by float60 on x87, multiplies the unsigned low
    timer word, then 66C230 truncates to int64; the caller observes EAX only.
    PC/RC may change when original resource code runs on the retained worker.
    """
    host.require(type(timer_frames) is int and 0 <= timer_frames < 2**64 and
                 type(control_word) is int and 0 <= control_word <= 0xffff,
                 'Original clock timer/control word is invalid')
    precision = {0: 24, 2: 53, 3: 64}.get((control_word >> 8) & 3)
    host.require(precision is not None, 'Original clock uses reserved x87 precision')
    rounding = (control_word >> 10) & 3
    ratio = round_x87_positive(Fraction(1000, 60), precision, rounding)
    ticks = Fraction(timer_frames & 0xffffffff)
    if ticks >= 2**31:
        # The original signed FILD is exact; its +2^32 FADD uses current PC/RC.
        ticks = round_x87_positive(ticks, precision, rounding)
    result = round_x87_positive(ratio * ticks, precision, rounding)
    return (result.numerator // result.denominator) & 0xffffffff


def milliseconds_stub(record):
    """Read FNSTCW around the original getter in one retained-worker command."""
    host.require(type(record) is int and 0x10000 <= record <= 0xfffff000 and record % 4 == 0,
                 'Original clock requires an aligned owned result record')
    pack = lambda value: struct.pack('<I', value)
    code = bytearray(b'\xc7\x05' + pack(record) + pack(2))
    code.extend(b'\xd9\x3d' + pack(record + 72))  # FNSTCW, never FLDCW.
    for source, destination in ((0x8459c0, record + 76), (0x8459c4, record + 80)):
        code.extend(b'\xa1' + pack(source) + b'\xa3' + pack(destination))
    code.extend(b'\xb8' + pack(PINS['milliseconds'][1]) + b'\xff\xd0\xa3' + pack(record + 68))
    code.extend(b'\xd9\x3d' + pack(record + 74))
    code.extend(b'\xc7\x05' + pack(record) + pack(6) + host.STUB)
    return bytes(code)


def validate_max_frames(value):
    if value is None:
        return None  # Continuous gameplay; per-command and host-heartbeat guards remain.
    host.require(type(value) is int and 1 <= value <= 216000,
                 "Original frame limit must be an integer from 1 to 216000 (one native hour)")
    return value


def validate_snapshot(frames, ticks, milliseconds, delta, opened, max_frames=600,
                      *, timer_frames=None, control_word=0x027f):
    limit = validate_max_frames(max_frames)
    if timer_frames is None:
        timer_frames = frames
    host.require(all(type(value) is int and 0 <= value < 2**64 for value in (frames, ticks, timer_frames)) and
                 frames == ticks == timer_frames and (limit is None or frames <= limit),
                 "Original clock counter diverged or exceeded bounded probe")
    expected = expected_milliseconds(timer_frames, control_word)
    host.require(type(milliseconds) is int and milliseconds == expected,
                 f"Original millisecond getter differs from its x87 arithmetic: frames={frames}, milliseconds={milliseconds}, expected={expected}, CW={control_word:#x}")
    host.require(math.isfinite(delta) and abs(delta - 1 / 60) < 1e-12,
                 "Original simulation-clock delta is not one sixtieth of a second")
    return {"native_frame_count": frames, "native_vblank_count": ticks,
            "native_milliseconds": milliseconds, "native_seconds_per_frame": delta,
            "native_timer_frames": timer_frames, "native_x87_control_word": control_word,
            "frame_open": opened}


class FrameClock:
    def __init__(self, api, process, record, deadline, log, max_frames=600):
        self.max_frames = validate_max_frames(max_frames)
        self.api, self.process, self.record = api, process, record
        self.deadline, self.log, self.open = deadline, log, False
        for name, address, size, digest in PINS.values():
            host.require(hashlib.sha256(self.read(address, size)).hexdigest() == digest,
                         "Original clock entry differs: " + name)
        host.require(self.value(0x6d8684, "I") == 60 and self.value(0x6d86a4, "f") == 60 and
                     self.value(0x8459d0, "Q") == self.value(0x8459c8, "Q") == 0,
                     "Original clock is already running or has another frequency")
        self.call("reset")
        log("frame_clock_initialized", **self.snapshot(), host_wrote_clock_data=False)

    def read(self, address, size):
        return host.remote_read(self.api, self.process, address, size)

    def value(self, address, kind):
        return struct.unpack("<" + kind, self.read(address, struct.calcsize("<" + kind)))[0]

    def call(self, key):
        return host.native_call(self.api, self.process, PINS[key], [], self.record, self.deadline, self.log)

    def milliseconds(self):
        from native_worker import retained_worker
        worker = retained_worker(self.api, self.process)
        worker.check_call_access()
        for key in ('milliseconds', 'integer_conversion'):
            name, address, count, digest = PINS[key]
            host.require(hashlib.sha256(self.read(address, count)).hexdigest() == digest,
                         'Original clock entry differs: ' + name)
        host.require(self.value(0x68e3d0, 'f') == 1000 and self.value(0x6d86a4, 'f') == 60 and
                     self.value(0x68e3c4, 'f') == 2**32, 'Original millisecond constants changed')
        host.remote_write(self.api, self.process, self.record + 36, bytes(48))
        host.remote_write(self.api, self.process, self.record + 452, bytes(24))
        code = milliseconds_stub(self.record)
        address = worker.prepare_command(code)
        waited, returned = worker.execute(address, self.deadline)
        status = struct.unpack('<18I', self.read(self.record, 72))
        host.require(waited == 0 and returned == 0 and status[0] == 6,
                     f'Original clock getter failed: status={status[0]} EIP={status[10]:#x}')
        host.require(self.read(address, len(code)) == code, 'Owned clock getter glue changed')
        before, after, timer = struct.unpack('<HHQ', self.read(self.record + 72, 12))
        host.require(before == after, 'Original millisecond getter changed its x87 control word')
        return status[17], timer, before

    def snapshot(self):
        # The pinned original getter computes this division on the x87 stack.
        # Inspect its real data without replacing it or leaking x87 return slots.
        delta = self.value(0x845930, "d") / self.value(0x6d86a4, "f")
        frames, ticks = self.value(0x8459d0, "Q"), self.value(0x8459c8, "Q")
        milliseconds, timer, control_word = self.milliseconds()
        snapshot = validate_snapshot(frames, ticks, milliseconds, delta, self.open, self.max_frames,
                                     timer_frames=timer, control_word=control_word)
        # Physics has a separate, original scaled getter (e.g. Focus). Do not
        # mistake the simulation tick duration above for that gameplay delta.
        scale, next_scale = self.value(0x6d869c, "f"), self.value(0x6d86a0, "f")
        host.require(all(math.isfinite(value) and 0 <= value <= 100 for value in (scale, next_scale)),
                     "Original gameplay time scale is invalid")
        base_delta = self.value(0x690660, "f")
        host.require(base_delta == struct.unpack("<f", struct.pack("<f", 1 / 60))[0],
                     "Original gameplay delta constant changed")
        snapshot.update(native_time_scale=scale, native_next_time_scale=next_scale,
                        native_gameplay_seconds_per_frame=scale * base_delta)
        return snapshot

    def begin_frame(self):
        host.require(not self.open, "Original clock frame is already open")
        host.require(self.max_frames is None or self.value(0x8459d0, "Q") < self.max_frames,
                     "Original clock reached its bounded frame limit")
        self.call("vblank")
        self.call("begin_frame")
        self.open = True

    def end_frame(self):
        host.require(self.open, "Original clock frame was not opened")
        self.call("end_frame")
        self.open = False
        return self.snapshot()
