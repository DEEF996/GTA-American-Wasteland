import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import startup_status as s


class StartupStatusTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.api = SimpleNamespace(GetTickCount64=lambda: self.now)
        self.observer = SimpleNamespace(pid=123, created=456, api=self.api)
        self.transport = Mock()
        creator = patch.object(s, 'Publisher', return_value=self.transport)
        creator.start()
        self.addCleanup(creator.stop)

    def packet(self):
        body, generation = self.transport.publish.call_args.args
        packet = s.BODY.unpack(body)
        self.assertEqual(packet[:5], (b'GTASTAT1', 1, 64, 123, packet[4]))
        self.assertEqual(packet[5:7], (456, 1000))
        self.assertEqual(packet[7], generation)
        return packet

    def test_readiness_requires_ready_and_heartbeat_does_not_restart_notice(self):
        display = s.StartupStatus(self.observer)
        display.update('loading')
        self.assertEqual(self.packet()[4], s.PREPARING)
        self.now = 1500
        display.update('starting_gameplay')
        self.assertEqual(self.packet()[4], s.STARTING_GAMEPLAY)
        self.assertEqual(self.packet()[-1], 1500)
        self.now = 1900
        display.update('starting_gameplay')
        self.assertEqual(self.packet()[-2:], (1900, 1500))
        self.now = 2000
        display.update('ready')
        self.assertEqual(self.packet()[4], s.READY)
        self.assertEqual(self.packet()[-1], 2000)
        self.now = 9000
        display.update('ready')
        self.assertEqual(self.packet()[-2:], (9000, 2000))
        display.update('restarting')
        self.assertEqual(self.packet()[4], s.STARTING_GAMEPLAY)
        self.now = 9500
        display.update('ready')
        self.assertEqual(self.packet()[-1], 9500)
        display.close()
        self.transport.close.assert_called_once()

    def test_unavailable_player_or_stopped_host_does_not_claim_ready(self):
        display = s.StartupStatus(self.observer)
        for phase in ('waiting_for_game', 'waiting_for_on_foot', 'game_closed', 'stopped'):
            display.update(phase)
            self.assertEqual(self.packet()[4], s.HIDDEN)

    def test_older_adapter_without_mapping_is_optional_but_other_errors_surface(self):
        missing = OSError('mapping absent')
        missing.winerror = 2
        with patch.object(s, 'Publisher', side_effect=missing):
            self.assertIsNone(s.connect(self.observer))
        with patch.object(s, 'Publisher', side_effect=OSError('access failed')):
            with self.assertRaisesRegex(OSError, 'access failed'):
                s.connect(self.observer)


if __name__ == '__main__':
    unittest.main()
