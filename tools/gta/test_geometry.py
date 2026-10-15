"""Synthetic format/transform/budget tests; no copyrighted fixtures required."""
import json
import math
from contextlib import closing
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

import geometry as g


def col_fixture(version=3):
    header = 120 if version == 3 else 108
    sphere = struct.pack("<4f4B", 0, 0, 0, 1, 3, 0, 0, 0)
    box = struct.pack("<6f4B", -1, -2, -3, 1, 2, 3, 4, 0, 0, 0)
    vertices = struct.pack("<9h", 0, 0, 0, 128, 0, 0, 0, 128, 0)
    face = struct.pack("<3H2B", 0, 1, 2, 5, 6)
    raw = bytearray(header)
    raw += sphere + box + vertices + face
    struct.pack_into("<4sI22sH", raw, 0, f"COL{version}".encode(), len(raw)-8, b"fixture", 1000)
    struct.pack_into("<10f", raw, 32, -1, -2, -3, 1, 2, 3, 0, 0, 0, 4)
    struct.pack_into("<HHHBx7I", raw, 72, 1, 1, 1, 0, 2,
                     header-4, header+16, 0, header+44, header+62, 0)
    return bytes(raw)


def binary_ipl(rows):
    raw = bytearray(76)
    struct.pack_into("<4sI", raw, 0, b"bnry", len(rows))
    struct.pack_into("<I", raw, 28, 76)
    for xyz, quat, model, packed, lod in rows:
        raw.extend(struct.pack("<7fiIi", *xyz, *quat, model, packed, lod))
    return bytes(raw)


