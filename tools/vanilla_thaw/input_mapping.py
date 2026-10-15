"""Pure held-input mapping into the existing original THAW packet transport.

No OS input polling, edge synthesis, native execution or camera writes. A caller
supplies one simultaneous snapshot only while it owns gameplay input. Original
decoders own pressure, stick deadzones, button edges and timestamps.

Button identities: stock CPadCtor 596580, CPadUpdate 596DD0, decode table 6BDE00
(whole hashes are ACTION_PINS in probe_frame_input). START is named by the ctor
but never updated by this packet path: bit 0x800 is deliberately unsupported.
Axes are right-stick X/Y, left-stick X/Y; 0/128/255 are negative/neutral/positive.

The action/key preset comes from the user's previous reTHAWed INI and our old
skater_input.rs, not a claim about stock launcher defaults. The stock engine
still decides what these buttons do in each gameplay state. SDL scan codes are
physical key identifiers, independent of text layout and Num Lock state.
"""
from collections.abc import Mapping
from types import MappingProxyType
from typing import NamedTuple

from probe_frame_input import packet


BUTTON_MASKS = MappingProxyType({
    "UP": 0x1000, "DOWN": 0x4000, "LEFT": 0x8000, "RIGHT": 0x2000,
    "TRIANGLE": 0x10, "CIRCLE": 0x20, "X": 0x40, "SQUARE": 0x80,
    "L1": 4, "L2": 1, "L3": 0x200, "R1": 8, "R2": 2, "R3": 0x400,
    "SELECT": 0x100, "BLACK": 0x10000, "WHITE": 0x20000,
})
ACTION_MASKS = MappingProxyType({
    "Ollie": 0x40, "Grab": 0x20, "Flip": 0x80, "Grind": 0x10,
    "Nollie": 0x20001, "Caveman": 0x10002,
    "SpinLeft": 4, "SpinRight": 8, "Focus": 0x200,
    "SpineTransfer": 0x0c, "ViewToggle": 0x100,
    "ItemsUp": 0x1000, "ItemsDown": 0x4000,
    "ItemsLeft": 0x8000, "ItemsRight": 0x2000,
})
AXIS_ACTIONS = (("CameraLeft", "CameraRight"), ("CameraUp", "CameraDown"),
                ("Left", "Right"), ("Forward", "Backward"))
KNOWN_ACTIONS = frozenset(ACTION_MASKS) | frozenset(a for pair in AXIS_ACTIONS for a in pair) | {"CameraRecenter"}
PREVIOUS_USER_BINDINGS = MappingProxyType({
    "Forward": 26, "Backward": 22, "Left": 4, "Right": 7,  # W S A D
    "CameraUp": 87, "CameraDown": 86, "CameraLeft": 84, "CameraRight": 85,
    "Ollie": 93, "Grab": 94, "Flip": 92, "Grind": 96,  # Num5/6/4/8
    "Nollie": 98, "Caveman": 8, "SpinLeft": 95, "SpinRight": 97,
    "Focus": 156, "SpineTransfer": 44, "ViewToggle": 28,
    "ItemsUp": 12, "ItemsDown": 13, "ItemsLeft": 14, "ItemsRight": 15,
})
# Keep the saved CameraRecenter binding: walking uses the host camera action,
# skating sends the original L1|R1 transfer chord. Num7+Num9 remains available.
# Focus 156 is SDL_CLEAR / Windows VK_OEM_CLEAR (0xFE), never keypad5 (VK_CLEAR).
# J belongs to the GTA mode toggle, so do not also send ItemsDown to THAW.
GTA_BINDINGS = MappingProxyType({
    **{a: key for a, key in PREVIOUS_USER_BINDINGS.items() if a not in ("SpineTransfer", "ItemsDown")},
    "CameraRecenter": 44,
})


class MappedInput(NamedTuple):
    packet: bytes
    recenter_camera: bool  # Held level; host camera owns edge/context handling.
    skitch_requested: bool = False


def _names(values, allowed, label):
    if isinstance(values, (str, bytes)):
        raise ValueError(label + " must be a collection of names")
    result = set(values)
    if any(type(value) is not str or value not in allowed for value in result):
        raise ValueError("Unsupported " + label)
    return result


def controller_packet(buttons=(), axes=(128, 128, 128, 128)):
    """Encode verified controller buttons; Start and guessed bits are rejected."""
    mask = 0
    for button in _names(buttons, BUTTON_MASKS, "controller buttons"):
        mask |= BUTTON_MASKS[button]
    return packet(axes, mask)


def map_actions(actions, *, skating=False):
    held = _names(actions, KNOWN_ACTIONS, "input actions")
    if type(skating) is not bool:
        raise ValueError('Skating context must be a boolean')
    skitch_chord = skating and {'Ollie', 'Grind', 'CameraRecenter'} <= held
    skitch_requested = skitch_chord
    skitch_up = skitch_chord and not {'Backward', 'ItemsDown'} & held
    if skitch_chord:
        held.difference_update(('Ollie', 'Grind', 'CameraRecenter', 'SpineTransfer'))
    if skating and 'CameraRecenter' in held:
        held.remove('CameraRecenter')
        held.add('SpineTransfer')
    axes = tuple(128 if (negative in held) == (positive in held)
                 else 0 if negative in held else 255 for negative, positive in AXIS_ACTIONS)
    if 'CameraRecenter' in held:
        # A held look key must not immediately undo the original camera flush.
        axes = (128, 128, *axes[2:])
    mask = 0
    for action in held:
        mask |= ACTION_MASKS.get(action, 0)
    if skitch_up:
        # Stock check_for_skitch consumes held UP. Only the chord supplies it;
        # VehicleWorld separately gates eligible rear hooks, leaving W intact.
        mask |= BUTTON_MASKS['UP']
    return MappedInput(packet(axes, mask), "CameraRecenter" in held, bool(skitch_requested))


def _binding_key(code):
    # Same physical-key boundary as existing portable settings: reserve
    # Escape, F1/F7/F8 and OS/Alt keys for the adapter, never send them to THAW.
    return type(code) is int and (4 <= code <= 40 or 42 <= code <= 49 or
        51 <= code <= 57 or 59 <= code <= 63 or 66 <= code <= 69 or
        73 <= code <= 100 or code in (156, 224, 225, 228, 229))


def map_keys(held_scancodes, bindings=GTA_BINDINGS, *, skating=False):
    """Map a physical SDL snapshot; unbound keys do nothing, chords stay intact.

    A custom binding map replaces the preset. Repeated values intentionally
    permit chords. Missing actions are unbound; invalid/reserved bindings fail.
    This helper must not be used to fabricate a neutral frame on focus loss:
    releasing a held Ollie has gameplay meaning. The adapter must suspend or
    relinquish input ownership instead.
    """
    if not isinstance(bindings, Mapping) or any(
        action not in KNOWN_ACTIONS or not _binding_key(code) for action, code in bindings.items()
    ):
        raise ValueError("Unsupported action or reserved physical binding")
    held = set(held_scancodes)
    if any(type(code) is not int or not 0 <= code < 512 for code in held):
        raise ValueError("Expected physical SDL scan codes, not text or virtual keys")
    return map_actions((action for action, code in bindings.items() if code in held), skating=skating)
