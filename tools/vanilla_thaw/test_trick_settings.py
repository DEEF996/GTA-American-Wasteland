import struct
import unittest

from probe_trick_settings import (FLIP, ORDINARY_SLOT, UNASSIGNED, changed_snapshot,
                                 mapping_keys, select_edits, special_record, validate_assignments,
                                 TrickSettings)


class TrickSettingsTests(unittest.TestCase):
    def menu_fixture(self):
        baseline = dict(normal={1: 10}, special=[(20, 30, 0), (UNASSIGNED, UNASSIGNED, 0)],
                        stats=[10]*10, unresolved_includes=[999])
        catalog = dict(slots=[dict(checksum=1, category='Flip')],
                       tricks=[dict(checksum=10, category='Flip', special=False),
                               dict(checksum=11, category='Flip', special=False),
                               dict(checksum=12, category='Grab', special=False),
                               dict(checksum=20, category='Flip', special=True)],
                       combos=[dict(checksum=30, category='Air'), dict(checksum=31, category='Grind')])
        return baseline, catalog

    def test_user_changes_validate_full_profile_and_preserve_stats(self):
        baseline, catalog = self.menu_fixture()
        target = validate_assignments(baseline, catalog, {'1': 11}, {})
        self.assertEqual(target['normal'], {1: 11})
        self.assertEqual(baseline['normal'], {1: 10})
        self.assertEqual(target['stats'], [10]*10)
        self.assertEqual(target['unresolved_includes'], [999])
        self.assertEqual(validate_assignments(baseline, catalog, {}, {}), baseline)

    def test_reject_incompatible_or_ambiguous_user_tricks_before_native_mutation(self):
        baseline, catalog = self.menu_fixture()
        changes = [({'1': 12}, {}), ({'1': 20}, {}), ({'1': True}, {}), ({'2': 11}, {}),
                   ({}, {'0': dict(trick=20, combo=31)}),
                   ({}, {'1': dict(trick=20, combo=30)}),
                   ({}, {'0': dict(trick=UNASSIGNED, combo=30)})]
        calls = []
        controller = TrickSettings(baseline, [], catalog, calls.append, lambda: None)
        for normal, special in changes:
            with self.subTest(normal=normal, special=special), self.assertRaises(RuntimeError):
                controller.apply(normal, special)
        self.assertEqual(calls, [])

    def test_special_combo_can_move_and_defaults_restore_whole_original_profile(self):
        baseline, catalog = self.menu_fixture()
        override = {'0': dict(trick=UNASSIGNED, combo=UNASSIGNED), '1': dict(trick=20, combo=30)}
        target = validate_assignments(baseline, catalog, {}, override)
        self.assertEqual(target['special'], [(UNASSIGNED, UNASSIGNED, 0), (20, 30, 0)])
        calls = []
        def native(value):
            calls.append(value)
            return value
        controller = TrickSettings(baseline, [], catalog, native, lambda: calls.append('closed'))
        controller.apply({}, override)
        controller.apply({}, override)
        controller.apply({}, {})
        controller.close()
        controller.close()
        self.assertEqual(calls, [target, baseline, 'closed'])

    def test_custom_created_special_remains_read_only(self):
        baseline, catalog = self.menu_fixture()
        baseline['special'][0] = (20, 30, 1)
        with self.assertRaises(RuntimeError):
            validate_assignments(baseline, catalog, {}, {'0': dict(trick=UNASSIGNED, combo=UNASSIGNED)})

    def fixture(self):
        # Synthetic metadata, not copied game catalogue or game bytes.
        rows = [dict(key=i, defined=True, category=FLIP, special=i >= 101)
                for i in range(1, 211)]
        before = dict(normal={ORDINARY_SLOT: 2, 999: 3}, unresolved_includes=[987],
                      special=[(101, 400, 0)] + [(UNASSIGNED, UNASSIGNED, 0)] * 11,
                      stats=[10] * 10)
        return before, rows

    def test_edits_are_different_and_keep_category_and_specialness(self):
        before, rows = self.fixture()
        ordinary, index, special = select_edits(before, rows)
        self.assertEqual((ordinary, index, special), (1, 0, (102, 400, 0)))
        after = changed_snapshot(before, ordinary, index, special)
        self.assertEqual(after['normal'], {ORDINARY_SLOT: 1, 999: 3})
        self.assertEqual(after['special'][1:], before['special'][1:])
        self.assertEqual(after['stats'], [10] * 10)
        self.assertEqual(before['normal'][ORDINARY_SLOT], 2)

    def test_no_incompatible_replacement_or_custom_special(self):
        for modification in ('category', 'special', 'cat', 'combo'):
            before, rows = self.fixture()
            if modification == 'category':
                rows[1]['category'] = 900
            elif modification == 'special':
                for row in rows[100:]:
                    row['special'] = False
            elif modification == 'cat':
                before['special'][0] = (101, 400, 1)
            else:
                before['special'][0] = (101, UNASSIGNED, 0)
            with self.subTest(modification=modification), self.assertRaises(RuntimeError):
                select_edits(before, rows)

    def test_catalogue_is_exact_dynamic_unique_inventory(self):
        before, rows = self.fixture()
        for bad in (rows[:-1], rows + [dict(rows[0])], rows[:-1] + [dict(rows[0])]):
            with self.assertRaises(RuntimeError):
                select_edits(before, bad)

    def test_special_padding_is_not_part_of_semantic_state(self):
        prefix = struct.pack('<2IB', 123, 456, 0)
        self.assertEqual(special_record(prefix + b'\x10\x20\x30'),
                         special_record(prefix + b'\xff\xff\xff'))
        for bad in (bytes(11), struct.pack('<3I', 123, 456, 2)):
            with self.assertRaises(RuntimeError):
                special_record(bad)

    def chain(self):
        memory = {
            0x100: struct.pack('<2I', 0, 0x200),
            0x200: struct.pack('<4I', 0x1a00, 4, 90, 0x210),
            0x210: struct.pack('<4I', 0x1a00, 0, 700, 0x220),
            0x220: struct.pack('<4I', 0x1a00, 0, 800, 0),
            0x300: struct.pack('<2I', 0, 0x400),
            0x400: struct.pack('<4I', 0x1a00, 8, 91, 0),
        }
        return memory, lambda address, count: memory[address][:count]

    def test_mapping_discovers_includes_and_preserves_unresolved_names(self):
        memory, read = self.chain()
        self.assertEqual(mapping_keys(read, 0x100, lambda key: 0x300 if key == 700 else 0),
                         ([4, 8], [800]))

    def test_mapping_rejects_cycles_and_non_checksum_fields(self):
        for bad in ('next', 'include', 'type'):
            memory, read = self.chain()
            resolve = lambda key: 0x300 if key == 700 else 0
            if bad == 'next':
                memory[0x200] = struct.pack('<4I', 0x1a00, 4, 90, 0x200)
            elif bad == 'include':
                resolve = lambda key: 0x100
            else:
                memory[0x200] = struct.pack('<4I', 0x200, 4, 90, 0)
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                mapping_keys(read, 0x100, resolve)

    def test_mapping_does_not_invent_override_semantics(self):
        memory, read = self.chain()
        memory[0x400] = struct.pack('<4I', 0x1a00, 4, 99, 0)
        # Only key discovery: runtime values must still use native GetChecksum.
        self.assertEqual(mapping_keys(read, 0x100, lambda key: 0x300 if key == 700 else 0),
                         ([4], [800]))


if __name__ == '__main__':
    unittest.main()
