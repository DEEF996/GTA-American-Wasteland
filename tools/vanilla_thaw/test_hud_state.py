"""Synthetic native memory only; no game or copyrighted pose/name fixtures."""
import math
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import hud_state as p


class Memory:
    def __init__(self):
        self.bytes = {}

    def put(self, at, data):
        self.bytes.update((at + i, value) for i, value in enumerate(data))

    def pack(self, at, fmt, *values):
        self.put(at, struct.pack('<' + fmt, *values))

    def read(self, at, size):
        return bytes(self.bytes.get(at + i, 0) for i in range(size))

    def word(self, at):
        return struct.unpack('<I', self.read(at, 4))[0]


class HudStateTests(unittest.TestCase):
    def setup_memory(self):
        self.enterContext(patch.object(p.probe_vm_idle, 'PINS', ()))
        m = Memory()
        actor, score, balance, timer, walk, input_ = 0x10100, 0x10800, 0x10900, 0x10b00, 0x10c00, 0x10d00
        components = {p.SCORE: score, p.BALANCE: balance, p.TIMER: timer, p.WALK: walk, 0x27d7cd28: input_}
        m.pack(0x84376c, '2I', 0x10000, 0x20000)
        m.pack(actor, 'I', 0x6989e8)
        m.pack(actor + 0x18, 'I', 7)
        m.pack(actor + 0x2b0, 'I', score)
        for key, (vtable, _) in p.COMPONENTS.items():
            m.pack(components[key], '4I', vtable, key, 0, actor)
        m.pack(score + 0x18, 'I', 0x11000)
        m.pack(0x11050, 'I', 8)
        m.pack(timer + 0x18, 'If', 2, 4.5)
        m.pack(timer + 0x24, 'I', walk)
        m.pack(0x6dd6d0, 'I', 0x10600)
        m.pack(0x8459c8, '2Q', 10, 10)
        state = [0] * 16
        state[3:6] = [1, 123, 20]
        worker = SimpleNamespace(process=100, thread=200, tid=123, count=20,
                                 check_call_access=lambda: None, snapshot=lambda: list(state))
        api = SimpleNamespace(_thaw_native_worker=worker, WaitForSingleObject=lambda *args: 258)
        with patch.object(p, 'PINS', ()):
            sampler = p.HudSampler(api, 100, {'actor': actor, 'components': components}, m)
        return m, sampler, state

    def test_coherent_original_values_are_returned_without_calculating_score(self):
        m, reader, _ = self.setup_memory()
        m.pack(0x1100c, 'i', 300)
        m.pack(0x11014, 'i', 123)
        m.pack(0x11020, 'i', 777)  # Deliberately not base*multiplier.
        m.pack(0x1104c, '3I', 0x12000, 8, 1)
        m.pack(0x1105c, 'If', 1, 3.5)
        m.pack(0x12000, 'I', 0x13000)
        m.pack(0x13000, 'i', 123)
        m.put(0x13020, b'Synthetic trick\0')
        m.put(0x13060, b'\\c1Synthetic trick\0')
        result = reader.read()
        self.assertEqual(result['native_frame'], 10)
        self.assertEqual(result['score']['native_combo_points'], 777)
        self.assertEqual(result['score']['entries'][0]['formatted_name'], '\\c1Synthetic trick')
        self.assertEqual(result['combo_timer']['seconds_remaining'], 4.5)
        self.assertFalse(result['balance']['meter_requested'])

    def test_trick_count_and_pointer_bounds_are_checked_before_dereference(self):
        for at, fmt, value in ((0x11054, 'I', 251), (0x11054, 'I', 9),
                               (0x11060, 'f', math.nan), (0x11074, 'B', 2)):
            m, reader, _ = self.setup_memory()
            m.pack(at, fmt, value)
            with self.assertRaises(RuntimeError): reader.read()
        m, reader, _ = self.setup_memory()
        m.pack(0x1104c, '3I', 0x30000, 8, 1)
        with self.assertRaisesRegex(RuntimeError, 'arena'): reader.read()

    def test_strings_must_terminate_within_each_original_64_byte_field(self):
        raw = bytearray(0xa0)
        raw[0x20:0x60] = b'a' * 64
        with self.assertRaisesRegex(RuntimeError, 'terminator'): p.trick_fields(raw)
        raw[0x5f] = 0
        self.assertEqual(len(p.trick_fields(raw)['name']), 63)
        raw[0x60:] = b'b' * 64
        with self.assertRaisesRegex(RuntimeError, 'terminator'): p.trick_fields(raw)

    def test_balance_uses_original_subobject_and_native_hud_scale(self):
        m, reader, _ = self.setup_memory()
        component = reader.components[p.BALANCE]
        sub = component + 0x24
        m.pack(component + 0x18, 'IB', 0xef24413b, 1)
        m.pack(sub, '5I', 0x698840, reader.actor, component, reader.components[p.SCORE], reader.components[0x27d7cd28])
        m.pack(sub + 0x30, '2f', -2048, 91)
        m.pack(sub + 0x44, '2I', 0xbc6b118f, 0xe3006fc4)
        balance = reader.read()['balance']
        self.assertEqual((balance['kind'], balance['raw_lean'], balance['hud_normalized']), ('manual', -2048, .5))
        self.assertTrue(balance['meter_requested'] and balance['vertical_controls'])
        m.pack(sub + 0x55, 'B', 1)
        self.assertFalse(reader.read()['balance']['meter_requested'])
        m.pack(sub + 4, 'I', reader.actor + 4)
        with self.assertRaisesRegex(RuntimeError, 'another actor'): reader.read()

    def test_native_timer_negative_expiration_is_preserved_and_unknown_state_fails(self):
        raw = bytearray(0x28)
        struct.pack_into('<If', raw, 0x18, 3, -.01)
        result = p.timer_fields(raw)
        self.assertTrue(result['expired'])
        self.assertLess(result['seconds_remaining'], 0)
        for state, value in ((4, 0), (1, math.inf), (2, math.nan)):
            struct.pack_into('<If', raw, 0x18, state, value)
            with self.assertRaises(RuntimeError): p.timer_fields(raw)

    def test_component_replacement_busy_worker_and_open_frame_are_rejected(self):
        for mutate in (lambda m, r, s: m.pack(r.components[p.TIMER] + 0xc, 'I', r.actor + 4),
                       lambda m, r, s: m.pack(0x8459c8, 'Q', 11),
                       lambda m, r, s: s.__setitem__(3, 2),
                       lambda m, r, s: m.pack(0x6dcf84, 'I', 0x123456),
                       lambda m, r, s: m.pack(r.actor + 0x18, 'I', 8)):
            m, reader, state = self.setup_memory()
            mutate(m, reader, state)
            with self.assertRaises(RuntimeError): reader.read()

    def test_same_count_changed_trick_is_rejected_by_full_reread(self):
        m, reader, _ = self.setup_memory()
        m.pack(0x1104c, '3I', 0x12000, 8, 1)
        m.pack(0x12000, 'I', 0x13000)
        original, visits = m.read, 0
        def changing(at, size):
            nonlocal visits
            if at == 0x13000:
                visits += 1
                if visits == 2: m.pack(at, 'i', 99)
            return original(at, size)
        m.read = changing
        with self.assertRaisesRegex(RuntimeError, 'changed during'): reader.read()


if __name__ == '__main__':
    unittest.main()
