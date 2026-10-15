"""Assign the owned original DirectSound device to the verified visible GTA window."""
import ctypes as C
from ctypes import wintypes as W


def visible_game_window(pid, api=None):
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    if api is None:
        api = C.WinDLL('user32', use_last_error=True)
        for name, arguments, result in (
            ('EnumWindows', [callback_type, W.LPARAM], W.BOOL),
            ('GetWindowThreadProcessId', [W.HWND, C.POINTER(W.DWORD)], W.DWORD),
            ('IsWindowVisible', [W.HWND], W.BOOL),
            ('GetWindow', [W.HWND, W.UINT], W.HWND),
        ):
            function = getattr(api, name)
            function.argtypes, function.restype = arguments, result
    windows = []

    def visit(window, _):
        owner = W.DWORD()
        if (api.GetWindowThreadProcessId(window, C.byref(owner)) and owner.value == pid
                and api.IsWindowVisible(window) and not api.GetWindow(window, 4)):
            windows.append(window)
        return True

    if not api.EnumWindows(callback_type(visit), 0):
        raise C.WinError(C.get_last_error())
    if len(windows) != 1:
        raise ValueError('Audio target needs one visible top-level window of the verified GTA process')
    return windows[0]


def bind_audio_to_game(system, device, observer, *, windows_api=None):
    observer.read()  # Validate the existing process/mapping, even when GTA is in the background.
    window = system.call('user32.dll', 'GetForegroundWindow')
    owner = system.allocate(bytes(4))

    def valid(candidate):
        return (system.call('user32.dll', 'GetWindowThreadProcessId', (candidate, owner))
                and system.word(owner) == observer.pid
                and system.call('user32.dll', 'IsWindowVisible', (candidate,))
                and not system.call('user32.dll', 'GetWindow', (candidate, 4)))

    if not valid(window):
        window = visible_game_window(observer.pid, windows_api)
    # Enumeration and cooperative binding use different processes; recheck the chosen HWND.
    if not valid(window):
        raise ValueError('Audio target must be a visible window of the verified GTA process')
    status = system.com('IDirectSound8::SetCooperativeLevel', device, 6, (window, 2))
    if status != 0:
        raise RuntimeError('Original DirectSound device rejected the GTA cooperative window')
    return dict(window=window, pid=observer.pid, cooperative_level=2, result=status,
                game_image_verified=True, hidden_THAW_window_unchanged=True)
