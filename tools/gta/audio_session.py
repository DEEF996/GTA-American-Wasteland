"""Master gain for only the owned audio host's Windows audio sessions.

CoreAudio mixes after the original DirectSound voices, so mute includes voices
already playing and never changes their authored gain, pan, priority or PCM.
No dependency on pycaw/comtypes; COM ownership stays on the creating thread.
"""
import ctypes as C
import math
import threading
import time
import uuid


def scalar(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('Audio gain must be finite and between zero and one')
    return float(value)


def _guid(value):
    return (C.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le)


def _method(pointer, slot, *arguments):
    table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(C.c_long, C.c_void_p, *arguments)(table[slot])


def _check(result, label):
    if result < 0:
        raise OSError(f'{label} failed: HRESULT 0x{result & 0xffffffff:08x}')


def _release(pointer):
    if pointer:
        _method(pointer, 2)(pointer)


def _query(pointer, iid):
    result = C.c_void_p()
    identity = _guid(iid)
    _check(_method(pointer, 0, C.c_void_p, C.POINTER(C.c_void_p))(
        pointer, C.byref(identity), C.byref(result)), 'CoreAudio QueryInterface')
    return result.value


class _Volume:
    def __init__(self, pointer):
        self.pointer = pointer

    def get(self):
        value, muted = C.c_float(), C.c_int()
        _check(_method(self.pointer, 4, C.POINTER(C.c_float))(self.pointer, C.byref(value)), 'GetMasterVolume')
        _check(_method(self.pointer, 6, C.POINTER(C.c_int))(self.pointer, C.byref(muted)), 'GetMute')
        return scalar(value.value), bool(muted.value)

    def set(self, value, muted):
        # Close the mute gate before reducing gain; open it only after setting
        # the new gain. There is no frame in which zero requests full volume.
        if muted:
            _check(_method(self.pointer, 5, C.c_int, C.c_void_p)(self.pointer, 1, None), 'SetMute')
        _check(_method(self.pointer, 3, C.c_float, C.c_void_p)(self.pointer, value, None), 'SetMasterVolume')
        if not muted:
            _check(_method(self.pointer, 5, C.c_int, C.c_void_p)(self.pointer, 0, None), 'SetMute')

    def close(self):
        pointer, self.pointer = self.pointer, None
        _release(pointer)


class CoreAudio:
    def __init__(self):
        self.thread = threading.get_ident()
        self.ole = C.WinDLL('ole32')
        self.ole.CoInitializeEx.argtypes = [C.c_void_p, C.c_uint]
        self.ole.CoInitializeEx.restype = C.c_long
        result = self.ole.CoInitializeEx(None, 0)
        # An existing STA is usable; don't uninitialize somebody else's COM.
        self.initialized = result in (0, 1)
        if result & 0xffffffff != 0x80010106:
            _check(result, 'CoInitializeEx')
        self.ole.CoCreateInstance.argtypes = [C.c_void_p, C.c_void_p, C.c_uint, C.c_void_p, C.POINTER(C.c_void_p)]
        self.ole.CoCreateInstance.restype = C.c_long
        self.ole.CoUninitialize.argtypes = []
        self.ole.CoUninitialize.restype = None

    def _check_thread(self):
        if threading.get_ident() != self.thread:
            raise RuntimeError('Audio sessions must stay on their creating thread')

    def sessions(self, pid):
        self._check_thread()
        clsid = _guid('bcde0395-e52f-467c-8e3d-c4579291692e')
        iid = _guid('a95664d2-9614-4f35-a746-de8db63617e6')
        manager_iid = _guid('77aa99a0-1bd6-484f-8bc7-2c654c9a9b6f')
        enumerator, devices = C.c_void_p(), C.c_void_p()
        found = []
        try:
            _check(self.ole.CoCreateInstance(C.byref(clsid), None, 1, C.byref(iid), C.byref(enumerator)), 'MMDeviceEnumerator')
            _check(_method(enumerator, 3, C.c_int, C.c_uint, C.POINTER(C.c_void_p))(
                enumerator, 0, 1, C.byref(devices)), 'EnumAudioEndpoints')
            count = C.c_uint()
            _check(_method(devices, 3, C.POINTER(C.c_uint))(devices, C.byref(count)), 'Get device count')
            if count.value > 64:
                raise RuntimeError('Unexpected audio endpoint count')
            for index in range(count.value):
                device, manager, sessions = C.c_void_p(), C.c_void_p(), C.c_void_p()
                try:
                    _check(_method(devices, 4, C.c_uint, C.POINTER(C.c_void_p))(devices, index, C.byref(device)), 'Get audio endpoint')
                    _check(_method(device, 3, C.c_void_p, C.c_uint, C.c_void_p, C.POINTER(C.c_void_p))(
                        device, C.byref(manager_iid), 23, None, C.byref(manager)), 'Activate audio sessions')
                    _check(_method(manager, 5, C.POINTER(C.c_void_p))(manager, C.byref(sessions)), 'Enumerate audio sessions')
                    length = C.c_int()
                    _check(_method(sessions, 3, C.POINTER(C.c_int))(sessions, C.byref(length)), 'Get session count')
                    if not 0 <= length.value <= 512:
                        raise RuntimeError('Unexpected audio session count')
                    for number in range(length.value):
                        session, identity = C.c_void_p(), None
                        try:
                            _check(_method(sessions, 4, C.c_int, C.POINTER(C.c_void_p))(
                                sessions, number, C.byref(session)), 'Get audio session')
                            identity = _query(session, 'bfb7ff88-7239-4fc9-8fa2-07c950be9c6d')
                            owner = C.c_uint()
                            result = _method(identity, 14, C.POINTER(C.c_uint))(identity, C.byref(owner))
                            _check(result, 'Get audio session process')
                            # S_OK only: a cross-process/system session is never ours.
                            if result == 0 and owner.value == pid:
                                found.append(_Volume(_query(session, '87ce5498-68d6-44e5-9215-6da47ef883d8')))
                        finally:
                            _release(identity)
                            _release(session)
                finally:
                    _release(sessions)
                    _release(manager)
                    _release(device)
            return found
        except BaseException:
            for volume in found:
                volume.close()
            raise
        finally:
            _release(devices)
            _release(enumerator)

    def close(self):
        self._check_thread()
        if self.initialized:
            self.initialized = False
            self.ole.CoUninitialize()


class SessionGain:
    def __init__(self, pid, *, backend=None):
        if type(pid) is not int or not 0 < pid <= 0xffffffff:
            raise ValueError('Audio gain requires the owned host PID')
        self.backend = CoreAudio() if backend is None else backend
        self.pid, self.volumes, self.applied = pid, [], None
        try:
            # DirectSound can publish its process session asynchronously after
            # the first silent Play. Retry absence only; COM failures stay fatal.
            deadline = time.monotonic() + .25
            for _ in range(26):
                self.volumes = [[volume, None, None] for volume in self.backend.sessions(pid)]
                remaining = deadline - time.monotonic()
                if self.volumes or remaining <= 0:
                    break
                time.sleep(min(.01, remaining))
            for row in self.volumes:
                row[1] = row[0].get()
            if not self.volumes:
                raise OSError('The owned DirectSound host has no CoreAudio session')
        except BaseException:
            try:
                self.close()
            except Exception:
                pass
            raise

    def set(self, value):
        value = scalar(value)
        if not self.volumes:
            raise RuntimeError('Audio gain is closed')
        if value != self.applied:
            for row in self.volumes:
                volume = row[0]
                # Record the intended ownership before COM: SetMute may have
                # succeeded even if the following SetMasterVolume failed.
                row[2] = value
                volume.set(value, value == 0)
            self.applied = value

    def close(self):
        errors = []
        for volume, previous, applied in self.volumes:
            try:
                current = volume.get()
                if previous is not None and applied is not None and abs(current[0] - applied) < 1e-6 and current[1] == (applied == 0):
                    volume.set(*previous)
            except Exception as error:
                errors.append(str(error))
            finally:
                volume.close()
        self.volumes.clear()
        self.backend.close()
        # Teardown must not replace the source worker's original exception.
        return errors
