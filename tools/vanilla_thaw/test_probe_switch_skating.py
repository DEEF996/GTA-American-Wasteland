import hashlib
import struct
import unittest

from probe_switch_skating import validate_script_source, validate_switch_contact, validate_transition
from import_qb import qbkey


class SwitchTests(unittest.TestCase):
    def states(self, contact):
        before = dict(mode=1, walking_gate=0, state=1-contact,
                      walk_state=contact, suspended=[1, 0, 1, 0])
        after = dict(before, mode=0, state=contact, suspended=[0, 1, 0, 1])
        return before, after

    def test_native_handoff_uses_walk_contact_not_stale_core(self):
        for contact in (0, 1):
            validate_transition(*self.states(contact))

    def test_walking_constraints_are_rejected_before_native_switch(self):
        for state in (2, 3, 4, 6):
            before, _ = self.states(0)
            before['walk_state'] = state
            with self.subTest(walk_state=state), self.assertRaises(ValueError):
                validate_switch_contact(before)
        for state, name in ((0, 'ground'), (1, 'air')):
            self.assertEqual(validate_switch_contact(self.states(state)[0]), name)

    def test_mode_without_correct_component_and_camera_ownership_is_rejected(self):
        for flags in ([1, 0, 1, 0], [0, 1, 1, 0], [0, 0, 0, 1]):
            before, after = self.states(0)
            after['suspended'] = flags
            with self.subTest(flags=flags), self.assertRaises(RuntimeError):
                validate_transition(before, after)


    def test_changed_contact_is_rejected(self):
        before, after = self.states(1)
        after['state'] = 0
        with self.assertRaises(RuntimeError):
            validate_transition(before, after)

    def test_special_or_inhibited_source_is_rejected(self):
        for change in (dict(walk_state=3), dict(walking_gate=1)):
            before, after = self.states(0)
            before.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_transition(before, after)


class ScriptSourceTests(unittest.TestCase):
    def setUp(self):
        self.name, self.base, self.body = 'scripts/game/skater/switch_control.qb.wpc', 0x10000, b'\x01\x24'
        self.raw = bytearray(96)
        self.raw[0] = 1
        self.pin = (self.name, 96, 0x93a4e402, 16, 36, 2, 2, hashlib.sha256(self.body).hexdigest())
        struct.pack_into('<5I', self.raw, 16, 0x70400, self.pin[2], qbkey(self.name), self.base + 36, 0)
        struct.pack_into('<3I', self.raw, 36, 0, 2, 2)
        self.raw[48:50] = self.body
        self.sources = {self.name: (self.base, 96)}

    def read(self, address, length):
        return bytes(self.raw[address - self.base:address - self.base + length])

    def test_original_owned_relocated_script_is_accepted(self):
        validate_script_source(self.read, self.sources, self.pin)

    def test_different_source_owner_or_script_body_is_rejected(self):
        for offset in (0, 16, 20, 24, 28, 40, 44, 48):
            with self.subTest(offset=offset):
                self.raw[offset] ^= 1
                with self.assertRaises(RuntimeError):
                    validate_script_source(self.read, self.sources, self.pin)
                self.raw[offset] ^= 1

    def test_missing_or_wrong_sized_source_is_rejected(self):
        for sources in ({}, {self.name: (self.base, 95)}, {self.name: (0xffff, 96)}):
            with self.subTest(sources=sources), self.assertRaises(RuntimeError):
                validate_script_source(self.read, sources, self.pin)

if __name__ == '__main__':
    unittest.main()
