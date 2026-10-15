"""Poll only the GTA skating bindings while the verified game has focus.

No hook, event log, text capture, or synthetic keys. Letters use physical PC
scan codes in the foreground layout. This initial keypad preset requires Num
Lock; navigation-key aliases are deliberately not treated as keypad keys.
"""
import ctypes as C
from ctypes import wintypes as W
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vanilla_thaw'))
from input_mapping import GTA_BINDINGS, KNOWN_ACTIONS, _binding_key, map_keys

# SDL physical key -> PC set-1 scan code, or a dedicated keypad virtual key.
PHYSICAL = dict(zip(range(4, 30), (0x1e,0x30,0x2e,0x20,0x12,0x21,0x22,0x23,
    0x17,0x24,0x25,0x26,0x32,0x31,0x18,0x19,0x10,0x13,0x1f,0x14,0x16,
    0x2f,0x11,0x2d,0x15,0x2c)))
PHYSICAL.update({code: code-28 for code in range(30, 40)})
PHYSICAL.update({40:0x1c,42:0x0e,43:0x0f,44:0x39,45:0x0c,46:0x0d,47:0x1a,
    48:0x1b,49:0x2b,51:0x27,52:0x28,53:0x29,54:0x33,55:0x34,56:0x35,
    57:0x3a,100:0x56,224:0x1d,225:0x2a,228:0xe01d,229:0x36})
KEYPAD = {84: 0x6f, 85: 0x6a, 86: 0x6d, 87: 0x6b,
          88: 0x0d, 89: 0x61, 90: 0x62, 91: 0x63, 92: 0x64, 93: 0x65,
          94: 0x66, 95: 0x67, 96: 0x68, 97: 0x69, 98: 0x60, 99: 0x6e}
VIRTUAL = {**KEYPAD, 73:0x2d,74:0x24,75:0x21,76:0x2e,77:0x23,78:0x22,
           79:0x27,80:0x25,81:0x28,82:0x26,83:0x90,156:0xfe,
           **{code:code-58+0x70 for code in range(58,70)}}
SUPPORTED_KEYS = frozenset(code for code in PHYSICAL.keys() | VIRTUAL.keys()
                          if _binding_key(code) and code not in (13, 41, 58, 64, 65, 83, 88))


def validate_bindings(bindings):
    if not isinstance(bindings, dict) or any(action not in KNOWN_ACTIONS or
            type(code) is not int or code not in SUPPORTED_KEYS for action,code in bindings.items()):
        raise ValueError('Unsupported action or reserved physical binding')
    if len(set(bindings.values())) != len(bindings):
        raise ValueError('A key cannot be assigned to more than one action')
    return dict(bindings)


class InputUnavailable(ValueError):
    """Normal scene/focus/keypad availability change, not a binding error."""


class KeyboardInput:
    def __init__(self, observer, api=None):
        self.observer = observer
        self.bindings = dict(GTA_BINDINGS)
        self._skitch_keys = frozenset()
        self.api = api if api is not None else C.WinDLL('user32', use_last_error=True)
        if api is None:
            for name, args, result in (
                ('GetForegroundWindow', [], W.HWND),
                ('GetWindowThreadProcessId', [W.HWND, C.POINTER(W.DWORD)], W.DWORD),
                ('GetKeyboardLayout', [W.DWORD], W.HANDLE),
                ('MapVirtualKeyExW', [W.UINT, W.UINT, W.HANDLE], W.UINT),
                ('GetAsyncKeyState', [C.c_int], C.c_short),
            ):
                fn = getattr(self.api, name)
                fn.argtypes, fn.restype = args, result

    def read(self, *, skating=False):
        state = self.observer.read()
        if not state['control_eligible'] or not state['flags'] & 8:
            raise InputUnavailable('Skating input requires the active GTA scene')
        window = self.api.GetForegroundWindow()
        owner = W.DWORD()
        thread = self.api.GetWindowThreadProcessId(window, C.byref(owner))
        if not thread or owner.value != self.observer.pid:
            raise InputUnavailable('GTA lost keyboard focus')
        # Toggle state is sampled on the actual GTA input thread. GetKeyState
        # on this Python thread would instead read its own message-queue state.
        if not state['flags'] & 128:
            raise InputUnavailable('Enable Num Lock for the skating keypad preset')
        layout = self.api.GetKeyboardLayout(thread)
        held = set()
        for code in set(self.bindings.values()):
            key = (self.api.MapVirtualKeyExW(PHYSICAL[code], 3, layout)
                   if code in PHYSICAL else VIRTUAL.get(code, 0))
            if not key:
                raise ValueError('Skating binding has no supported physical key')
            if self.api.GetAsyncKeyState(key) & 0x8000:
                held.add(code)
        if self.api.GetForegroundWindow() != window:
            raise InputUnavailable('GTA lost keyboard focus during input sampling')
        chord = frozenset(self.bindings.get(action) for action in ('Ollie', 'Grind', 'CameraRecenter'))
        if skating and None not in chord and chord <= held:
            self._skitch_keys = chord
        elif self._skitch_keys:
            # Releasing Space a poll before Num5 must not begin a new Ollie.
            remaining = bool(held & self._skitch_keys)
            held.difference_update(self._skitch_keys)
            if not remaining:
                self._skitch_keys = frozenset()
        return map_keys(held, self.bindings, skating=skating)