def img_fixture(entries):
    header = bytearray(2048)
    struct.pack_into("<4sI", header, 0, b"VER2", len(entries))
    payload = bytearray()
    for number, (name, data) in enumerate(entries):
        sectors = (len(data) + 2047) // 2048
        struct.pack_into("<II24s", header, 8 + 32*number, 1+len(payload)//2048, sectors, name.encode())
        payload += data + bytes(sectors*2048-len(data))
    return bytes(header + payload)


class GeometryTests(unittest.TestCase):
    def test_vertical_actor_yaw_retains_wall_direction_and_rejects_invalid_basis(self):
        for yaw in (0., .7, -1.4):
            c, s = math.cos(yaw), math.sin(yaw)
            vertical = [c,0.,-s,0., -s,0.,-c,0., 0.,1.,0.,0., 100000.,0.,-120000.,1.]
            self.assertAlmostEqual(g.source_horizontal_forward(vertical)[0], s)
            self.assertAlmostEqual(g.source_horizontal_forward(vertical)[1], c)
        for bad in ([0.]*16, [float('nan')]*16,
                    [-1.,0.,0.,0., 0.,1.,0.,0., 0.,0.,1.,0., 0.,0.,0.,1.]):
            with self.assertRaises(ValueError): g.source_horizontal_forward(bad)

    def test_col2_col3_and_primitive_materials(self):
        for version in (2, 3):
            result, = g.parse_col(col_fixture(version) + bytes(19))
            self.assertEqual(result["version"], version)
            self.assertEqual(result["vertices"][1], [1, 0, 0])
            self.assertEqual(result["faces"], [[0, 1, 2, 5, 6]])
            self.assertEqual(result["spheres"][0][3:5], [1, 3])
            self.assertEqual(result["boxes"][0][6], 4)

    def test_col_boundary_checks(self):
        raw = bytearray(col_fixture())
        for truncated in (bytes(raw[:32]), bytes(raw[:-1])):
            with self.assertRaises(ValueError):
                list(g.parse_col(truncated))
        struct.pack_into("<I", raw, 100, 0xffffffff)
        with self.assertRaises(ValueError):
            list(g.parse_col(raw))
        with self.assertRaises(ValueError):
            list(g.parse_col(col_fixture() + b"\0\0\0\0BAD"))

    def test_legacy_primitives(self):
        raw = bytearray(72)
        struct.pack_into("<10f", raw, 32, 4, 0, 0, 0, -1, -2, -3, 1, 2, 3)
        raw += struct.pack("<I4f4B", 1, 1, 0, 0, 0, 9, 0, 0, 0)
        raw += struct.pack("<III", 0, 0, 3)
        raw += struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
        raw += struct.pack("<I3I4B", 1, 0, 1, 2, 8, 0, 0, 7)
        struct.pack_into("<4sI22sH", raw, 0, b"COLL", len(raw)-8, b"fixture", 1000)
        result, = g.parse_col(raw)
        self.assertEqual(result["spheres"][0][:5], [0, 0, 0, 1, 9])
        self.assertEqual(result["faces"], [[0, 1, 2, 8, 7]])

    def test_binary_and_text_ipl_packed_area(self):
        binary = binary_ipl([((1, 2, 3), (0, 0, 0, 1), 1000, 0x40d, -1)])
        entry, = g.parse_ipl(binary)
        self.assertEqual(entry["interior"], 13)
        self.assertEqual(entry["instance_flags"], 0x40d)
        text, = g.parse_ipl(b"inst\n1000,fixture,1037,1,2,3,0,0,0,1,-1\nend\n")
        self.assertEqual(entry, {**text, "name": ""})
        with self.assertRaises(ValueError):
            list(g.parse_ipl(binary[:-1]))
        invalid = bytearray(binary)
        struct.pack_into("<I", invalid, 28, 4)
        with self.assertRaises(ValueError):
            list(g.parse_ipl(invalid))

    def test_quaternion_conjugation_and_world_bounds(self):
        q = (0, 0, math.sqrt(.5), math.sqrt(.5))
        matrix = g.rotation_matrix(q)
        actual = g.transform((1, 0, 0), matrix, (10, 20, 30))
        for a, b in zip(actual, (10, 19, 30)):
            self.assertAlmostEqual(a, b)
        instance = g.placement(1000, (10, 20, 30), q, 0, -1)
        low, high = g.world_bounds(((-1, -2, -3), (1, 2, 3)), instance)
        for a, b in zip(low+high, (8, 19, 27, 12, 21, 33)):
            self.assertAlmostEqual(a, b)
        tilt = g.rotation_matrix((math.sqrt(.5), 0, 0, math.sqrt(.5)))
        self.assertAlmostEqual(g.transform((0, 1, 0), tilt, (0, 0, 0))[2], -1)
        self.assertEqual(g.gta_to_thaw((11, 22, 33), 2, (10, 20, 30)), [2, 6, -4])
        with self.assertRaises(ValueError):
            g.gta_to_thaw((0, 0, 0), float("nan"))

    def test_native_units_momentum_and_timestep_independence(self):
        origin = (2240., -1262., 24.)
        # A 12-inch vertical span must remain one foot at any world origin.
        self.assertAlmostEqual(g.gta_to_thaw((2240., -1262., 24.3048), origin=origin)[1], 12.)
        position = (2241., -1264., 27.)
        thaw = g.gta_to_thaw(position, origin=origin)
        for actual, expected in zip(g.thaw_to_gta(thaw, origin=origin), position):
            self.assertAlmostEqual(actual, expected)
        raw = (.1, -.2, .3)
        velocity = g.gta_velocity_to_thaw(raw)
        for actual, expected in zip(g.thaw_velocity_to_gta(velocity), raw):
            self.assertAlmostEqual(actual, expected)
        # Compare GTA's raw v*time_step integration with seconds-based THAW
        # integration. Multiplying either velocity by render FPS fails this.
        for fps in (30, 60, 120, 144):
            timestep = 50 / fps
            advanced = [position[i] + raw[i] * timestep for i in range(3)]
            transformed = g.gta_to_thaw(advanced, origin=origin)
            for axis in range(3):
                self.assertAlmostEqual(transformed[axis], thaw[axis] + velocity[axis] / fps)
        for value in ((0., 1.), (0., float('nan'), 0.), (0., float('inf'), 0.)):
            for function in (g.gta_to_thaw, g.thaw_to_gta,
                             g.gta_velocity_to_thaw, g.thaw_velocity_to_gta):
                with self.assertRaises(ValueError):
                    function(value)

    def test_optional_stream_interiors_dynamic_and_budgets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/"game"
            (root/"data").mkdir(parents=True)
            (root/"models").mkdir()
            (root/"data/default.dat").write_text("IDE data/objects.ide\n")
            (root/"data/gta.dat").write_text("IPL data/base.ipl\n")
            (root/"data/object.dat").write_text("; no moving model definitions\n")
            (root/"data/objects.ide").write_text("objs\n1000, fixture, tex, 150, 0\nend\n")
            (root/"data/base.ipl").write_text("inst\n1000,fixture,0,0,0,0,0,0,0,1,-1\n"
                                              "1000,fixture,1,0,0,0,0,0,0,1,-1\nend\n")
            stream = binary_ipl([((0, 0, 0), (0, 0, 0, 1), 1000, 13, -1)])
            optional = binary_ipl([((0, 0, 0), (0, 0, 0, 1), 1000, 0, -1)])
            (root/"models/gta3.img").write_bytes(img_fixture([("fixture.col", col_fixture()),
                                                            ("base_stream0.ipl", stream),
                                                            ("mission.ipl", optional)]))
            (root/"models/gta_int.img").write_bytes(img_fixture([]))
            db = Path(temp)/"world.db"
            report = g.build_world(root, db)
            self.assertEqual(report["stats"]["instances"], 4)
            self.assertEqual(report["conditional_streams"], {"mission": 1})
            result = g.query_world(db, (0, 0, 0), 10)
            self.assertEqual(result["stats"]["instances"], 2)
            self.assertEqual({row['interior'] for row in result['instances']}, {0, 13})
            self.assertEqual(result["omitted"], {"inactive_conditional": 1})
            active = g.query_world(db, (0, 0, 0), 10, active_streams=["mission"])
            self.assertEqual(active["stats"]["instances"], 3)
            interior = g.query_world(db, (0, 0, 0), 10, interior=13)
            self.assertEqual(interior["stats"]["instances"], 1)
            room = g.query_world(db, (0, 0, 0), 10, interior=1)
            self.assertEqual({row['interior'] for row in room['instances']}, {1, 13})
            self.assertEqual(len(room['instances']), 2)
            other = g.query_world(db, (0, 0, 0), 10, interior=2)
            self.assertEqual([row['interior'] for row in other['instances']], [13])
            with self.assertRaisesRegex(ValueError, "primitive budget"):
                g.query_world(db, (0, 0, 0), 10, max_primitives=2)
            with self.assertRaisesRegex(ValueError, "overwrite"):
                g.build_world(root, db)
            with self.assertRaisesRegex(ValueError, "outside source"):
                g.build_world(root, root/"world.db")
            with closing(sqlite3.connect(db)) as sql:
                row_id, payload = sql.execute("SELECT id,payload FROM instances WHERE area=0 AND conditional=0").fetchone()
                payload = json.loads(payload)
                payload["dynamic"] = True
                sql.execute("UPDATE instances SET payload=? WHERE id=?", (json.dumps(payload), row_id))
                sql.commit()
            self.assertEqual(g.query_world(db, (0, 0, 0), 10)["stats"]["instances"], 1)
            self.assertEqual(g.query_world(db, (0, 0, 0), 10, include_dynamic=True)["stats"]["instances"], 2)
            with closing(sqlite3.connect(db)) as sql:
                sql.execute("UPDATE metadata SET value='false' WHERE key='complete'")
                sql.commit()
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                g.query_world(db, (0, 0, 0), 10)

    def test_time_windows_and_path_escape(self):
        self.assertTrue(g.active_hour([20, 6], 23))
        self.assertTrue(g.active_hour([20, 6], 3))
        self.assertFalse(g.active_hour([20, 6], 12))
        self.assertTrue(g.active_hour([6, 20], 12))
        self.assertFalse(g.active_hour([6, 20], 20))
        for path in ("../elsewhere", "C:\\elsewhere", "\\elsewhere"):
            with self.assertRaises(ValueError):
                g.local_path(Path.cwd(), path)


if __name__ == "__main__":
    unittest.main()
