"""Conservative authored material policy; native THAW still owns sound/friction.

GTA IDs match stock data/surfinfo.dat (sha256
431b95d4dcb20ce5258f7d939902c56d50fb4ee68b30ed7a044919c666291b55)
and gta-reversed/source/game_sa/Enums/eSurfaceType.h. THAW names/indices were
read from the complete pinned scripts/game/terrain.qb.wpc (dfc4310c...75adb)
and its stock checksum dictionary; probe_terrain verifies that source at boot.
Only named material families with a direct counterpart are assigned. Ambiguous
GTA procedural/mixed surfaces and unknown IDs preserve TERRAIN_DEFAULT.
"""
import hashlib
import math
from pathlib import Path

# Stock scripts/game/skutils.qb.wpc defines mFD_WALL_RIDABLE as 4.
# Stock contact5D3D60 copies this bit to Core+3EA; wallride5D70B3 tests it.
# GTA has no authored THAW flags. This automatic near-vertical policy matches
# the existing CoD adapter; it does not change the native movement solver.
WALL_RIDABLE = 4
WALLRIDE_MAX_DEGREES_FROM_VERTICAL = 5.
WALLRIDE_NORMAL_Y_SQUARED = math.sin(math.radians(WALLRIDE_MAX_DEGREES_FROM_VERTICAL)) ** 2


def wallride_flag(normal):
    length_squared = sum(value * value for value in normal)
    return WALL_RIDABLE if length_squared > 0 and normal[1] ** 2 <= WALLRIDE_NORMAL_Y_SQUARED * length_squared else 0


def wallride_policy():
    return dict(face_flag=WALL_RIDABLE,
        maximum_degrees_from_vertical=WALLRIDE_MAX_DEGREES_FROM_VERTICAL,
        authored_from_final_world_normals=True, native_solver_unchanged=True)


def surface_policy(flags):
    return {**policy(), 'thaw_face_flags': sorted(set(flags)),
            'automatic_wallride': wallride_policy()}

# Numeric values are stock THAW's authored terrain-array order, not GTA IDs.
TERRAINS = {
    0: 'TERRAIN_DEFAULT', 1: 'TERRAIN_CONCSMOOTH', 2: 'TERRAIN_CONCROUGH',
    3: 'TERRAIN_METALSMOOTH', 7: 'TERRAIN_METALTIN', 8: 'TERRAIN_WOOD',
    11: 'TERRAIN_WOODFLIMSY', 16: 'TERRAIN_ASPHALT', 17: 'TERRAIN_ROCK',
    18: 'TERRAIN_GRAVEL', 19: 'TERRAIN_SIDEWALK', 20: 'TERRAIN_GRASS',
    21: 'TERRAIN_GRASSDRIED', 22: 'TERRAIN_DIRT', 27: 'TERRAIN_SAND',
    30: 'TERRAIN_CARPET', 32: 'TERRAIN_CHAINLINK', 38: 'TERRAIN_METALFENCE',
    39: 'TERRAIN_GRINDTRAIN', 42: 'TERRAIN_GRINDCONC',
    43: 'TERRAIN_GRINDROUNDMETALPOLE', 44: 'TERRAIN_GRINDCHAINLINK',
    45: 'TERRAIN_GRINDMETAL', 46: 'TERRAIN_GRINDWOODRAILING',
    48: 'TERRAIN_GRINDWOOD',
}

# (GTA IDs, ground terrain, rail Type, rail TerrainType). Rail Type remains
# Concrete for natural ground without a demonstrated authored grind material.
GROUPS = (
    ((1, 2, 3), 16, 'Concrete', 42),                    # tarmac
    ((4, 5), 19, 'Concrete', 42),                       # pavement
    ((6,), 18, 'Concrete', 0),                         # gravel
    ((7,), 2, 'Concrete', 42),                         # rough concrete
    ((9, 10, 11, 15, 16, 17, 20, 80, 81, 160), 20, 'Concrete', 0),
    ((12, 13, 14, 82), 21, 'Concrete', 0),             # dry grass
    ((26, 27), 22, 'Concrete', 0),                     # dirt
    # Stock zone banks omit RollSand and GrindTrain; keep audible existing routes.
    ((28, 29, 30, 31, 32, 33, 74, 75, 76, 77, 79), 0, 'Concrete', 0),
    ((34, 89, 138, 165), 1, 'Concrete', 42),           # concrete
    ((35, 36, 37, 161), 17, 'Concrete', 0),            # rock / stone stairs
    ((42, 43, 70, 72, 73), 8, 'Wood', 48),            # crates / wood / floorboards
    ((44,), 11, 'Wood', 48),                          # thin wood
    ((51, 56, 162, 164, 168, 171), 3, 'Metal', 45),    # metal plate/girder/floor/barrel
    ((52, 53), 3, 'Metal', 43),                       # scaffold / lamp poles
    ((54,), 38, 'Metal', 45),                         # metal gate
    ((55,), 32, 'Metal', 44),                         # chain-link fence
    ((71, 163), 30, 'Concrete', 0),                   # carpet
    ((167,), 7, 'Metal', 45),                         # thin metal sheet
    ((172, 173, 174), 8, 'Wood', 46),                 # named wood fences
    ((178,), 3, 'Metal', 45),                         # railway steel
)
MATERIALS = {material: (ground, kind, grind)
             for ids, ground, kind, grind in GROUPS for material in ids}
RAIL_PAIRS = frozenset((kind, TERRAINS[grind]) for _, kind, grind in MATERIALS.values())
RAIL_PAIRS = RAIL_PAIRS | {('Concrete', 'TERRAIN_DEFAULT')}


def fingerprint():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _material(value):
    if type(value) is not int or not 0 <= value <= 255:
        raise ValueError('GTA material must be an unsigned byte')
    return MATERIALS.get(value, (0, 'Concrete', 0))


def ground(value):
    return _material(value)[0]


def rail(value):
    _, kind, grind = _material(value)
    return kind, TERRAINS[grind]


def policy():
    return dict(thaw_face_flags=0, material_mapping_sha256=fingerprint(),
                terrain_source_sha256='dfc4310c1d04bde8c78a03ab938a805496e752ef7667769cf15c74bfd1e75adb',
                unknown_terrain=0, material_mapping_verified=True,
                native_material_sounds_tested=False)
