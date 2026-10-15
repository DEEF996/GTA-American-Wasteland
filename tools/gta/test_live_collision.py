"""Completed source frames keep their original collision selection across buffering."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from live_session import collision_selection_matches
from world_stream import Ticket, WorldStream


class LiveCollisionTests(unittest.TestCase):
    def setUp(self):
        self.source = Ticket('old-tile', (1, 2, 3, 4, 0, 0), 13, ('barriers1',),
                             'activation', (2288., -1120., 32.), (0., 0., 0.), 64., 16.)
        self.world = WorldStream.__new__(WorldStream)
        self.world.radius = 64.
        self.world.timed_collision_instances = []
        self.stream = SimpleNamespace(world=self.world,
            current_ticket=replace(self.source, key='new-tile', hour=14, center=(2288., -1088., 32.)),
            current_inputs={'col_sha256': 'new-spatial-col'})

    def test_pending_sample_survives_hour_13_to_14_and_different_neighbor_col(self):
        self.assertNotEqual(self.source.hour, self.stream.current_ticket.hour)
        self.assertTrue(collision_selection_matches(self.stream, self.source, 'old-spatial-col'))

    def test_visibility_is_compared_at_source_tile_even_if_successor_has_other_timed_models(self):
        # This model changes in the new tile only. The completed old-tile step never saw it.
        self.world.timed_collision_instances = [(0, '', False,
            (2280., -1020., 30.), (2290., -1010., 40.), (14, 20))]
        self.assertTrue(collision_selection_matches(self.stream, self.source, 'old-spatial-col'))

    def test_real_hour_visibility_change_in_source_tile_still_rejects(self):
        self.world.timed_collision_instances = [(0, '', False,
            (2280., -1130., 30.), (2290., -1120., 40.), (14, 20))]
        self.assertFalse(collision_selection_matches(self.stream, self.source, 'old-spatial-col'))

    def test_completed_frame_survives_actual_visibility_change_only_with_ready_covering_world(self):
        # Live frame1254: the original19h step completed before a73ms wait;
        # a20h timed instance became visible while the successor COL loaded.
        self.source = replace(self.source, hour=19, center=(2336., -1664., 0.))
        self.stream.current_ticket = replace(self.source, key='new-tile', hour=20,
            center=(2336., -1632., 32.))
        self.world.timed_collision_instances = [(0, '', False,
            (2330., -1660., 0.), (2340., -1650., 20.), (20, 6))]
        position = (2352.8553796875, -1665.6611578125, 13.553361099023437)
        self.assertTrue(collision_selection_matches(self.stream, self.source, 'old-spatial-col',
            completed_position=position, current_ready=True))
        self.assertFalse(collision_selection_matches(self.stream, self.source, 'old-spatial-col',
            completed_position=position, current_ready=False))
        self.assertFalse(collision_selection_matches(self.stream, self.source, 'old-spatial-col',
            current_ready=True))

    def test_clock_boundary_never_borrows_foreign_identity_or_coverage(self):
        self.world.timed_collision_instances = [(0, '', False,
            (2280., -1130., 30.), (2290., -1120., 40.), (14, 20))]
        position = (2288., -1120., 32.)
        current = self.stream.current_ticket
        changes = [dict(scene=tuple(v + 1 if i == field else v
                                   for i, v in enumerate(self.source.scene))) for field in range(6)]
        changes += [dict(active_streams=()), dict(activation_sha256='different'),
                    dict(origin=(1., 0., 0.)), dict(radius=65.), dict(margin=15.),
                    dict(center=(2288., -1024., 32.))]
        for change in changes:
            with self.subTest(change=change):
                self.stream.current_ticket = replace(current, **change)
                self.assertFalse(collision_selection_matches(self.stream, self.source, 'old-spatial-col',
                    completed_position=position, current_ready=True))
        # The step may cross the predecessor's safety margin, but never its
        # physical64m extent. Both positions are safely covered by the successor.
        self.stream.current_ticket = current
        for offset, expected in ((49., True), (65., False)):
            with self.subTest(offset=offset):
                self.assertEqual(collision_selection_matches(self.stream, self.source, 'old-spatial-col',
                    completed_position=(2288., -1120.+offset, 32.), current_ready=True), expected)

    def test_same_hour_spatial_swap_and_identical_col_reticket_remain_valid(self):
        self.stream.current_ticket = replace(self.stream.current_ticket, hour=13)
        self.assertTrue(collision_selection_matches(self.stream, self.source, 'old-spatial-col'))
        self.stream.current_ticket = replace(self.stream.current_ticket, hour=20)
        self.world.timed_collision_instances = None
        self.world.visibility_hours = tuple(range(24))
        self.assertTrue(collision_selection_matches(self.stream, self.source, 'new-spatial-col'))

    def test_foreign_scene_or_activation_rejects_even_with_identical_col(self):
        changes = [dict(scene=tuple(v + 1 if i == field else v
                                   for i, v in enumerate(self.source.scene))) for field in range(6)]
        changes += [dict(active_streams=())]
        current = self.stream.current_ticket
        for change in changes:
            with self.subTest(change=change):
                self.stream.current_ticket = replace(current, **change)
                self.assertFalse(collision_selection_matches(self.stream, self.source, 'new-spatial-col'))

    def test_diagnostic_only_ipl_change_accepts_identical_col_but_not_unverified_different_col(self):
        # Actual frame2407 failure: every geometry field was identical; only
        # the full IPL diagnostic fingerprint changed during the native step.
        self.stream.current_ticket = replace(self.source, activation_sha256='changed-request-diagnostics')
        self.stream.current_inputs = {'col_sha256': 'same-proven-collision'}
        self.assertTrue(collision_selection_matches(self.stream, self.source, 'same-proven-collision'))
        self.assertFalse(collision_selection_matches(self.stream, self.source, 'different-collision'))

    def test_conservative_visibility_fallback_does_not_guess_equivalence(self):
        self.world.timed_collision_instances = None
        self.world.visibility_hours = tuple(range(24))
        self.assertFalse(collision_selection_matches(self.stream, self.source, 'old-spatial-col'))


if __name__ == '__main__':
    unittest.main()
