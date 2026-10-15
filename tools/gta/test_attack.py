import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import attack
from test_hud_preview import state


class BoardAttackTests(unittest.TestCase):
    def test_wire_identity_and_source_edge_are_bounded(self):
        s = state()
        raw = attack.packet(s, attack.scene(s), 99, 2, 1000, 17, 283333, 1, True, True)
        self.assertEqual(len(raw), 104)
        self.assertEqual(struct.unpack_from('<8s4I', raw), (b'GTAHIT01', 1, 104, 17, 3))
        self.assertEqual(struct.unpack_from('<3Q2I', raw, 72), (17, 283333, 1, 1, 1))
        for fields in ((0, 0, 1, True, True), (1, 0, 1, True, True),
                       (1, 16667, -1, True, True), (1, 16667, 1, 1, True)):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                attack.packet(s, attack.scene(s), 99, 2, 1000, *fields)

    def test_only_original_walk_swing_edges_get_ids_and_new_lease_drops_carried_attack(self):
        observer = SimpleNamespace(api=SimpleNamespace(GetTickCount64=lambda: 1000))
        s = state()
        with patch.object(attack, 'Publisher') as transport:
            p = attack.AttackPublisher(observer)
            def send(tick, whacking, walking=True, request=99):
                p.publish(s, attack.scene(s), request, tick, tick*16667, whacking, walking=walking)
                return attack.BODY.unpack(transport.return_value.publish.call_args.args[0])[-3]
            self.assertEqual(send(1, True), 0)
            self.assertEqual(send(2, False), 0)
            self.assertEqual(send(3, True), 1)
            self.assertEqual(send(4, True), 1)
            self.assertEqual(send(5, False), 1)
            self.assertEqual(send(6, True, walking=False), 1)
            self.assertEqual(send(7, False), 1)
            self.assertEqual(send(8, True), 2)
            self.assertEqual(send(1, True, request=100), 0)
            self.assertEqual(send(2, False, request=100), 0)
            self.assertEqual(send(3, True, request=100), 1)
            with self.assertRaisesRegex(ValueError, 'new original sample'):
                send(3, True, request=100)
            p.clear()
            transport.return_value.clear.assert_called_once()
            self.assertEqual(send(4, True, request=100), 1)
            self.assertEqual(send(5, False, request=100), 1)
            self.assertEqual(send(6, True, request=100), 2)
            p.clear()
            self.assertEqual(send(1, False, request=101), 0)
            self.assertEqual(send(2, True, request=101), 1)


if __name__ == '__main__':
    unittest.main()
