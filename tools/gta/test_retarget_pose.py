"""Synthetic pose-math tests; no copyrighted fixtures or running game needed."""
import math
import random
import struct
import unittest
from unittest.mock import patch

import retarget_pose as r
from waw.retarget import quaternion_matrix


def rotation(axis, angle):
    q = [0., 0., 0., math.cos(angle/2)]
    q[axis] = math.sin(angle/2)
    return quaternion_matrix(q)


def placed(matrix, xyz):
    result = list(matrix)
    result[12:15] = xyz
    return result


def humanoid_fixture():
    """Synthetic anatomical tree, with different source/target arm rest angles."""
    children = {0: [1], 1: [2, 201, 41, 51], 2: [3], 3: [4, 302, 301],
                4: [5, 31, 21], 5: [8, 6, 7]}
    for chain in ((31, 32, 33, 34, 35, 36), (21, 22, 23, 24, 25, 26),
                  (41, 42, 43, 44), (51, 52, 53, 54)):
        children.update({a: [b] for a, b in zip(chain, chain[1:])})
    mapping = {0: 0, 1: 1, 2: 2, 3: 4, 4: 29, 5: 30,
               31: 5, 32: 6, 33: 7, 34: 8, 35: 9, 36: 10,
               21: 17, 22: 18, 23: 19, 24: 20, 25: 23, 26: 24,
               41: 43, 42: 44, 43: 45, 44: 46, 51: 39, 52: 40, 53: 41, 54: 42}
    positions = {0: [0, 0, 0], 1: [0, 0, 1], 2: [0, 0, 1.1], 3: [0, 0, 1.5],
                 4: [0, 0, 1.8], 5: [.08, 0, 2], 8: [.1, 0, 2.02],
                 6: [.1, .04, 2.1], 7: [.1, -.04, 2.1],
                 201: [.1, 0, 1.2], 301: [.12, .1, 1.6], 302: [.12, -.1, 1.6]}
    for arm, leg, side in ((30, 40, 1), (20, 50, -1)):
        for bone, lateral, height in ((1, .1, 1.8), (2, .3, 1.75), (3, .5, 1.5),
                                      (4, .7, 1.25), (5, .8, 1.12), (6, .81, 1.04)):
            positions[arm+bone] = [0, side*lateral, height]
        for bone, position in ((1, [0, side*.14, 1]), (2, [.02, side*.14, .55]),
                               (3, [0, side*.14, .1]), (4, [.15, side*.14, .1])):
            positions[leg+bone] = position
    nodes, binds, parents_by_id = [], [], {}
    def visit(bone_id, parent=-1, sibling=False):
        index = len(nodes)
        children_here = children.get(bone_id, [])
        bind = placed(rotation(0, bone_id*.003), positions[bone_id])
        binds.append(bind)
        nodes.append(dict(ordinal=index, bone_id=bone_id, matrix_index=index,
            parent_flags=(2 if sibling else 0) | (0 if children_here else 1),
            candidate_thaw_index=mapping.get(bone_id), provisional_mapping=bone_id in (2, 3, 25, 26, 35, 36),
            observed_transform=dict(affine_row_major=r.multiply(bind, rotation(2, .5)))))
        parents_by_id[bone_id] = parent
        for ordinal, child in enumerate(children_here):
            visit(child, bone_id, ordinal != len(children_here)-1)
    visit(0)
    source_binds = [placed(r.IDENTITY, [0, 0, .1*i]) for i in range(52)]
    source_parents = [-1]*52
    for bone_id, source_index in mapping.items():
        source_binds[source_index] = placed(r.IDENTITY, positions[bone_id])
        source_parents[source_index] = mapping.get(parents_by_id[bone_id], -1)
    for side, indices in ((1, (5, 6, 7, 8, 9, 10)), (-1, (17, 18, 19, 20, 23, 24))):
        for i, source_index in enumerate(indices):
            source_binds[source_index] = placed(r.IDENTITY, [0, side*(.1+.2*i), 1.8-.015*i*i])
    source_binds[30] = placed(r.IDENTITY, [0, 0, 2])
    source_parents[3], source_parents[4] = 2, 3
    source = dict(format="thaw-ske-v1", bone_count=52, source=dict(ske_sha256=r.SKE_SHA),
        bones=[dict(index=i, parent_index=source_parents[i], inverse_bind_row_major=r.inverse(bind),
                    bind_model_row_major=bind) for i, bind in enumerate(source_binds)])
    target = dict(accepted=True, read_only=True, rig=dict(
        hierarchies=[dict(address=10, count=32, matrix_space="world", nodes=nodes)],
        skins=[dict(address=20, count=32, inverse_bind_invariants=[dict(affine_row_major=r.inverse(b)) for b in binds])],
        atomics=[dict(hierarchy_address=10, skin_address=20)]))
    return source, target


