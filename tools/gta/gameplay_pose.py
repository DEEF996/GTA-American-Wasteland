"""Retarget one evaluated THAW gameplay palette into the existing CJ transport.

No animation selection or physics is implemented here. The caller supplies a
complete palette sampled after an original native gameplay update.
"""
from __future__ import annotations
import hashlib
import json
import math
import struct

import preview_pose as transport
import retarget_pose as pose


def visual_orientation(matrix):
    pose.require(len(matrix) == 16 and all(math.isfinite(v) and abs(v) <= 10000000 for v in matrix),
                 'Invalid original visual transform dimensions or values')
    pose.require(max(abs(matrix[i]) for i in (3,7,11)) < .001 and abs(matrix[15]-1) < .001,
                 'Original visual transform is not affine')
    scales = [math.hypot(*matrix[row:row+3]) for row in (0,4,8)]
    pose.require(all(.01 <= v <= 100 for v in scales), 'Invalid original visual model scale')
    visual = list(matrix)
    for row, scale in zip((0,4,8), scales):
        visual[row:row+3] = [v/scale for v in visual[row:row+3]]
    visual[12:15] = [0., 0., 0.]
    return pose.rigid(visual)


def source_visual_delta(actor_matrix, position, visual_matrix):
    """Remove only world translation/yaw already applied by GTA MOVE2.

    ModelComponent's final transform also contains ramps/lean and RotateDisplay
    aerial pivots. Removing the full physics orientation would lose the former.
    Cosmetic source scale is not multiplied into CJ's measured proportions.
    """
    pose.require(len(actor_matrix) == len(visual_matrix) == 16 and len(position) == 3 and
        all(math.isfinite(v) and abs(v) <= 10000000 for v in (*actor_matrix, *position, *visual_matrix)),
        'Invalid original visual transform dimensions or values')
    visual = visual_orientation(visual_matrix); visual[12:15] = list(visual_matrix[12:15])
    from geometry import source_horizontal_forward
    # Actor orientation stores Mth::Vector W tags (walking can set up.w=1),
    # not a render affine matrix. Validate its right-handed 3x3 via the same
    # helper as MOVE; the final visual matrix still requires affine W values.
    fx, fz = source_horizontal_forward(actor_matrix)
    yaw_world = [fz,0.,-fx,0., 0.,1.,0.,0., fx,0.,fz,0., *position,1.]
    delta = pose.multiply(visual, pose.inverse(yaw_world))
    rotation = pose.rigid(delta); rotation[12:15] = delta[12:15]
    pose.require(max(abs(v) for v in rotation[12:15]) <= 10000, 'Unbounded original visual pivot')
    return rotation


def visual_conversion(calibration):
    """Same source-pelvis -> CJ-pelvis conversion as the immutable board bundle."""
    result = list(calibration['calibration'])
    target, source, scale = calibration['translation_transfer']
    for row in range(3):
        for col in range(3): result[row*4+col] *= scale
    origin = pose.transform(calibration['source_bind'][source][12:15], result)
    result[12:15] = [calibration['target_bind'][target][12+i]-origin[i] for i in range(3)]
    return result


def board_transform(matrix, bind):
    """Keep the original board joint position in the rigid presentation transport."""
    rotation = pose.source_rotation(matrix)
    differences = [matrix[4*r+c]-rotation[4*r+c] for r in range(3) for c in range(3)]
    if max(abs(v) for v in differences) < .001:
        return matrix
    # A captured native bail has axis lengths 1.007813/1.000030/1.007782.
    pose.require(math.hypot(*differences) < .02,
                 'Scaled or sheared board transform exceeds presentation correction')
    pivot = pose.transform(bind[12:15], matrix)
    rotated_pivot = pose.transform(bind[12:15], rotation)
    rotation[12:15] = [pivot[i]-rotated_pivot[i] for i in range(3)]
    pose.rigid(rotation)
    pose.require(max(abs(v) for v in rotation[12:15]) < 100000,
                 'Unbounded corrected board transform')
    return rotation


def detached_board_world(calibration, visual_matrix, world_origin, *, conversion_inverse=None):
    """Cancel mesh calibration, then place a native free board in GTA world.

    Board size stays matched to CJ; its world trajectory uses physical inches
    per metre and never receives the rig's unrelated proportion scale/pivot.
    """
    from geometry import thaw_to_gta
    rotation = visual_orientation(visual_matrix)
    shape_scale = calibration['translation_transfer'][2]
    for row in (0,4,8):
        rotation[row:row+3] = [v*shape_scale for v in rotation[row:row+3]]
    axes = [1.,0.,0.,0., 0.,0.,1.,0., 0.,-1.,0.,0., 0.,0.,0.,1.]
    world = pose.multiply(rotation, axes)
    world[12:15] = thaw_to_gta(visual_matrix[12:15], origin=world_origin)
    if conversion_inverse is None:
        conversion_inverse = pose.inverse(visual_conversion(calibration))
    result = pose.multiply(conversion_inverse, world)
    clean = pose.rigid(result); clean[12:15] = result[12:15]
    pose.require(max(abs(v) for v in clean[12:15]) <= 20000, 'Unbounded detached board world position')
    return clean


