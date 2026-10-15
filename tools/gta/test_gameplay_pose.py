"""Source palette boundary tests with a synthetic skeleton and no game assets."""
import hashlib
import math
import struct
import unittest
from unittest.mock import patch

from gameplay_pose import GameplayPose, source_visual_delta, visual_conversion, detached_board_world, board_transform
import retarget_pose as pose


def fixture():
    converter = GameplayPose.__new__(GameplayPose)
    binds = [list(pose.IDENTITY) for _ in range(32)]
    for index, matrix in enumerate(binds):
        matrix[14] = index * .1
    parents = [-1]+list(range(31))
    converter.prepared = {}
    converter.calibration = dict(root_index=0, target_bind=binds, parents=parents,
                                translation_transfer=(1, 1, .025),
                                source_bind=[list(pose.IDENTITY) for _ in range(52)], calibration=pose.IDENTITY)
    converter.arguments = dict(source_bind=[pose.IDENTITY]*52, target_bind=binds,
        parents=parents, order=list(range(32)), mapping={i: i for i in range(32)},
        calibration=pose.IDENTITY, translation_transfer=(1, 1, 1.))
    converter.retargeter = pose.Retargeter(**converter.arguments)
    cache_conversion(converter)
    return converter, [list(pose.IDENTITY) for _ in range(52)]


def cache_conversion(converter):
    converter.visual_conversion = tuple(visual_conversion(converter.calibration))
    converter.visual_inverse = tuple(pose.inverse(list(converter.visual_conversion)))


def packed(matrices):
    return struct.pack('<832f', *(v for matrix in matrices for v in matrix))


def bail_board():
    # Native bail's measured non-unit axes, representable without game assets.
    return [-1.0078125,0,0,0, 0,.9921875,.125,0, 0,.125,-1,0, 3,1.5,-26,1]


