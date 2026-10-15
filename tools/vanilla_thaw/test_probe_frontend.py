"""Synthetic native layout checks; no Windows process or original code runs."""
import unittest

import probe_frontend as frontend


class FrontendLayoutTests(unittest.TestCase):
    def setUp(self):
        self.memory = {}
        self.owned = [0x10000 + index * 32 for index in range(10)]
        for index, address in enumerate(self.owned):
            self.memory[address] = [0x6955a4, 0, 0, 640, 480, self.owned[index + 1] if index < 9 else 0]

    def words(self, address, count):
        return tuple(self.memory[address][:count])

    def test_ten_original_panes_and_cycle_rejection(self):
        self.assertEqual(frontend.pane_chain(self.words, self.owned[0]), self.owned)
        self.memory[self.owned[-1]][5] = self.owned[0]
        with self.assertRaises(RuntimeError):
            frontend.pane_chain(self.words, self.owned[0])

    def root(self):
        manager, root = 0x20000, 0x21000
        m, r = [0] * (0x6ac // 4), [0] * (0x188 // 4)
        m[0], r[0] = 0x6944bc, 0x6946b4
        m[4] = m[5] = root + 0x38
        r[0x18 // 4], r[0x20 // 4], r[0xc // 4] = frontend.ROOT_ID, frontend.ROOT_TYPE, manager
        r[0x44 // 4] = r[0x48 // 4] = manager + 4
        r[0x184 // 4] = self.owned[0]
        self.memory[self.owned[0]][4] = 448
        self.memory[manager], self.memory[root] = m, r
        return manager, root

    def test_root_owns_one_pane_and_has_native_links(self):
        manager, root = self.root()
        free = self.owned[1:]
        self.assertEqual(frontend.validate_root(self.words, manager, root, self.owned, free), self.owned[0])
        self.memory[root][0x44 // 4] = 0
        with self.assertRaises(RuntimeError):
            frontend.validate_root(self.words, manager, root, self.owned, free)

    def test_root_rejects_wrong_owner_or_reused_pane(self):
        manager, root = self.root()
        with self.assertRaises(RuntimeError):
            frontend.validate_root(self.words, manager, root, self.owned, self.owned[:9])
        self.memory[root][0xc // 4] = manager + 4
        with self.assertRaises(RuntimeError):
            frontend.validate_root(self.words, manager, root, self.owned, self.owned[1:])


if __name__ == "__main__":
    unittest.main()
