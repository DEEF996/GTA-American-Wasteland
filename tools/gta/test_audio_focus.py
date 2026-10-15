from types import SimpleNamespace
import unittest
from unittest.mock import patch
from audio_focus import bind_audio_to_game


class AudioFocusTests(unittest.TestCase):
    def system(self, owner=17, visible=True, result=0, *, windows=None):
        calls = []
        selected_owner = owner
        windows = windows if windows is not None else {123: (owner, visible, 0)}
        def call(module, name, args=()):
            nonlocal selected_owner
            if name == 'GetForegroundWindow': return 123
            window_owner, shown, parent = windows.get(args[0], (0, False, 0))
            if name == 'GetWindowThreadProcessId':
                selected_owner = window_owner
                return 4 if window_owner else 0
            if name == 'IsWindowVisible': return int(shown)
            if name == 'GetWindow': return parent
            raise AssertionError(name)
        def com(*args):
            calls.append(args)
            return result
        return SimpleNamespace(call=call, allocate=lambda data: 4096,
                               word=lambda address: selected_owner, com=com, calls=calls)

    def windows_api(self, windows):
        def enumerate_windows(callback, unused):
            for window in windows:
                if not callback(window, unused): return False
            return True
        def owner(window, pointer):
            pointer._obj.value = windows[window][0]
            return 4 if windows[window][0] else 0
        return SimpleNamespace(EnumWindows=enumerate_windows, GetWindowThreadProcessId=owner,
            IsWindowVisible=lambda window: windows[window][1], GetWindow=lambda window, kind: windows[window][2])

    def observer(self, flags=8):
        return SimpleNamespace(pid=17, read=lambda: dict(flags=flags))

    def test_visible_owned_foreground_window_gets_original_device(self):
        system = self.system()
        result = bind_audio_to_game(system, 100, self.observer())
        self.assertEqual(result['window'], 123)
        self.assertEqual(system.calls, [('IDirectSound8::SetCooperativeLevel', 100, 6, (123, 2))])

    def test_other_hidden_and_owned_popup_windows_never_change_audio(self):
        for windows in ({123: (18, True, 0)}, {123: (17, False, 0)}, {123: (17, True, 999)}):
            system = self.system(windows=windows)
            with self.subTest(windows=windows), self.assertRaises(ValueError):
                bind_audio_to_game(system, 100, self.observer(), windows_api=self.windows_api(windows))
            self.assertFalse(system.calls)

    def test_background_game_binds_its_single_visible_window_without_stealing_focus(self):
        windows = {123: (18, True, 0), 456: (17, True, 0), 789: (17, False, 0)}
        system = self.system(windows=windows)
        result = bind_audio_to_game(system, 100, self.observer(flags=0), windows_api=self.windows_api(windows))
        self.assertEqual(result['window'], 456)
        self.assertEqual(system.calls, [('IDirectSound8::SetCooperativeLevel', 100, 6, (456, 2))])

    def test_ambiguous_visible_game_windows_do_not_bind_audio(self):
        windows = {123: (18, True, 0), 456: (17, True, 0), 789: (17, True, 0)}
        system = self.system(windows=windows)
        with self.assertRaisesRegex(ValueError, 'one visible'):
            bind_audio_to_game(system, 100, self.observer(), windows_api=self.windows_api(windows))
        self.assertFalse(system.calls)

    def test_selected_window_owner_is_rechecked_after_enumeration(self):
        windows = {123: (18, True, 0), 456: (18, True, 0)}
        system = self.system(windows=windows)
        with patch('audio_focus.visible_game_window', return_value=456), self.assertRaises(ValueError):
            bind_audio_to_game(system, 100, self.observer(flags=0))
        self.assertFalse(system.calls)

    def test_failed_cooperative_binding_is_reported(self):
        with self.assertRaises(RuntimeError):
            bind_audio_to_game(self.system(result=0x80004005), 100, self.observer())


if __name__ == '__main__':
    unittest.main()
