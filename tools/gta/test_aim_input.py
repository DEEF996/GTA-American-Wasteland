import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import aim_input as aim
from native.inspect_state import SnapshotBusy


class AimInputTests(unittest.TestCase):
    def fields(self):
        return [b'GTAAIM01', 1, 96, 17, 1, 123, 7, 1000, 44,
                0x20000, 3, 0, 0, 9, 9, 0., 1., 0., 0]

    def decode(self, values=None, now=1000):
        return aim.decode(aim.BODY.pack(*(self.fields() if values is None else values)), 17, 123, now)

    def state(self):
        return dict(pid=17, creation_100ns=123, ped_address=0x20000, epoch=3, area=0,
                    ped_area=0, f7_requests=9, flags=8 | 64, age_ms=0, control_eligible=True)

    def test_active_packet_and_exact_movement_identity(self):
        sample = self.decode(now=1100)
        self.assertTrue(aim.matches(sample, self.state(), 44))
        self.assertEqual(sample['forward_gta'], (0, 1, 0))
        self.assertFalse(aim.matches(sample, self.state(), 45))
        for field in ('pid', 'creation_100ns', 'ped_address', 'epoch', 'area', 'ped_area', 'f7_requests'):
            state = self.state(); state[field] += 1
            with self.subTest(field=field): self.assertFalse(aim.matches(sample, state, 44))

    def test_lost_focus_preparing_menu_or_stale_ownership_never_matches(self):
        sample = self.decode()
        for change in (dict(flags=64), dict(flags=8), dict(flags=8 | 64 | 256),
                       dict(age_ms=101), dict(age_ms=-1), dict(control_eligible=False)):
            with self.subTest(change=change): self.assertFalse(aim.matches(sample, dict(self.state(), **change), 44))
        for change in (dict(active=False), dict(age_ms=101), dict(age_ms=-1),
                       dict(entry_f7_requests=8), dict(protocol='GTAAIM00')):
            with self.subTest(change=change): self.assertFalse(aim.matches(dict(sample, **change), self.state(), 44))

    def test_process_protocol_freshness_and_active_state_validation(self):
        for index, value in ((0, b'GTAAIM00'), (1, 2), (2, 100), (3, 18), (4, 3), (5, 124),
                             (6, 0), (8, 0), (9, 0), (10, 0), (11, 256), (12, 1), (13, 8), (18, 1)):
            values = self.fields(); values[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError): self.decode(values)
        for now in (999, 1101):
            with self.subTest(now=now), self.assertRaises(ValueError): self.decode(now=now)
        with self.assertRaises(ValueError): aim.decode(bytes(95), 17, 123, 1000)

    def test_direction_rejects_nonfinite_vertical_and_nonunit(self):
        for direction in ((math.nan, 1, 0), (0, math.inf, 0), (0, 0, 1), (0, 0, 0), (2, 0, 0)):
            values = self.fields(); values[15:18] = direction
            with self.subTest(direction=direction), self.assertRaises(ValueError): self.decode(values)
        for direction in ((-1, 0, 0), (0, -1, 0), (.6, 0, .8)):
            values = self.fields(); values[15:18] = direction
            self.assertTrue(self.decode(values)['active'])

    def test_inactive_clear_cannot_retain_aim(self):
        values = self.fields(); values[4] = values[8] = 0; values[15:18] = (0., 0., 0.)
        self.assertFalse(self.decode(values)['active'])
        for index, value in ((8, 44), (15, .1), (17, math.nan)):
            changed = values.copy(); changed[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError): self.decode(changed)

    def test_reader_owns_mapping_only_and_propagates_busy_for_safe_caller_fallback(self):
        closed, unmapped, calls = [], [], []
        api = SimpleNamespace(OpenFileMappingW=lambda *args: calls.append(args) or 91,
                              MapViewOfFile=lambda *args: calls.append(args) or 92,
                              CloseHandle=closed.append, UnmapViewOfFile=unmapped.append,
                              GetTickCount64=lambda: 1000)
        observer = SimpleNamespace(pid=17, created=123, process=77, api=api)
        reader = aim.AimInput(observer)
        self.assertEqual(calls[0], (4, False, 'Local\\THAW_GTA_AIM_V1_17'))
        self.assertEqual(calls[1], (91, 4, 0, 0, 104))
        with patch.object(aim, 'read_mapping_body', return_value=aim.BODY.pack(*self.fields())) as read:
            self.assertTrue(reader.read(deadline=5)['active'])
            read.assert_called_once_with(92, 96, api, 77, 'aim input', deadline=5)
        with patch.object(aim, 'read_mapping_body', side_effect=SnapshotBusy('busy')):
            with self.assertRaises(SnapshotBusy): reader.read()
        reader.close(); reader.close()
        self.assertEqual(closed, [91]); self.assertEqual(unmapped, [92])

    def test_failed_view_mapping_releases_only_owned_handle(self):
        closed = []
        api = SimpleNamespace(OpenFileMappingW=lambda *args: 91, MapViewOfFile=lambda *args: 0,
                              CloseHandle=closed.append)
        observer = SimpleNamespace(pid=17, created=123, process=77, api=api)
        with self.assertRaises(OSError): aim.AimInput(observer)
        self.assertEqual(closed, [91])


if __name__ == '__main__':
    unittest.main()