class RetargetTests(unittest.TestCase):
    def assertMatrix(self, actual, expected):
        self.assertLess(r.matrix_error(actual, expected), 1e-9)

    def test_multiply_preserves_order_and_sum_rounding_bit_for_bit(self):
        rng = random.Random(42)
        pairs = [([rng.uniform(-1e6, 1e6) for _ in range(16)],
                  [rng.uniform(-1e6, 1e6) for _ in range(16)]) for _ in range(256)]
        pairs += [([1e16, 1., -1e16, 0.]*4, [1.]*16),
                  ([-0.]*16, [1.]*16), (list(r.IDENTITY), list(r.IDENTITY))]
        for a, b in pairs:
            expected = [sum(a[row*4+k]*b[k*4+column] for k in range(4))
                        for row in range(4) for column in range(4)]
            self.assertEqual(struct.pack('<16d', *r.multiply(a, b)), struct.pack('<16d', *expected))

    def test_rest_preserves_target_axes_and_segments_with_different_source_axes(self):
        source = [placed(rotation(0, .7), [0, 0, 0]),
                  placed(rotation(1, -.8), [0, 5, 0]),
                  placed(rotation(2, .9), [1, 9, 0])]
        target = [placed(rotation(1, -.3), [1, 2, 3]),
                  placed(rotation(2, .4), [1, 2, 7]),
                  placed(rotation(0, -.6), [2, 2, 10])]
        result = r.retarget(source, target, [-1, 0, 1], [0, 1, 2], {0: 0, 1: 1, 2: 2},
                            rotation(2, 1.2), [r.IDENTITY]*3)
        for actual, expected in zip(result, target):
            self.assertMatrix(actual, expected)

    def test_conjugation_transforms_vectors_in_the_correct_order(self):
        delta = r.multiply(rotation(0, .8), rotation(1, -.6))
        calibration = r.multiply(rotation(2, 1.1), rotation(0, .4))
        converted = r.multiply(r.multiply(r.inverse(calibration), delta), calibration)
        for point in ([1, 0, 0], [0, 1, 0], [0, 0, 1], [.4, -.3, 1]):
            actual = r.transform(r.transform(point, calibration), converted)
            expected = r.transform(r.transform(point, delta), calibration)
            self.assertLess(math.dist(actual, expected), 1e-9)

    def test_parent_motion_is_not_applied_twice_and_unmapped_child_follows(self):
        binds = [r.IDENTITY, placed(rotation(1, .2), [0, 0, 2]),
                 placed(rotation(0, -.5), [1, 0, 3])]
        delta = rotation(2, 1.)
        result = r.retarget(binds, binds, [-1, 0, 1], [0, 1, 2], {0: 0, 1: 1},
                            r.IDENTITY, [delta]*3)
        for actual, bind in zip(result, binds):
            self.assertMatrix(actual, r.multiply(bind, delta))
        self.assertLess(r.segment_error(result, binds, [-1, 0, 1]), 1e-9)

    def test_collapsed_chain_uses_end_global_delta_once(self):
        source = [r.IDENTITY, placed(r.IDENTITY, [0, 0, 1]), placed(r.IDENTITY, [0, 0, 2])]
        target = [r.IDENTITY, placed(r.IDENTITY, [0, 0, 4])]
        middle, end = rotation(0, .6), r.multiply(rotation(1, -.7), rotation(0, .6))
        result = r.retarget(source, target, [-1, 0], [0, 1], {0: 0, 1: 2},
                            r.IDENTITY, [r.IDENTITY, middle, end])
        self.assertMatrix(r.rigid(result[1]), end)
        self.assertEqual(result[1][12:15], [0, 0, 4])

    def test_different_limb_rest_directions_follow_actual_source_motion(self):
        source = [rotation(0, .3), placed(rotation(1, -.4), [3, 0, 0])]
        target = [rotation(1, .6), placed(rotation(2, .2), [0, 0, 2])]
        calibration = r.multiply(rotation(0, .4), rotation(2, -.7))
        mapping, pairs = {0: 0, 1: 1}, [(0, 1)]
        correction = r.limb_corrections(source, target, mapping, calibration, pairs)
        for delta in (r.IDENTITY, rotation(0, 1.2),
                      r.multiply(rotation(2, .6), rotation(1, -.5))):
            actual_source = [r.multiply(bind, delta) for bind in source]
            models = r.retarget(source, target, [-1, 0], [0, 1], mapping, calibration,
                                [delta]*2, rest_corrections=correction)
            self.assertLess(max(r.limb_errors(actual_source, models, mapping, calibration, pairs)), 1e-5)
            self.assertLess(r.segment_error(models, target, [-1, 0]), 1e-9)

    def test_collapsed_endpoint_includes_omitted_joint_bend(self):
        source = [r.IDENTITY, placed(r.IDENTITY, [0, 0, 1]), placed(r.IDENTITY, [0, 0, 2])]
        middle = placed(rotation(1, .8), [0, 0, 1])
        end = placed(rotation(1, .8), r.transform([0, 0, 1], middle))
        actual_source = [r.IDENTITY, middle, end]
        palettes = [r.multiply(r.inverse(bind), pose) for bind, pose in zip(source, actual_source)]
        target = [r.IDENTITY, placed(r.IDENTITY, [0, 0, 4])]
        mapping, pairs = {0: 0, 1: 2}, [(0, 1)]
        baseline = r.retarget(source, target, [-1, 0], [0, 1], mapping, r.IDENTITY, palettes)
        self.assertGreater(r.limb_errors(actual_source, baseline, mapping, r.IDENTITY, pairs)[0], 10)
        actual = r.retarget(source, target, [-1, 0], [0, 1], mapping, r.IDENTITY, palettes,
                            collapsed_pairs=pairs)
        self.assertLess(r.limb_errors(actual_source, actual, mapping, r.IDENTITY, pairs)[0], 1e-5)
        self.assertLess(r.segment_error(actual, target, [-1, 0]), 1e-9)

    def test_palm_inherits_forearm_rest_alignment_without_losing_native_articulation(self):
        source = [rotation(1, .2), placed(rotation(2, -.4), [3, 0, 0])]
        target = [rotation(2, .6), placed(rotation(0, -.7), [0, 0, 2])]
        calibration = r.multiply(rotation(0, .3), rotation(1, -.5))
        mapping, pairs = {0: 0, 1: 1}, [(0, 1)]
        corrections = r.limb_corrections(source, target, mapping, calibration, pairs, [(0, 1)])
        bind_local = r.rigid(r.multiply(target[1], r.inverse(target[0])))
        for parent_delta in (r.IDENTITY, r.multiply(rotation(0, .8), rotation(2, -.4))):
            actual = r.retarget(source, target, [-1, 0], [0, 1], mapping, calibration,
                                [parent_delta]*2, rest_corrections=corrections)
            self.assertMatrix(r.rigid(r.multiply(actual[1], r.inverse(actual[0]))), bind_local)
            self.assertLess(r.segment_error(actual, target, [-1, 0]), 1e-9)
        # Independent source palm rotation must still reach the mapped hand.
        palm_delta = r.multiply(rotation(1, .9), rotation(0, -.2))
        actual = r.retarget(source, target, [-1, 0], [0, 1], mapping, calibration,
                            [r.IDENTITY, palm_delta], rest_corrections=corrections)
        expected = r.multiply(r.multiply(r.rigid(target[1]), corrections[1]),
                             r.multiply(r.multiply(r.inverse(calibration), palm_delta), calibration))
        self.assertMatrix(r.rigid(actual[1]), expected)
        self.assertGreater(r.matrix_error(r.rigid(r.multiply(actual[1], r.inverse(actual[0]))), bind_local), .1)

    def test_pelvis_translation_moves_descendants_without_moving_root_or_stretching_limbs(self):
        binds = [r.IDENTITY, placed(r.IDENTITY, [0, 1, 0]), placed(r.IDENTITY, [0, 2, 0])]
        movement = placed(r.IDENTITY, [2, 3, 0])
        calibration = rotation(2, math.pi/2)
        result = r.retarget(binds, binds, [-1, 0, 1], [0, 1, 2], {0: 0, 1: 1, 2: 2},
                            calibration, [r.IDENTITY, movement, movement],
                            translation_transfer=(1, 1, .5))
        self.assertMatrix(result[0], binds[0])
        displacement = [-1.5, 1., 0.]
        for index in (1, 2):
            self.assertLess(math.dist(result[index][12:15],
                                     [binds[index][12+i]+displacement[i] for i in range(3)]), 1e-9)
        self.assertLess(r.segment_error(result, binds, [-1, 0, 1], excluded=(1,)), 1e-9)

    def test_unmapped_rotation_keeps_native_local_axes_and_bind_offset(self):
        binds = [r.IDENTITY, placed(r.IDENTITY, [0, 0, 2])]
        parent, native_child = rotation(2, .4), rotation(0, -.6)
        result = r.retarget(binds, binds, [-1, 0], [0, 1], {0: 0}, r.IDENTITY,
                            [parent, r.IDENTITY], [r.IDENTITY, native_child])
        self.assertMatrix(r.rigid(result[1]), r.multiply(native_child, parent))
        self.assertEqual(result[1][12:15], [0, 0, 2])

    def test_anatomical_calibration_is_measured_not_fixed_game_axes(self):
        original = [[0, 0, 0], [0, 2, 0], [-1, 0, 0], [1, 0, 0]]
        shift = placed(r.multiply(rotation(0, .6), rotation(2, -.4)), [7, 8, 9])
        source = r.anatomical_basis(*original)
        target = r.anatomical_basis(*(r.transform(p, shift) for p in original))
        self.assertMatrix(r.multiply(r.inverse(source), target), r.rigid(shift))
        with self.assertRaisesRegex(ValueError, "Degenerate"):
            r.anatomical_basis(*([[0, 0, 0]]*4))

    def test_hanim_parent_stack_uses_indices_and_checks_balance(self):
        nodes = [dict(ordinal=i, bone_id=100+i, matrix_index=n, parent_flags=f)
                 for i, (n, f) in enumerate(((2, 0), (0, 0), (1, 1)))]
        parents, order = r.parents_from_nodes(nodes)
        self.assertEqual(parents, [2, 0, -1])
        self.assertEqual(order, [2, 0, 1])
        for index, flag in ((0, 1), (2, 0), (1, 4)):
            invalid = [dict(n) for n in nodes]
            invalid[index]["parent_flags"] = flag
            with self.subTest(index=index), self.assertRaises(ValueError):
                r.parents_from_nodes(invalid)

    def test_bad_transforms_bits_and_parent_order_are_rejected(self):
        for index, value in ((0, math.nan), (0, -1), (0, 2), (1, .3)):
            bad = list(r.IDENTITY)
            bad[index] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                r.rigid(bad)
        for bits in ([0x7fc00000], [1 << 32], [-1], [True], []):
            with self.subTest(bits=bits), self.assertRaises(ValueError):
                r.decode_bits(bits, 1)
        with self.assertRaisesRegex(ValueError, "parent"):
            r.retarget([r.IDENTITY]*2, [r.IDENTITY]*2, [-1, 0], [1, 0], {},
                       r.IDENTITY, [r.IDENTITY]*2)

    def test_source_affine_scale_and_shear_preserve_target_segment_lengths(self):
        target = [r.IDENTITY, placed(r.IDENTITY, [0, 0, 2])]
        original_rotation = rotation(2, .7)
        affine = list(r.IDENTITY)
        affine[0], affine[4], affine[5], affine[10] = 2., .6, 3., .5
        affine = r.multiply(affine, original_rotation)
        self.assertMatrix(r.source_rotation(affine), original_rotation)
        actual = r.retarget([r.IDENTITY]*2, target, [-1, 0], [0, 1], {0: 0, 1: 1},
                            r.IDENTITY, [affine]*2)
        for model, bind in zip(actual, target):
            self.assertMatrix(model, r.multiply(bind, original_rotation))
            r.rigid(model)
        self.assertLess(r.segment_error(actual, target, [-1, 0]), 1e-9)
        with self.assertRaisesRegex(ValueError, "Scaled"):
            r.rigid(affine)

    def test_source_orientation_rejects_invalid_singular_and_reflected_affines(self):
        for index, value in ((0, math.nan), (3, 1.), (15, 0.), (12, 100000.),
                             (0, 0.), (5, 0.), (10, 0.), (10, 1e-12), (10, -1.)):
            bad = list(r.IDENTITY)
            bad[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                r.source_rotation(bad)


class HumanoidCalibrationTests(unittest.TestCase):
    assertMatrix = RetargetTests.assertMatrix
    def test_neutral_face_and_head_alignment_do_not_freeze_startup_expression(self):
        source, target = humanoid_fixture()
        nodes = target['rig']['hierarchies'][0]['nodes']
        by_id = {n['bone_id']: n['matrix_index'] for n in nodes}
        for bone_id in (6, 7, 8):
            node = nodes[by_id[bone_id]]
            node['observed_transform']['affine_row_major'] = r.multiply(
                rotation(1, .8), node['observed_transform']['affine_row_major'])
        prepared = r.prepare(source, target)
        args = {k: prepared[k] for k in ('source_bind', 'target_bind', 'parents', 'order',
            'mapping', 'calibration', 'native_local_rotations', 'rest_corrections', 'collapsed_pairs')}
        for rotation_delta in (r.IDENTITY, r.multiply(rotation(1, .7), rotation(2, -.4))):
            models = r.retarget(**args, palettes=[rotation_delta]*52)
            for parent, child in ((4, 5), (5, 6), (5, 7), (5, 8)):
                a, b = by_id[parent], by_id[child]
                expected = r.rigid(r.multiply(prepared['target_bind'][b], r.inverse(prepared['target_bind'][a])))
                self.assertMatrix(r.rigid(r.multiply(models[b], r.inverse(models[a]))), expected)

    def test_actual_finger_motion_replaces_captured_fist_without_changing_bone_lengths(self):
        source, target = humanoid_fixture()
        prepared = r.prepare(source, target)
        nodes = target['rig']['hierarchies'][0]['nodes']
        by_id = {n['bone_id']: n['matrix_index'] for n in nodes}
        args = {k: prepared[k] for k in ('source_bind', 'target_bind', 'parents', 'order',
            'mapping', 'calibration', 'native_local_rotations', 'rest_corrections', 'collapsed_pairs')}
        palettes = [r.IDENTITY]*52
        neutral = r.retarget(**args, palettes=palettes)
        for bone_id, source_index in ((35, 9), (36, 10), (25, 23), (26, 24)):
            self.assertEqual(prepared['mapping'][by_id[bone_id]], source_index)
        palettes[9] = palettes[10] = rotation(0, .6)
        curled = r.retarget(**args, palettes=palettes)
        self.assertMatrix(curled[by_id[34]], neutral[by_id[34]])
        self.assertGreater(math.dist(curled[by_id[36]][12:15], neutral[by_id[36]][12:15]), .01)
        self.assertLess(r.segment_error(curled, prepared['target_bind'], prepared['parents']), 1e-9)
        for parent, child in ((35, 36), (25, 26)):
            a, b = by_id[parent], by_id[child]
            expected = r.rigid(r.multiply(prepared['target_bind'][b], r.inverse(prepared['target_bind'][a])))
            self.assertMatrix(r.rigid(r.multiply(curled[b], r.inverse(curled[a]))), expected)

    def test_incompatible_finger_or_facial_hierarchy_is_rejected(self):
        source, target = humanoid_fixture()
        source['bones'][9]['parent_index'] = 7
        with self.assertRaisesRegex(ValueError, 'Finger source or target hierarchy'):
            r.prepare(source, target)


class PreparedRetargetTests(unittest.TestCase):
    def arguments(self):
        source, target = humanoid_fixture()
        prepared = r.prepare(source, target)
        return {key: prepared[key] for key in ('source_bind', 'target_bind', 'parents', 'order',
            'mapping', 'calibration', 'native_local_rotations', 'rest_corrections',
            'collapsed_pairs', 'translation_transfer')}

    def test_repeated_different_palettes_reuse_rig_without_recomputing_bind_inverses(self):
        args = self.arguments()
        evaluator = r.Retargeter(**args)
        for angle in (0., .3, -1.2, 2.7):
            palettes = [placed(rotation(i % 3, angle+i*.003), [0, angle, 0]) for i in range(52)]
            expected = r.retarget(**args, palettes=palettes)
            with patch.object(r, 'inverse', side_effect=AssertionError('Per-frame bind inverse')):
                self.assertEqual(evaluator.evaluate(palettes), expected)
        with self.assertRaisesRegex(ValueError, 'count'):
            evaluator.evaluate(palettes[:-1])

    def test_owns_constant_snapshot_and_does_not_reuse_previous_pose(self):
        args = self.arguments()
        evaluator = r.Retargeter(**args)
        palettes = [rotation(2, .3)]*52
        expected = evaluator.evaluate(palettes)
        args['source_bind'][1][0] = 2
        args['target_bind'][1][12] = 10
        args['mapping'].clear(); args['parents'][1] = 31
        args['calibration'][0] = 2
        args['native_local_rotations'][0][0] = 2
        args['rest_corrections'].clear(); args['collapsed_pairs'].clear()
        result = evaluator.evaluate(palettes)
        self.assertEqual(result, expected)
        result[0][0] = 999
        self.assertEqual(evaluator.evaluate(palettes), expected)
        changed = list(palettes); changed[9] = rotation(0, -.7)
        self.assertNotEqual(evaluator.evaluate(changed), expected)

    def test_each_new_palette_still_rejects_corruption_after_valid_frames(self):
        evaluator = r.Retargeter(**self.arguments())
        evaluator.evaluate([r.IDENTITY]*52)
        for index, value in ((0, math.nan), (3, 1.), (15, 0.), (10, 0.), (10, -1.)):
            bad = list(r.IDENTITY); bad[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                evaluator.evaluate([r.IDENTITY]*4+[bad]+[r.IDENTITY]*47)
            evaluator.evaluate([r.IDENTITY]*52)

    def test_unmapped_source_model_retains_guards_and_translation_channel(self):
        binds = [r.IDENTITY, placed(r.IDENTITY, [0, 0, 1])]
        evaluator = r.Retargeter(binds, binds, [-1, 0], [0, 1], {0: 0}, r.IDENTITY,
                                translation_transfer=(1, 1, .5))
        moved = placed(rotation(1, .4), [2, 3, 4])
        result = evaluator.evaluate([r.IDENTITY, moved])
        model = r.multiply(binds[1], moved)
        expected = [binds[1][12+i]+(model[12+i]-binds[1][12+i])*.5 for i in range(3)]
        self.assertEqual(result[1][12:15], expected)
        for index, value in ((0, math.nan), (3, .1), (10, 0.), (10, -1.), (14, 99999.)):
            bad = list(r.IDENTITY); bad[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                evaluator.evaluate([r.IDENTITY, bad])


class TimelineTests(unittest.TestCase):
    def test_legacy_and_dense_original_timestamps(self):
        duration = .6333333253860474
        passed = dict(phase="animation_pass", host_palette_writes=False, deterministic_repeat=True)
        legacy = [dict(phase="animation_frame", time=t) for t in (0, .3, duration, .3)]+[passed]
        _, info, repeat = r.animation_timeline(legacy)
        self.assertEqual((info["playback_frames"], repeat), (3, (1, 3)))
        expected = [i/120 for i in range(76)]+[duration]
        passed = dict(passed, sample_hz=120, duration=duration, playback_frames=77,
                      native_frames=79, unique_sample_times=expected)
        dense = [dict(phase="animation_frame", time=t) for t in expected+[.3, .3]]+[passed]
        _, info, repeat = r.animation_timeline(dense)
        self.assertEqual((info["sample_hz"], info["playback_frames"], repeat), (120, 77, (77, 78)))
        for changed in (dense[:-2]+[passed], dense[:1]+dense[:1]+dense[2:], dense[:-1]+[dict(passed, sample_hz=60)]):
            with self.assertRaises(ValueError):
                r.animation_timeline(changed)


if __name__ == "__main__":
    unittest.main()