class GameplayPoseTests(unittest.TestCase):
    def assertMatrix(self, actual, expected):
        self.assertLess(max(abs(a-b) for a,b in zip(actual,expected)), 1e-7)

    def test_final_model_flip_removes_world_yaw_position_and_scale_once(self):
        c,s=math.cos(.7),math.sin(.7)
        yaw=[c,0,-s,0, 0,1,0,0, s,0,c,0, 150000,3000,-160000,1]
        c,s=math.cos(1.2),math.sin(1.2)
        flip=[1,0,0,0, 0,c,s,0, 0,-s,c,0, 0,40*(1-c),-40*s,1]
        final=pose.multiply(flip,yaw)
        for r,scale in zip((0,4,8),(2.,3.,4.)):
            final[r:r+3]=[v*scale for v in final[r:r+3]]
        self.assertMatrix(source_visual_delta(yaw,yaw[12:15],final),flip)
        self.assertMatrix(source_visual_delta(yaw,yaw[12:15],yaw),pose.IDENTITY)

    def test_physics_slope_is_not_lost_when_final_display_matches_physics(self):
        c,s=math.cos(.4),math.sin(.4)
        tilt=[1,0,0,0, 0,c,s,0, 0,-s,c,0, 0,0,0,1]
        c,s=math.cos(.8),math.sin(.8)
        yaw=[c,0,-s,0, 0,1,0,0, s,0,c,0, 120,30,-60,1]
        physics=pose.multiply(tilt,yaw)
        self.assertMatrix(source_visual_delta(physics,physics[12:15],physics),tilt)

    def test_vertical_parkour_basis_uses_the_same_yaw_as_movement(self):
        import preview_gameplay_move as movement
        vertical=[1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,0,0,1]
        c,s=math.cos(.8),math.sin(.8)
        yaw=[c,0,-s,0, 0,1,0,0, s,0,c,0, 120,30,-60,1]
        physics=pose.multiply(vertical,yaw)
        bits=struct.unpack('<16I',struct.pack('<16f',*physics))
        heading=movement.source_heading(dict(actor_matrix_bits=bits))
        self.assertAlmostEqual(heading,math.atan2(-s,-c),places=6)
        self.assertMatrix(source_visual_delta(physics,physics[12:15],physics),vertical)

    def test_original_walking_actor_vector_tags_are_not_render_affine_terms(self):
        import preview_gameplay_move as movement
        # Captured after dismounting: actor up.w=1, final rendered up.w=0.
        actor=[-.25874897837638855,0,-.9659446477890015,0,
               0,1,0,1, .9659446477890015,0,-.25874897837638855,0,
               0,0,0,1]
        original=list(actor)
        canonical=list(actor);canonical[7]=0
        position=[89092.359375,904.5890502929688,49520.01171875]
        visual=list(canonical);visual[12:15]=position
        expected=source_visual_delta(canonical,position,visual)
        self.assertMatrix(source_visual_delta(actor,position,visual),expected)
        bits=lambda matrix: struct.unpack('<16I',struct.pack('<16f',*matrix))
        self.assertEqual(movement.source_heading(dict(actor_matrix_bits=bits(actor))),
                         movement.source_heading(dict(actor_matrix_bits=bits(canonical))))
        converter,matrices=fixture()
        result=converter.evaluate(packed(matrices),actor_matrix=actor,
                                  actor_position=position,visual_matrix=visual)
        self.assertMatrix(result['visual_delta'],pose.IDENTITY)
        self.assertEqual(actor,original)
        visual[7]=1
        with self.assertRaisesRegex(ValueError,'not affine'):
            source_visual_delta(actor,position,visual)

    def test_actor_vector_tags_do_not_weaken_orientation_validation(self):
        for index,value in ((0,float('nan')),(0,1.5),(0,-1.),(4,.1),(5,0.)):
            actor=list(pose.IDENTITY);actor[7]=1;actor[index]=value
            with self.subTest(index=index,value=value),self.assertRaises(ValueError):
                source_visual_delta(actor,[0,0,0],pose.IDENTITY)

    def test_visual_conversion_keeps_cj_and_board_in_the_same_original_pivot(self):
        converter, matrices=fixture()
        converter.calibration['calibration']=[1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,0,0,1]
        converter.calibration['source_bind'][1][13]=40
        cache_conversion(converter)
        base=converter.evaluate(packed(matrices))['model_bytes']
        flip=[1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,40,-40,1]
        result=converter.evaluate(packed(matrices),actor_matrix=pose.IDENTITY,
            actor_position=[0,0,0],visual_matrix=flip,board_visible=False)
        self.assertEqual(result['model_bytes'],base) # Native anchored-root checks remain unchanged.
        self.assertFalse(result['board_visible'])
        conversion=visual_conversion(converter.calibration)
        board=struct.unpack('<64f',result['board_palette'])[:16]
        for point in ([0,40,0],[13,8,-4],[0,0,0]):
            from_source=pose.transform(pose.transform(point,flip),conversion)
            from_target=pose.transform(pose.transform(point,conversion),result['visual_delta'])
            from_board=pose.transform(pose.transform(point,board),conversion)
            self.assertMatrix(from_source,from_target);self.assertMatrix(from_source,from_board)
        models=[struct.unpack_from('<16f',base,i*64) for i in range(32)]
        shown=[pose.multiply(m,result['visual_delta']) for m in models]
        self.assertGreater(math.dist(shown[0][12:15],models[0][12:15]),.1)
        self.assertLess(pose.segment_error(shown,models,converter.calibration['parents']),1e-7)

    def test_invalid_visual_frame_rejected_before_publishing(self):
        for index,value in ((0,float('nan')),(3,1.),(0,-1.),(5,0.)):
            final=list(pose.IDENTITY);final[index]=value
            with self.subTest(index=index),self.assertRaises(ValueError):
                source_visual_delta(pose.IDENTITY,[0,0,0],final)
        converter,matrices=fixture()
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            converter.evaluate(packed(matrices),visual_matrix=pose.IDENTITY)

    def test_free_board_world_path_uses_physical_units_not_rig_proportion_scale(self):
        converter, matrices=fixture()
        calibration=converter.calibration
        calibration['translation_transfer']=(1,1,.031)
        calibration['source_bind'][1][13]=40
        calibration['calibration']=[0,1,0,0, 0,0,1,0, 1,0,0,0, 0,0,0,1]
        cache_conversion(converter)
        final=[0,0,1,0, 0,1,0,0, -1,0,0,0, 9000,50,-8000,1]
        origin=[1200,-1800,10]
        world=detached_board_world(calibration,final,origin)
        conversion=visual_conversion(calibration)
        for point in ([0,0,0],[13,8,-4]):
            actual=pose.transform(pose.transform(point,conversion),world)
            rotated=pose.transform(point,final)
            local=[(rotated[i]-final[12+i])*.031 for i in range(3)]
            expected=[origin[0]+9000*.0254+local[0],origin[1]+8000*.0254-local[2],
                      origin[2]+50*.0254+local[1]]
            self.assertMatrix(actual,expected)
        changed=list(final);changed[12]+=100
        next_world=detached_board_world(calibration,changed,origin)
        self.assertAlmostEqual(next_world[12]-world[12],2.54)
        raw=packed(matrices)
        result=converter.evaluate(raw,board_visible=False,board_pose=dict(object_id=123,
            palette=raw[48*64:],visual_matrix=final),world_origin=origin)
        self.assertTrue(result['board_visible']);self.assertEqual(result['board_object_id'],123)
        self.assertMatrix(result['board_world'],world)
        self.assertEqual(result['board_palette'],raw[48*64:])
        with self.assertRaisesRegex(ValueError,'together'):
            converter.evaluate(raw,board_visible=True,board_pose=dict(object_id=123,
                palette=raw[48*64:],visual_matrix=final))
        result=converter.evaluate(raw)
        self.assertEqual(result['board_object_id'],0)
        self.assertMatrix(result['board_world'],pose.IDENTITY)
    def test_scaled_sheared_skater_preserves_rigid_cj_and_original_board_bytes(self):
        converter, matrices = fixture()
        matrices[4][0], matrices[4][4], matrices[4][5], matrices[4][10] = 2., .6, 3., .5
        matrices[48][12] = 7.
        raw = packed(matrices)
        result = converter.evaluate(raw, hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['board_palette'], raw[48*64:])
        models = struct.unpack('<512f', result['model_bytes'])
        for index in range(32):
            pose.rigid(models[index*16:(index+1)*16])

    def test_board_scale_is_rejected_with_bone_identity_before_render_transport(self):
        converter, matrices = fixture()
        matrices[48][0] = 2.
        with self.assertRaisesRegex(ValueError, 'source bone 48: Scaled'):
            converter.evaluate(packed(matrices))

    def test_native_bail_board_correction_preserves_each_joint_pivot_and_cj(self):
        converter, matrices = fixture()
        baseline = converter.evaluate(packed(matrices))['model_bytes']
        for bone in range(48,52):
            converter.calibration['source_bind'][bone][12:15] = [bone-45., 8., -3.]
            matrices[bone] = bail_board()
        raw = packed(matrices)
        result = converter.evaluate(raw, hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['model_bytes'], baseline)
        for index, bone in enumerate(range(48,52)):
            shown = struct.unpack_from('<16f', result['board_palette'], index*64)
            pose.rigid(shown)
            pivot = converter.calibration['source_bind'][bone][12:15]
            self.assertLess(math.dist(pose.transform(pivot, shown),
                                     pose.transform(pivot, matrices[bone])), 2e-6)
        self.assertEqual(raw, packed(matrices))

    def test_bail_board_correction_precedes_display_pivot_and_applies_to_free_board(self):
        converter, matrices = fixture()
        for bone in range(48,52): matrices[bone] = bail_board()
        raw = packed(matrices)
        expected = bytes(converter.evaluate(raw)['board_palette'])
        display = [1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,40,-40,1]
        result = converter.evaluate(raw, actor_matrix=pose.IDENTITY, actor_position=[0,0,0],
                                    visual_matrix=display)
        for bone in range(4):
            before = struct.unpack_from('<16f', expected, bone*64)
            after = struct.unpack_from('<16f', result['board_palette'], bone*64)
            pose.rigid(after)
            self.assertLess(max(abs(a-b) for a,b in zip(after,pose.multiply(before,display))), 3e-6)
        result = converter.evaluate(raw, board_visible=False, board_pose=dict(
            palette=raw[48*64:], object_id=17, visual_matrix=pose.IDENTITY))
        self.assertEqual(result['board_palette'],expected)
        self.assertEqual(result['board_object_id'],17)

    def test_existing_rigid_board_bytes_are_unchanged(self):
        converter, matrices = fixture()
        for bone in range(48,52):
            matrices[bone][4] = .0002
            matrices[bone][12:15] = [-0., bone/13., -bone/17.]
        raw = packed(matrices)
        self.assertEqual(converter.evaluate(raw)['board_palette'],raw[48*64:])

    def test_board_correction_rejects_large_deformation_and_corruption(self):
        for index,value in ((0,float('nan')),(3,.1),(0,0.),(0,-1.),(0,1.03),(4,.03),(12,100000.)):
            matrix=list(pose.IDENTITY); matrix[index]=value
            with self.subTest(index=index,value=value),self.assertRaises(ValueError):
                board_transform(matrix,pose.IDENTITY)
        converter, matrices = fixture()
        bad = matrices[48:]
        bad[0][4] = .03
        detached = struct.pack('<64f', *(v for matrix in bad for v in matrix))
        with self.assertRaises(ValueError):
            converter.evaluate(packed([list(pose.IDENTITY) for _ in range(52)]),
                board_visible=False, board_pose=dict(palette=detached,object_id=17,
                                                   visual_matrix=pose.IDENTITY))

    def test_corrupt_source_palette_or_hash_is_rejected(self):
        for index, value in ((0, float('nan')), (3, 1.), (10, 0.), (10, -1.)):
            converter, matrices = fixture()
            matrices[4][index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                converter.evaluate(packed(matrices))
        converter, matrices = fixture()
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            converter.evaluate(packed(matrices), '0'*64)

    def test_unused_skater_bones_still_reject_corruption(self):
        converter, matrices = fixture()
        for bone in (32, 47):
            for index, value in ((0, float('nan')), (3, .1), (10, 0.), (10, -1.), (12, 100000.)):
                bad = [list(matrix) for matrix in matrices]
                bad[bone][index] = value
                with self.subTest(bone=bone, index=index), self.assertRaises(ValueError):
                    converter.evaluate(packed(bad))

    def test_repeated_visual_frames_and_free_board_reuse_rig_conversion(self):
        converter, matrices = fixture()
        raw = packed(matrices)
        kwargs = dict(actor_matrix=pose.IDENTITY, actor_position=[0,0,0],
                      visual_matrix=pose.IDENTITY)
        attached = dict(converter.evaluate(raw, **kwargs))
        free = dict(board_visible=False, board_pose=dict(palette=raw[48*64:],
                    object_id=17, visual_matrix=pose.IDENTITY))
        detached = dict(converter.evaluate(raw, **kwargs, **free))
        with patch('gameplay_pose.visual_conversion', side_effect=AssertionError('Per-frame rig conversion')):
            for _ in range(3):
                self.assertEqual(converter.evaluate(raw, **kwargs), attached)
                self.assertEqual(converter.evaluate(raw, **kwargs, **free), detached)

    def test_detached_board_replaces_hidden_joint_without_relaxing_render_guards(self):
        converter, matrices = fixture()
        good = packed(matrices)
        detached = dict(object_id=17, palette=good[48*64:], visual_matrix=pose.IDENTITY)
        expected = dict(converter.evaluate(good, board_visible=False, board_pose=detached))
        # The original hidden bail joint can exceed the visible-board correction
        # bound while the separate rendered board still has a rigid palette.
        for bone in range(48, 52):
            matrices[bone][0] = 1.03
        raw = packed(matrices)
        actual = converter.evaluate(raw, board_visible=False, board_pose=detached)
        self.assertEqual(actual, expected)
        with self.assertRaisesRegex(ValueError, 'exceeds presentation correction'):
            converter.evaluate(raw)
        with self.assertRaisesRegex(ValueError, 'exceeds presentation correction'):
            converter.evaluate(good, board_visible=False,
                               board_pose=dict(detached, palette=raw[48*64:]))
        for object_id in (0, -1, 0x100000000, True):
            with self.subTest(object_id=object_id), self.assertRaisesRegex(ValueError, 'identity/palette'):
                converter.evaluate(raw, board_visible=False, board_pose=dict(detached, object_id=object_id))
        with self.assertRaisesRegex(ValueError, 'together'):
            converter.evaluate(raw, board_visible=True, board_pose=detached)

    def test_hidden_replaced_board_still_rejects_corrupt_source_joint(self):
        converter, matrices = fixture()
        good = packed(matrices)
        detached = dict(object_id=17, palette=good[48*64:], visual_matrix=pose.IDENTITY)
        for bone in range(48, 52):
            for index, value in ((0, math.nan), (3, .1), (10, 0.), (10, -1.), (12, 100000.)):
                bad = [list(matrix) for matrix in matrices]
                bad[bone][index] = value
                with self.subTest(bone=bone, index=index), self.assertRaises(ValueError):
                    converter.evaluate(packed(bad), board_visible=False, board_pose=detached)


if __name__ == '__main__':
    unittest.main()
