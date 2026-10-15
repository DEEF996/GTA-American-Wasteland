import unittest

from motion_contact import classify


class ContactTests(unittest.TestCase):
    def sample(self, **changes):
        return dict(dict(mode=0, walking_gate=0, state=0, walk_state=0), **changes)

    def test_skating_uses_core_and_ignores_inactive_walk_state(self):
        self.assertEqual(classify(self.sample(state=0, walk_state=6)), 'ground')
        self.assertEqual(classify(self.sample(state=1, walk_state=3)), 'air')

    def test_walking_uses_walk_and_ignores_inactive_core_state(self):
        self.assertEqual(classify(self.sample(mode=1, state=1, walk_state=0)), 'ground')
        self.assertEqual(classify(self.sample(mode=1, state=0, walk_state=1)), 'air')
        self.assertEqual(classify(self.sample(mode=1, state=2, walk_state=0)), 'ground')

    def test_special_contacts_are_not_misrepresented(self):
        for state, contact in ((2, 'wall'), (3, 'lip'), (5, 'wallplant'), (6, 'stall'), (7, 'tiretap')):
            with self.subTest(state=state):
                self.assertEqual(classify(self.sample(state=state)), contact)
        for state, contact in ((2, 'wall'), (3, 'hang'), (4, 'ladder'),
                               (5, 'walk_state_5'), (6, 'anim_wait'), (7, 'walk_state_7')):
            with self.subTest(walk_state=state):
                self.assertEqual(classify(self.sample(mode=1, state=7, walk_state=state)), contact)
        for mode, state in ((0, 8), (0, -1), (1, 8), (1, -1)):
            with self.subTest(mode=mode, state=state), self.assertRaises(ValueError):
                classify(self.sample(mode=mode, **{'walk_state' if mode else 'state': state}))

    def test_original_skating_rail_is_distinct_from_ground_and_walk(self):
        self.assertEqual(classify(self.sample(state=4, walk_state=6)), 'rail')
        self.assertEqual(classify(self.sample(mode=1, state=4, walk_state=0)), 'ground')
        self.assertEqual(classify(self.sample(mode=1, state=4, walk_state=1)), 'air')
        for sample in (self.sample(state=4, walking_gate=1), self.sample(state=4.0)):
            with self.assertRaises(ValueError):
                classify(sample)

    def test_user_wall_transition_keeps_original_walk_owner(self):
        samples = [self.sample(mode=1, state=0, walk_state=value) for value in (1, 1, 2, 3, 6, 0)]
        self.assertEqual([classify(row) for row in samples], ['air', 'air', 'wall', 'hang', 'anim_wait', 'ground'])

    def test_inhibited_missing_or_untyped_state_is_refused(self):
        for sample in (None, {}, self.sample(mode=2), self.sample(mode=True),
                       self.sample(walking_gate=1), self.sample(walking_gate=False),
                       self.sample(state=False), self.sample(mode=1, walk_state=1.0)):
            with self.subTest(sample=sample), self.assertRaises(ValueError):
                classify(sample)


if __name__ == '__main__':
    unittest.main()