class GameplayPose:
    def __init__(self, source_bytes, target_bytes):
        pose.require(len(source_bytes) <= 2000000, "Oversized source skeleton")
        self.prepared = transport.prepare_rig(target_bytes)
        self.target_hash = hashlib.sha256(target_bytes).digest()
        self.calibration = pose.prepare(json.loads(source_bytes), self.prepared['target'])
        self.arguments = {key: self.calibration[key] for key in (
            'source_bind', 'target_bind', 'parents', 'order', 'mapping', 'calibration',
            'native_local_rotations', 'rest_corrections', 'collapsed_pairs', 'translation_transfer')}
        self.retargeter = pose.Retargeter(**self.arguments)
        self.visual_conversion = tuple(visual_conversion(self.calibration))
        self.visual_inverse = tuple(pose.inverse(list(self.visual_conversion)))

    def evaluate(self, palette, expected_sha256=None, *, actor_matrix=None, actor_position=None,
                 visual_matrix=None, board_visible=True, board_pose=None, world_origin=(0.,0.,0.)):
        pose.require(isinstance(palette, bytes) and len(palette) == 52 * 64,
                     "Expected one complete native 52-bone palette")
        if expected_sha256 is not None:
            pose.require(hashlib.sha256(palette).hexdigest() == expected_sha256,
                         "Native gameplay palette hash differs")
        values = struct.unpack('<832f', palette)
        pose.require(all(math.isfinite(value) for value in values), "Nonfinite native gameplay palette")
        pose.require(type(board_visible) is bool, 'Original board visibility must be a boolean')
        if board_pose is not None:
            pose.require(not board_visible, 'Attached and detached original boards cannot be presented together')
        palettes = [values[i:i+16] for i in range(0, len(values), 16)]
        board_matrices = []
        for bone, matrix in enumerate(palettes):
            try:
                if bone >= 48 and board_pose is None:
                    board_matrices.append(board_transform(matrix, self.calibration['source_bind'][bone]))
                else:
                    # Detached boards replace these hidden joints; keep source guards,
                    # but apply rigid-render limits only to the board actually shown.
                    pose.source_rotation(matrix)
            except ValueError as error:
                raise ValueError(f"Native source bone {bone}: {error}") from error
        models = self.retargeter.evaluate(palettes)
        calibration = self.calibration
        root = calibration['root_index']
        pose.require(math.dist(models[root][12:15], calibration['target_bind'][root][12:15]) < 1e-8,
                     "Gameplay pose moved the anchored model root")
        pose.require(pose.segment_error(models, calibration['target_bind'], calibration['parents'],
                         excluded=(calibration['translation_transfer'][0],)) < 1e-5,
                     "Gameplay pose changed CJ segment lengths")
        visual_delta = list(pose.IDENTITY)
        if any(v is not None for v in (actor_matrix, actor_position, visual_matrix)):
            pose.require(all(v is not None for v in (actor_matrix, actor_position, visual_matrix)),
                         'Incomplete original model visual transform')
            source_delta = source_visual_delta(actor_matrix, actor_position, visual_matrix)
            visual_delta = pose.multiply(pose.multiply(self.visual_inverse, source_delta), self.visual_conversion)
            clean = pose.rigid(visual_delta); clean[12:15] = visual_delta[12:15]; visual_delta = clean
            pose.require(max(abs(v) for v in visual_delta[12:15]) <= 10, 'Unbounded CJ visual pivot')
            board_matrices = [pose.multiply(matrix, source_delta) for matrix in board_matrices]
            for matrix in board_matrices:
                pose.rigid(matrix)
                pose.require(max(abs(v) for v in matrix[12:15]) <= 10000, 'Unbounded visual board palette')
        board_palette = (struct.pack('<64f', *(v for matrix in board_matrices for v in matrix))
                         if board_pose is None else None)
        board_world, board_object_id = list(pose.IDENTITY), 0
        if board_pose is not None:
            board_palette, board_object_id = board_pose['palette'], board_pose['object_id']
            pose.require(isinstance(board_palette, bytes) and len(board_palette) == 256 and
                         type(board_object_id) is int and 0 < board_object_id <= 0xffffffff,
                         'Invalid original detached board identity/palette')
            detached_values = struct.unpack('<64f', board_palette)
            detached_matrices = [board_transform(detached_values[start:start+16],
                calibration['source_bind'][48+start//16]) for start in range(0,64,16)]
            board_palette = struct.pack('<64f', *(v for matrix in detached_matrices for v in matrix))
            board_world = detached_board_world(calibration, board_pose['visual_matrix'], world_origin,
                                               conversion_inverse=self.visual_inverse)
            board_visible = True
        self.prepared.update(model_bytes=transport.matrix_bytes(models), board_palette=board_palette,
                             visual_delta=visual_delta, board_visible=board_visible,
                             board_world=board_world, board_object_id=board_object_id)
        return self.prepared

    def set_board(self, bundle):
        from board_preview import validate_bundle
        self.prepared['board_hash'] = validate_bundle(bundle, self.prepared['bind_bytes'],
                                                      self.target_hash)
