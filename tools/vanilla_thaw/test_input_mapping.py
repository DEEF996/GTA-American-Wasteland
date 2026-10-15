"""Pure snapshots only: no keyboard acquisition or original native execution."""
import struct
import unittest

import input_mapping as p
from probe_frame_input import neutral_packet, validate_packet


def fields(mapped):
    raw = mapped.packet if isinstance(mapped, p.MappedInput) else mapped
    validate_packet(raw)
    return tuple(raw[:4]), struct.unpack_from('<I', raw, 0x24)[0]


class InputMappingTests(unittest.TestCase):
    def test_stock_named_buttons_match_pressure_update_evidence(self):
        # Independent expected values from table6BDE00 + CPadUpdate596DD0.
        expected = {'SQUARE': 0x80, 'CIRCLE': 0x20, 'X': 0x40, 'TRIANGLE': 0x10,
                    'L1': 4, 'L2': 1, 'L3': 0x200, 'R1': 8, 'R2': 2, 'R3': 0x400,
                    'SELECT': 0x100, 'BLACK': 0x10000, 'WHITE': 0x20000,
                    'UP': 0x1000, 'DOWN': 0x4000, 'LEFT': 0x8000, 'RIGHT': 0x2000}
        for name, mask in expected.items():
            self.assertEqual(fields(p.controller_packet([name])), ((128,)*4, mask))
        for bad in (['START'], ['X', 'square'], 'X', [0x800]):
            with self.assertRaises(ValueError): p.controller_packet(bad)

    def test_simultaneous_diagonal_grab_and_ollie_are_preserved(self):
        result = p.map_keys({22, 4, 93, 94})  # S+A+Num5+Num6
        self.assertEqual(fields(result), ((128, 128, 0, 255), 0x60))
        self.assertFalse(result.recenter_camera)
        self.assertEqual(p.map_keys({93}).packet, p.map_keys({93, 93}).packet)
        # Native code, not the mapper, detects the following release edge.
        self.assertEqual(p.map_keys(set()).packet, neutral_packet())

    def test_camera_and_movement_axes_are_independent_and_opposites_cancel(self):
        self.assertEqual(fields(p.map_keys({26, 7, 84, 87})), ((0, 0, 255, 0), 0))
        self.assertEqual(fields(p.map_keys({26, 22, 4, 7, 84, 85, 86, 87, 92})),
                         ((128,)*4, 0x80))

    def test_composite_actions_preserve_black_white_and_shoulders(self):
        self.assertEqual(fields(p.map_keys({98, 8, 95, 97})), ((128,)*4, 0x3000f))
        self.assertEqual(fields(p.map_keys({95, 97}))[1], 0xc)
        self.assertEqual(fields(p.map_actions({'SpineTransfer'}))[1], 0xc)

    def test_space_recenter_is_separate_from_native_focus_and_transfer(self):
        result = p.map_keys({44})
        self.assertEqual(result.packet, neutral_packet())
        self.assertTrue(result.recenter_camera)
        self.assertEqual(fields(p.map_keys({156}))[1], 0x200)
        self.assertFalse(p.map_keys({156}).recenter_camera)
        old = p.map_keys({44}, p.PREVIOUS_USER_BINDINGS)
        self.assertEqual(fields(old)[1], 0xc)
        self.assertFalse(old.recenter_camera)
        self.assertEqual(fields(p.map_keys({93}))[1], 0x40)  # Num5 never Focus
        # Space overrides held camera arrows without cancelling movement/tricks.
        self.assertEqual(fields(p.map_keys({44, 84, 87, 26, 92})), ((128, 128, 128, 0), 0x80))

    def test_physical_key_identity_does_not_depend_on_num_lock_or_text(self):
        self.assertEqual(fields(p.map_keys({94, 83}))[1], 0x20)  # NumLock is unbound
        self.assertEqual(p.map_keys({13, 64, 65, 41}).packet, neutral_packet())  # J/F7/F8/Esc
        for bad in ({'w'}, {True}, {-1}, {512}, {26.0}):
            with self.assertRaises(ValueError): p.map_keys(bad)

    def test_context_key_transfers_only_when_skating_and_preserves_camera_axes(self):
        held = {44, 84, 87, 26, 92}
        skating = p.map_keys(held, skating=True)
        self.assertEqual(fields(skating), ((0, 0, 128, 0), 0x8c))
        self.assertFalse(skating.recenter_camera)
        walking = p.map_keys(held, skating=False)
        self.assertEqual(fields(walking), ((128, 128, 128, 0), 0x80))
        self.assertTrue(walking.recenter_camera)
        rebound = {'CameraRecenter': 6, 'Grab': 94}
        self.assertEqual(fields(p.map_keys({6, 94}, rebound, skating=True))[1], 0x2c)
        self.assertEqual(p.map_keys({44}, rebound, skating=True).packet, neutral_packet())
        self.assertEqual(p.map_keys(set(), skating=True).packet, neutral_packet())

    def test_custom_bindings_are_validated_and_allow_intentional_chords(self):
        result = p.map_keys({26}, {'Forward': 26, 'Grab': 26, 'CameraRecenter': 26})
        self.assertEqual(fields(result), ((128, 128, 128, 0), 0x20))
        self.assertTrue(result.recenter_camera)
        for bindings in ({'Start': 40}, {'Ollie': 64}, {'Ollie': 65}, {'Ollie': 41},
                         {'Ollie': True}, {'Ollie': 999}, []):
            with self.assertRaises(ValueError): p.map_keys(set(), bindings)
        self.assertEqual(p.map_keys({93}, {}).packet, neutral_packet())

    def test_skitch_chord_consumes_actions_and_only_requests_original_up(self):
        for keys in ({93,96,44}, {93,96,44,26}):
            result=p.map_keys(keys,skating=True)
            self.assertTrue(result.skitch_requested)
            self.assertFalse(result.recenter_camera)
            self.assertEqual(fields(result)[1],0x1000)
        self.assertEqual(fields(p.map_keys({93,96,44,4},skating=True))[0],(128,128,0,128))
        self.assertFalse(p.map_keys({26},skating=True).skitch_requested)
        self.assertEqual(fields(p.map_keys({26},skating=True)),((128,128,128,0),0))
        for partial in ({93,96},{44,93},{44,96}):
            self.assertFalse(p.map_keys(partial,skating=True).skitch_requested)
        walking=p.map_keys({93,96,44},skating=False)
        self.assertFalse(walking.skitch_requested)
        self.assertEqual(fields(walking)[1],0x50)
        self.assertTrue(walking.recenter_camera)

    def test_skitch_chord_tracks_rebinding_and_backward_still_releases(self):
        bindings={'Ollie':6,'Grind':9,'CameraRecenter':10,'Backward':22}
        self.assertTrue(p.map_keys({6,9,10},bindings,skating=True).skitch_requested)
        self.assertFalse(p.map_keys({93,96,44},bindings,skating=True).skitch_requested)
        releasing=p.map_keys({6,9,10,22},bindings,skating=True)
        self.assertTrue(releasing.skitch_requested)
        self.assertEqual(fields(releasing),((128,128,128,255),0))


if __name__ == '__main__':
    unittest.main()
