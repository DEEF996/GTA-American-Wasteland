import unittest
import struct
from types import SimpleNamespace
from unittest.mock import patch
import probe_particles as particles


class ParticleConstructorTests(unittest.TestCase):
    def test_exact_static_parent_extents_and_arena_boundaries(self):
        start, end = 0x10000000, 0x20000000
        for pointer in (0x8429a8, 0x8436b0, start, end-0x70):
            self.assertTrue(particles.parent_extent(pointer, start, end))
        for pointer in (0, 0x8429ac, 0x8436b4, start+1, end-0x6c, end):
            self.assertFalse(particles.parent_extent(pointer, start, end))

    def fixture(self):
        state = [0x693ac0, 0x100000, 0x110000, 0, 0, 0x120000] + [0x3f800000]*4 + [0x41200000, 0x42c80000]
        arena = [0x6a2354, 0x200000, 0, 0, 0, 0x205000, state[2], 0]
        heap = [0]*28
        heap[0], heap[9], heap[10], heap[11], heap[13] = 0x6a2200, state[1], 1, arena[1], 0x693ac8
        sentinel = (0x693ac4, 0xffffffff, 0, state[5], state[5])
        return tuple(state), tuple(arena), tuple(heap), sentinel

    def test_genuine_shape_and_source_defaults(self):
        values = self.fixture()
        particles.validate_state(*values, 0x5000, values[0][10:12])

    def test_reject_foreign_heap_broken_list_and_wrong_source(self):
        for row, offset, value in ((2, 9, 0), (3, 3, 0), (1, 5, 0x204000), (0, 10, 0)):
            values = list(self.fixture())
            changed = list(values[row]); changed[offset] = value; values[row] = tuple(changed)
            with self.assertRaises(RuntimeError):
                particles.validate_state(*values, 0x5000, self.fixture()[0][10:12])


class ParticleFactoryTests(unittest.TestCase):
    def fixture(self):
        state = (particles.FACTORY_BASE_VTABLE, 0x110000, 0, *([0x3f800000]*4), 0x41200000, 0x42c80000)
        sentinel = (0x12345678, state[1], state[1])
        return state, sentinel

    def test_original_factory_shape_and_source_lod(self):
        state, sentinel = self.fixture()
        particles.validate_factory(state, sentinel, state[7:9])
        for offset in (0, 2, 3, 7):
            changed = list(state); changed[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(RuntimeError):
                particles.validate_factory(tuple(changed), sentinel, state[7:9])
        with self.assertRaises(RuntimeError):
            particles.validate_factory(state, (0, state[1], 0), state[7:9])

    def publication_fixture(self):
        state, _ = self.fixture()
        pointer = 0x100000
        memory = {pointer: bytearray(struct.pack('<9I', *state)), particles.FACTORY_GLOBAL: bytearray(4)}
        system = SimpleNamespace(read=lambda address, size: bytes(memory[address][:size]),
                                 word=lambda address: struct.unpack('<I', memory[address][:4])[0])
        def write(api, process, address, value):
            memory[address][:len(value)] = value
        return pointer, state, memory, system, write

    def test_publication_only_sets_evidenced_derived_vtable_and_alias(self):
        pointer, state, memory, system, write = self.publication_fixture()
        with patch.object(particles.host, 'remote_write', side_effect=write) as publish:
            particles.publish_factory(None, None, system, pointer, state)
        self.assertEqual([(call.args[2], call.args[3]) for call in publish.call_args_list],
                         [(pointer, struct.pack('<I', particles.FACTORY_VTABLE)),
                          (particles.FACTORY_GLOBAL, struct.pack('<I', pointer))])
        self.assertEqual(struct.unpack('<9I', memory[pointer]), (particles.FACTORY_VTABLE, *state[1:]))

    def test_raced_alias_or_constructor_fields_refuse_before_any_publication(self):
        for address_index in (0, 1):
            pointer, state, memory, system, _ = self.publication_fixture()
            address = (pointer, particles.FACTORY_GLOBAL)[address_index]
            memory[address][-1] ^= 1
            with patch.object(particles.host, 'remote_write') as publish, self.assertRaises(RuntimeError):
                particles.publish_factory(None, None, system, pointer, state)
            publish.assert_not_called()

    def test_unexpected_vtable_write_side_effect_prevents_alias_publication(self):
        pointer, state, memory, system, write = self.publication_fixture()
        def changed(api, process, address, value):
            write(api, process, address, value)
            memory[pointer][-1] ^= 1
        with patch.object(particles.host, 'remote_write', side_effect=changed) as publish:
            with self.assertRaises(RuntimeError):
                particles.publish_factory(None, None, system, pointer, state)
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(system.word(particles.FACTORY_GLOBAL), 0)


if __name__ == '__main__':
    unittest.main()
