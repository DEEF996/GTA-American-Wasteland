"""Bridge verified GTA effect controls to the owned THAW audio presentation.

Call update from the idle host loop, after_frame after each NEW source frame,
and suspend on lease loss. None of these operations changes simulation time.
"""
from audio_session import SessionGain, scalar
from probe_audio_mix import PitchControl, Transitions, pitch_scale
import probe_audio


class AudioMix:
    def __init__(self, frame, *, gain_factory=SessionGain, pitch_factory=PitchControl,
                 transition_factory=Transitions):
        frame.preflight()
        self.frame, self.gain, self.pitch, self.transitions = frame, None, None, None
        self.scale, self.volume, self.event_sequence = 1., 0., None
        self.suspended, self.closed = True, False
        self.host_pid = frame.system.call('kernel32.dll', 'GetCurrentProcessId')
        if type(self.host_pid) is not int or not 0 < self.host_pid <= 0xffffffff:
            raise RuntimeError('Audio mix requires the verified owned native host PID')
        try:
            self.pitch = pitch_factory(frame)
            self.transitions = transition_factory(frame.system, frame.owners[probe_audio.DEVICE])
            self.transitions.ensure_session()
            self.gain = gain_factory(self.host_pid)
            self.gain.set(0.)
        except BaseException:
            self.close()
            raise

    def _check(self):
        if self.closed:
            raise RuntimeError('Audio mix is closed')

    def update(self, sfx_volume, time_scale, *, event_sequence=0, event_kind=0):
        self._check()
        volume, scale = scalar(sfx_volume), pitch_scale(time_scale)
        if type(event_sequence) is not int or not 0 <= event_sequence <= 0xffffffffffffffff or type(event_kind) is not int or event_kind not in (0, 1, 2, 3):
            raise ValueError('Invalid audio effect event')
        # Validate every control before changing either the mixer or the host.
        self.gain.set(volume)
        self.pitch.apply(scale)
        changed = self.event_sequence is not None and event_sequence > self.event_sequence
        self.event_sequence = event_sequence
        self.volume, self.scale, self.suspended = volume, scale, False
        if changed and volume and event_kind in (1, 2):
            self.transitions.play(event_kind)

    def after_frame(self):
        self._check()
        if not self.suspended:
            # The stock update may have retuned or reused a buffer since our
            # last poll. Reapply to its NEW desired rate, never to our own rate.
            return self.pitch.apply(self.scale, after_frame=True)
        return 0

    def suspend(self):
        self._check()
        if not self.suspended:
            self.gain.set(0.)
            self.pitch.apply(1.)
            self.transitions.stop()
            self.scale, self.volume, self.suspended = 1., 0., True
        # An event accumulated while ownership was absent is not a fresh cue.
        self.event_sequence = None

    def close(self):
        if self.closed:
            return []
        self.closed = True
        errors = []
        if self.pitch:
            try:
                self.pitch.apply(1.)
            except Exception as error:
                errors.append(str(error))
        for child in (self.transitions, self.gain):
            if child:
                try:
                    errors.extend(child.close() or [])
                except Exception as error:
                    errors.append(str(error))
        return errors
