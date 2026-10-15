"""Synthetic UI records only. No native code, game process or private assets."""
import struct
from types import SimpleNamespace
import unittest

import probe_hud_layout as p


class Memory:
    def __init__(self):
        self.data = bytearray(0x20000)

    def read(self, address, size):
        if not 0x10000 <= address <= 0x30000 - size:
            raise RuntimeError('outside synthetic arena')
        return bytes(self.data[address - 0x10000:address - 0x10000 + size])

    def put(self, address, data):
        self.data[address - 0x10000:address - 0x10000 + len(data)] = data

    def pack(self, address, fmt, *values):
        self.put(address, struct.pack('<' + fmt, *values))


class LayoutTests(unittest.TestCase):
    def fixture(self):
        m = Memory()
        manager, root, text, sprite = 0x10000, 0x11000, 0x12000, 0x13000
        nodes = (root, text, sprite)
        m.pack(manager, 'I', 0x6944bc)
        m.pack(manager + 0x10, '2I', root + 0x38, sprite + 0x38)
        m.pack(manager + 0x44, 'I', root)
        for i, (pointer, kind, id_) in enumerate(zip(nodes, (0xa13adbb7, 0x5200dfb6, 0xb12b510a),
                                                  (p.ROOT_ID, p.GROUPS[0], p.GROUPS[1]))):
            m.pack(pointer, 'I', p.TYPES[kind][0])
            m.pack(pointer + 0xc, 'I', manager)
            m.pack(pointer + 0x18, 'I', id_)
            m.pack(pointer + 0x20, 'I', kind)
            m.pack(pointer + 0x44, '2I', nodes[i + 1] + 0x38 if i < 2 else manager + 4,
                   nodes[i - 1] + 0x38 if i else manager + 4)
            if i:
                m.pack(pointer + 0x60, 'I', root)
        m.pack(root + 0x6c, 'I', text)
        m.pack(text + 0x78, 'I', sprite)
        m.pack(sprite + 0x84, 'I', text)
        m.pack(text + 0x194, '2I', 0x14000, 1)
        m.pack(0x14000, 'I', 0x14100)
        m.pack(0x14100, 'I', 0x694ddc)
        m.pack(0x1414c, 'I', 0x14200)
        m.pack(0x14200, 'I', 0x6955e0)
        m.pack(0x1421c, '3I', 0x15000, 0x16000, 0x17000)
        m.pack(0x14247, 'B', 1)
        m.put(0x17000, b'Synthetic\0')
        m.pack(sprite + 0x184, 'I', 0x12345678)
        m.pack(sprite + 0x18c, 'I', 0x18000)
        m.pack(0x18000, 'I', 0x695360)
        m.pack(0x18014, 'I', 0x18100)
        m.pack(0x18100, 'I', 0x696c10)
        m.pack(0x1811c, 'I', 0x18200)
        m.pack(0x1814b, 'B', 1)
        return m, manager, root, text, sprite

    def test_real_draw_fields_are_captured_without_host_layout(self):
        m, manager, root, text, sprite = self.fixture()
        tree = p.graph(m.read, manager)
        result = p.DrawReader(m.read).draws(tree, 0)
        self.assertEqual(tree['reachable'], {root, text, sprite})
        self.assertEqual(len(result), 2)
        text_row = next(r for r in result if r['kind'] == 'text')
        self.assertEqual(text_row['text'], 'Synthetic')
        self.assertEqual(len(bytes.fromhex(text_row['effects_hex'])), 9 * 10)
        self.assertEqual(text_row['draw_hex'], m.read(0x14200, 0x4c).hex())
        self.assertEqual(next(r['image_key'] for r in result if r['kind'] == 'sprite'), 0x12345678)

    def test_registry_cycle_foreign_owner_and_parent_disagreement_refused(self):
        for at, value in ((0x13044, 0x13038), (0x1200c, 0x20000), (0x13060, 0),
                          (0x13084, 0), (0x12000, 0x694610), (0x12050, 0x7fffffff)):
            m, manager, *_ = self.fixture()
            m.pack(at, 'I', value)
            with self.subTest(at=at), self.assertRaises(RuntimeError):
                p.graph(m.read, manager)

    def test_invisible_native_draws_are_omitted_not_reconstructed(self):
        m, manager, *_ = self.fixture()
        m.pack(0x14208, 'B', 1)
        m.pack(0x1814b, 'B', 0)
        self.assertEqual(p.DrawReader(m.read).draws(p.graph(m.read, manager), 0), [])

    def test_oversized_effects_foreign_wrapper_and_unterminated_text_refused(self):
        for at, fmt, value in ((0x12198, 'I', 33), (0x14100, 'I', 0x695360),
                               (0x14200, 'I', 0x696c10), (0x14208, 'B', 2), (0x1421c, 'I', 0x40000)):
            m, manager, *_ = self.fixture()
            m.pack(at, fmt, value)
            with self.subTest(at=at), self.assertRaises(RuntimeError):
                p.DrawReader(m.read).draws(p.graph(m.read, manager), 0)
        m, manager, *_ = self.fixture()
        m.put(0x17000, b'x' * 512)
        with self.assertRaisesRegex(RuntimeError, 'terminator'):
            p.DrawReader(m.read).draws(p.graph(m.read, manager), 0)

    def test_hud_group_must_remain_attached_to_original_root(self):
        m, manager, root, text, sprite = self.fixture()
        m.pack(root + 0x6c, 'I', 0)
        m.pack(text + 0x60, 'I', 0)
        m.pack(sprite + 0x60, 'I', 0)
        m.pack(text + 0x78, 'I', 0)
        m.pack(sprite + 0x84, 'I', 0)
        tree = p.graph(m.read, manager)
        with self.assertRaisesRegex(RuntimeError, 'detached'):
            p.DrawReader(m.read).draws(tree, 0)

    def test_nested_named_groups_share_one_draw_not_a_false_cycle(self):
        m, manager, root, text, sprite = self.fixture()
        m.pack(text + 0x78, 'I', 0)
        m.pack(text + 0x6c, 'I', sprite)
        m.pack(sprite + 0x60, 'I', text)
        m.pack(sprite + 0x84, 'I', 0)
        result = p.DrawReader(m.read).draws(p.graph(m.read, manager), 0)
        self.assertEqual(len(result), 2)
        self.assertEqual({row['widget'] for row in result}, {text, sprite})

    def test_failed_original_update_still_pops_frontend_heap(self):
        m, manager, root, *_ = self.fixture()
        native = p.NativeHudLayout.__new__(p.NativeHudLayout)
        native.manager, native.root, native.heap = manager, root, 0x20000
        native.idle = lambda: (bytes(16), ())
        native.read = m.read
        words = {p.ALLOCATOR + 0x10c: 0, p.ALLOCATOR + 0xc8: 0x111,
                 p.ALLOCATOR + 0xcc: 0}
        native.system = SimpleNamespace(word=lambda at: words[at], read=lambda at, n: bytes(n))
        calls = []
        def call(name, args=(), this=None):
            calls.append((name, this))
            if name == 'Push':
                words.update({p.ALLOCATOR + 0x10c: 1, p.ALLOCATOR + 0xc8: p.ALLOCATOR + 0xcc,
                              p.ALLOCATOR + 0xcc: native.heap})
            elif name == 'Update':
                raise RuntimeError('synthetic original update failure')
            elif name == 'Pop':
                words[p.ALLOCATOR + 0x10c] = 0
        native.call = call
        with self.assertRaisesRegex(RuntimeError, 'synthetic original update'):
            native.update()
        self.assertEqual(calls, [('Push', p.ALLOCATOR), ('Update', manager), ('Pop', p.ALLOCATOR)])
        self.assertEqual(words[p.ALLOCATOR + 0x10c], 0)


if __name__ == '__main__':
    unittest.main()
