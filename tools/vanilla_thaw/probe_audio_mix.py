"""Presentation-only pitch and two original synthesized transition sounds.

The stock mixer still owns all sample selection, gain, pan, voice allocation
and authored pitch. Its desired-frequency shadow is read, never modified.
"""
import hashlib
import math
import struct

import probe_audio
from probe_audio_frame import PLATFORM_VOICES, ACTIVE_VOICES, locked
import probe_loader as host

# Whole original SetVoiceParameters writes the desired rate here immediately
# before its DirectSound SetFrequency call. This stays the unmodified reference
# even when presentation temporarily changes the real buffer's playback rate.
PINS = (
    (0x443e9d, 37, 'ee5b3ce36b69e248fa6ee8b41abb2ea2c5d3bad7f238686bdcd9b433f3e512cf'),
    (0x443f30, 675, '1594d479d081e05a0a92f47767c7162e331f903efe1928d41a65165c4bf3173c'),
)


def pitch_scale(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not .05 <= value <= 1:
        raise ValueError('Audio time scale must be finite and between .05 and one')
    return float(value)


def voice_rates(raw, count, scale, descriptors):
    """Validate the complete native snapshot before touching any COM buffer."""
    pitch_scale(scale)
    host.require(type(count) is int and 1 <= count <= 48 and len(raw) == count * 24,
                 'Audio mix voice extent differs')
    result, buffers = [], set()
    for index in range(count):
        buffer, _, base, desired, active, descriptor = struct.unpack_from('<6I', raw, index * 24)
        host.require((active & 255) in (0, 1) and (not buffer or buffer >= 0x10000 and buffer % 4 == 0),
                     'Audio mix native voice ownership differs')
        if not active & 255:
            continue
        host.require(buffer and buffer not in buffers and descriptor in descriptors and
                     100 <= base <= 200000 and 100 <= desired <= 200000,
                     'Audio mix active voice frequency/sample identity differs')
        buffers.add(buffer)
        result.append((buffer, max(100, min(200000, round(desired * scale)))))
    return result


class PitchControl:
    def __init__(self, frame):
        self.frame, self.system = frame, frame.system
        self.scale = 1.
        for address, size, digest in PINS:
            host.require(hashlib.sha256(self.system.read(address, size)).hexdigest() == digest,
                         'Original audio desired-frequency evidence differs')
        table = self.system.read(0x6e0f90, frame.sample_count * 32)
        self.descriptors = {struct.unpack_from('<I', table, index * 32 + 20)[0]
                            for index in range(frame.sample_count)} - {0}
        host.require(self.descriptors, 'Audio mix requires the original sample bank')

    def apply(self, scale, *, after_frame=False):
        scale = pitch_scale(scale)
        if scale == self.scale and (scale == 1 or not after_frame):
            return 0
        s = self.system
        with locked(s):
            host.require(all(s.word(address) == owner for address, owner in self.frame.owners.items()) and
                         s.word(probe_audio.SAMPLES) == self.frame.sample_count,
                         'Audio mix manager/device/bank ownership changed')
            count = s.word(probe_audio.VOICES)
            host.require(1 <= count <= 48, 'Audio mix voice count is invalid')
            voices = voice_rates(s.read(PLATFORM_VOICES, count * 24), count, scale, self.descriptors)
            host.require(len(voices) == s.word(ACTIVE_VOICES), 'Audio mix active voice count differs')
            for buffer, rate in voices:
                host.require(s.com('AudioMix:SetFrequency', buffer, 17, (rate,)) == 0,
                             'DirectSound rejected presentation pitch')
        self.scale = scale
        return len(voices)


def transition_pcm(entering, *, sample_rate=22050):
    """220 ms downward/upward air sweep, deterministic mono16 PCM, no assets."""
    if type(entering) is not bool or type(sample_rate) is not int or not 8000 <= sample_rate <= 48000:
        raise ValueError('Invalid transition sound parameters')
    count = round(sample_rate * .22)
    pcm, phase, filtered, noise = bytearray(count * 2), 0., 0., 0x51f15e
    for index in range(count):
        t = index / (count - 1)
        frequency = 560 - 430 * t if entering else 130 + 430 * t
        phase += 2 * math.pi * frequency / sample_rate
        noise = (1664525 * noise + 1013904223) & 0xffffffff
        filtered += .14 * ((noise / 0x80000000 - 1) - filtered)
        envelope = math.sin(math.pi * t) ** 2
        value = envelope * (math.sin(phase) * .10 + filtered * .20)
        struct.pack_into('<h', pcm, index * 2, round(value * 32767))
    return bytes(pcm)


class Transitions:
    """Two bounded extra DirectSound buffers; never part of the stock voice pool."""
    def __init__(self, system, device):
        self.system, self.device, self.buffers = system, device, {}
        self.scratch = system.allocate(bytes(128))
        try:
            for kind in (1, 2):
                self.buffers[kind] = self._create(transition_pcm(kind == 1))
        except BaseException:
            self.close()
            raise

    def _create(self, pcm):
        s, at = self.system, self.scratch
        # x86 DSBUFFERDESC36; CTRLVOLUME, immutable mono PCM WAVEFORMATEX18.
        s.write(at, struct.pack('<5I16s', 36, 0x80, len(pcm), 0, at + 40, bytes(16)))
        s.write(at + 40, struct.pack('<HHIIHHH', 1, 1, 22050, 44100, 2, 16, 0))
        s.write(at + 64, bytes(20))
        host.require(s.com('AudioMix:CreateSoundBuffer', self.device, 3, (at, at + 64, 0)) == 0,
                     'Cannot create transition sound buffer')
        buffer = s.word(at + 64)
        host.require(buffer >= 0x10000 and buffer % 4 == 0, 'Transition buffer is invalid')
        try:
            host.require(s.com('AudioMix:Lock', buffer, 11, (0, len(pcm), at + 68, at + 72, at + 76, at + 80, 0)) == 0,
                         'Cannot lock transition PCM')
            first, first_size, second, second_size = struct.unpack('<4I', s.read(at + 68, 16))
            try:
                host.require(first_size + second_size == len(pcm) and
                             all(size == 0 or 0x10000 <= pointer <= 0x100000000 - size
                                 for pointer, size in ((first, first_size), (second, second_size))),
                             'Transition PCM lock spans differ')
                if first_size:
                    s.write(first, pcm[:first_size])
                if second_size:
                    s.write(second, pcm[first_size:])
            except BaseException:
                try:
                    s.com('AudioMix:Unlock', buffer, 19, (first, first_size, second, second_size))
                except Exception:
                    pass  # Keep the original PCM validation/write failure.
                raise
            else:
                host.require(s.com('AudioMix:Unlock', buffer, 19, (first, first_size, second, second_size)) == 0,
                             'Cannot unlock transition PCM')
            return buffer
        except BaseException:
            try:
                s.com('AudioMix:Release', buffer, 2)
            except Exception:
                pass  # Preserve the real PCM/setup failure; host teardown owns the allocation.
            raise

    def ensure_session(self):
        # Prime Windows' lazy process session using only our own buffer. Stop
        # before restoring its gain, including a failed Play; stock voices stay untouched.
        s, buffer = self.system, self.buffers[1]
        host.require(s.com('AudioMix:PrimeMute', buffer, 15, ((-10000) & 0xffffffff,)) == 0,
                     'Cannot silence audio session priming')
        try:
            host.require(s.com('AudioMix:PrimePlay', buffer, 12, (0, 0, 0)) == 0,
                         'Cannot prime audio session')
        except BaseException:
            try:
                s.com('AudioMix:PrimeStop', buffer, 18)
            except Exception:
                pass  # The muted buffer is released by constructor cleanup.
            raise
        host.require(s.com('AudioMix:PrimeStop', buffer, 18) == 0,
                     'Cannot stop audio session priming')
        host.require(s.com('AudioMix:PrimeRestore', buffer, 15, (0,)) == 0,
                     'Cannot restore transition buffer volume')

    def stop(self):
        for buffer in self.buffers.values():
            host.require(self.system.com('AudioMix:Stop', buffer, 18) == 0, 'Cannot stop transition sound')

    def play(self, kind):
        if kind not in self.buffers:
            raise ValueError('Unknown transition sound')
        self.stop()
        buffer = self.buffers[kind]
        host.require(self.system.com('AudioMix:Rewind', buffer, 13, (0,)) == 0 and
                     self.system.com('AudioMix:Play', buffer, 12, (0, 0, 0)) == 0,
                     'Cannot play transition sound')

    def close(self):
        errors = []
        for buffer in self.buffers.values():
            try:
                host.require(self.system.com('AudioMix:Stop', buffer, 18) == 0, 'Cannot stop transition sound')
            except Exception as error:
                errors.append(str(error))
            try:
                self.system.com('AudioMix:Release', buffer, 2)
            except Exception as error:
                errors.append(str(error))
        self.buffers.clear()
        return errors
