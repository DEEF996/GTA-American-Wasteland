"""Synthetic metre-space topology tests; no game assets are embedded."""
from copy import deepcopy
import itertools
import math
import unittest

import rails as r


def model(boxes=(), spheres=(), vertices=(), faces=()):
    return dict(boxes=list(boxes), spheres=list(spheres), vertices=list(vertices), faces=list(faces))


def region(value, *, quaternion=(0, 0, 0, 1), position=(0, 0, 0)):
    instance = dict(instance_id=1, model_id=1000, name="fixture", source="synthetic.col",
                    position=list(position), quaternion=list(quaternion), instance_flags=0,
                    interior=0, dynamic=False, conditional=False)
    return dict(format="gta-sa-region-v1", coordinate_system="gta-z-up", center=[0, 0, 0],
                radius=128, interior=0, instances=[instance], models={"fixture": value}, omitted={})


def cube_mesh():
    points = list(itertools.product((-1, 1), (-2, 2), (0, 1)))
    faces = [(a, b, c, 7, 0) for q in r.BOX_QUADS for a, b, c in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))]
    return model(vertices=points, faces=faces)


def concave_mesh():
    outline = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    vertices = [(x, y, z) for z in (0, 1) for x, y in outline]
    triangulation = ((0, 1, 3), (1, 2, 3), (0, 3, 5), (3, 4, 5))
    faces = [(c, b, a, 7, 0) for a, b, c in triangulation]
    faces += [(a+6, b+6, c+6, 7, 0) for a, b, c in triangulation]
    for a in range(6):
        b = (a+1) % 6
        faces += [(a, b, b+6, 7, 0), (a, b+6, a+6, 7, 0)]
    return model(vertices=vertices, faces=faces)


class RailsTests(unittest.TestCase):
    def test_box_roof_retains_material_and_source_primitive(self):
        graph = r.generate(region(model(boxes=[[-1, -2, 0, 1, 2, 1, 7, 0, 0, 0]])))
        self.assertEqual(graph["stats"]["rail_chains"], 4)
        self.assertAlmostEqual(sum(rail["length_metres"] for rail in graph["rails"]), 12)
        for rail in graph["rails"]:
            self.assertTrue(all(point[2] == 1 for point in rail["points_gta"]))
            self.assertEqual(rail["gta_surface_material"], 7)
            self.assertEqual(rail["sources"][0]["primitive"], "box")
            self.assertIn("z+", rail["sources"][0]["supporting_faces"])
        self.assertFalse(graph["runtime_tested"])
        self.assertFalse(graph["thaw_qb_emitted"])
        self.assertIsNone(graph["source_region"]["scene_stamp"])

    def test_closed_convex_mesh_and_inward_winding(self):
        mesh = cube_mesh()
        graph = r.generate(region(mesh))
        self.assertEqual(graph["stats"]["rail_chains"], 4)
        self.assertTrue(all(s["primitive"] == "mesh" for rail in graph["rails"] for s in rail["sources"]))
        mesh["faces"] = [(c, b, a, m, l) for a, b, c, m, l in mesh["faces"]]
        flipped = r.generate(region(mesh))
        self.assertEqual(flipped["stats"]["rail_chains"], 4)
        self.assertEqual(flipped["stats"]["inward_components_reoriented"], 1)

    def test_concave_component_keeps_only_local_roof_ridges(self):
        graph = r.generate(region(concave_mesh()))
        self.assertEqual(len(graph["rails"]), 6)
        self.assertTrue(all(point[2] == 1 for rail in graph["rails"] for point in rail["points_gta"]))
        self.assertEqual(graph["stats"]["nonconvex_components_without_verified_volume"], 1)
        self.assertGreater(graph["stats"]["locally_concave_edges_excluded"], 0)

    def test_open_roof_wall_pair_retains_original_material_and_supporting_faces(self):
        mesh = model(vertices=[(0,0,1),(2,0,1),(0,2,1),(0,0,0)],
                     faces=[(0,1,2,7,0),(1,0,3,9,0)])
        for inward in (False, True):
            value = deepcopy(mesh)
            if inward:
                value["faces"] = [(c,b,a,m,l) for a,b,c,m,l in value["faces"]]
            graph = r.generate(region(value))
            self.assertEqual(len(graph["rails"]), 1)
            self.assertEqual(graph["rails"][0]["gta_surface_material"], 7)
            self.assertEqual(graph["rails"][0]["sources"][0]["supporting_faces"], [0,1])
            self.assertEqual(set(graph["rails"][0]["points_gta"]), {(0,0,1),(2,0,1)})
        for edit in ("valley", "flat", "nonmanifold", "winding"):
            value = deepcopy(mesh)
            if edit == "valley":
                value["vertices"][3] = (0,0,2)
            elif edit == "flat":
                value["vertices"][3] = (0,-2,1)
            elif edit == "nonmanifold":
                value["faces"].append((1,0,3,9,0))
                value["faces"][-1] = (0,1,3,9,0)
            else:
                value["faces"][1] = (0,1,3,9,0)
            with self.subTest(edit=edit):
                self.assertFalse(any(set((a,b)) == {(0,0,1),(2,0,1)}
                    for chain in r.generate(region(value))["rails"]
                    for a,b in zip(chain["points_gta"],chain["points_gta"][1:])))

    def test_open_fence_upper_border_not_bottom_or_diagonal(self):
        fence = model(vertices=[(0,0,0),(4,0,0),(4,0,2),(0,0,2)],
                      faces=[(0,1,2,66,0),(0,2,3,66,0)])
        for inward in (False,True):
            value = deepcopy(fence)
            if inward:
                value["faces"] = [(c,b,a,m,l) for a,b,c,m,l in value["faces"]]
            graph = r.generate(region(value))
            self.assertEqual(len(graph["rails"]),1)
            self.assertEqual(set(graph["rails"][0]["points_gta"]),{(0,0,2),(4,0,2)})
            self.assertEqual(graph["rails"][0]["sources"][0]["edge_evidence"],"open_panel_top")
        fence["faces"].append((0,2,1,66,0))
        self.assertEqual(r.generate(region(fence))["rails"],[])

    def test_narrow_planar_track_strips_join_without_crossbars_or_diagonals(self):
        strip = model(vertices=[(x,y,1+x*.01) for x in (0,4,8) for y in (0,.1015625)],
                      faces=[(0,2,3,178,0),(0,3,1,178,0),(2,4,5,178,0),(2,5,3,178,0)])
        for inward in (False,True):
            value = deepcopy(strip)
            if inward:
                value["faces"] = [(c,b,a,m,l) for a,b,c,m,l in value["faces"]]
            graph = r.generate(region(value))
            self.assertEqual(len(graph["rails"]),2)
            self.assertTrue(all(len(chain["points_gta"]) == 3 for chain in graph["rails"]))
            self.assertEqual({p for chain in graph["rails"] for p in chain["points_gta"]},set(strip["vertices"]))
            self.assertTrue(all(source["edge_evidence"] == "narrow_strip_border"
                                for chain in graph["rails"] for source in chain["sources"]))
        for edit in ("wide_road","nonmanifold","material_seam"):
            value = deepcopy(strip)
            if edit == "wide_road":
                value["vertices"] = [(x,y*40,z) for x,y,z in value["vertices"]]
            elif edit == "nonmanifold":
                value["faces"] += [(3,2,0,178,0),(5,4,2,178,0)]
            else:
                value["faces"][1] = (0,3,1,1,0)
                value["faces"][3] = (2,5,3,1,0)
            self.assertEqual(r.generate(region(value))["rails"],[],edit)

    def test_wide_roof_hole_has_no_narrow_strip_edges(self):
        roof = model(vertices=[(x,y,2) for x,y in [(-5,-5),(5,-5),(5,5),(-5,5),(-1,-1),(1,-1),(1,1),(-1,1)]],
            faces=[(a,b,b+4,0,0) for a,b in [(0,1),(1,2),(2,3),(3,0)]]+
                  [(a,b+4,a+4,0,0) for a,b in [(0,1),(1,2),(2,3),(3,0)]])
        self.assertEqual(r.generate(region(roof))["rails"],[])

    def test_rotation_uses_gta_quaternion_and_preserves_metres(self):
        graph = r.generate(region(cube_mesh(), quaternion=(0, 0, math.sqrt(.5), math.sqrt(.5)), position=(10, 20, 30)))
        points = [p for rail in graph["rails"] for p in rail["points_gta"]]
        low, high = r.bounds(points)
        for actual, expected in zip(low+high, (8, 19, 31, 12, 21, 31)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(sum(rail["length_metres"] for rail in graph["rails"]), 12)

    def test_spheres_generate_no_edges_and_occlude_buried_roof(self):
        graph = r.generate(region(model(spheres=[[0, 0, 0, 10, 1, 0, 0, 0]])))
        self.assertEqual(graph["rails"], [])
        self.assertEqual(graph["stats"]["spheres_without_artificial_edges"], 1)
        graph = r.generate(region(model(boxes=[[-1, -2, 0, 1, 2, 1, 7, 0, 0, 0]],
                                        spheres=[[0, 0, 0, 10, 1, 0, 0, 0]])))
        self.assertEqual(graph["rails"], [])
        self.assertEqual(graph["stats"]["occluded_edges_excluded"], 4)

    def test_dynamic_conditional_missing_primitives_and_whole_chain_budget(self):
        for field in ("dynamic", "conditional"):
            source = region(cube_mesh())
            source["instances"][0][field] = True
            graph = r.generate(source)
            self.assertEqual(graph["rails"], [])
            self.assertEqual(graph["stats"][field+"_instances_excluded"], 1)
        source = region(cube_mesh())
        del source["models"]["fixture"]["boxes"]
        with self.assertRaisesRegex(ValueError, "missing original model primitives"):
            r.generate(source)
        graph = r.generate(region(cube_mesh()), maximum_nodes=2)
        self.assertEqual(graph["stats"]["rail_nodes"], 2)
        self.assertTrue(graph["budget"]["applied"])
        self.assertEqual(graph["budget"]["dropped_chains"], 3)
        self.assertEqual(graph["budget"]["dropped_nodes"], 6)
        source = region(cube_mesh())
        source["instances"].append(deepcopy(source["instances"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate region instance"):
            r.generate(source)

    def test_smooth_chain_joining_keeps_sharp_corners_and_material_seams(self):
        def segment(a, b, material=1):
            return dict(points=[a, b], material=material, source={"fixture": str((a, b))})
        segments = [segment((0, 0, 1), (1, 0, 1)), segment((1, 0, 1), (2, .01, 1)),
                    segment((2, .01, 1), (2, 1, 1)), segment((2, 1, 1), (2, 2, 1), 2)]
        stats = r.Counter()
        chains, budget = r.chain_segments(segments, .002, .6, 20, 100, stats)
        self.assertEqual(len(chains), 3)
        self.assertEqual(max(len(chain["points_gta"]) for chain in chains), 3)
        self.assertFalse(budget["applied"])

    def test_exact_open_surface_crossings_and_buried_box_edges_are_excluded(self):
        source = region(model(boxes=[[-1, -2, 0, 1, 2, 1, 7, 0, 0, 0],
                                    [-2, -3, -.5, 2, 3, 2, 7, 0, 0, 0]]))
        graph = r.generate(source)
        self.assertEqual(graph["stats"]["occluded_edges_excluded"], 4)
        self.assertTrue(all(p[2] == 2 for rail in graph["rails"] for p in rail["points_gta"]))
        source = region(model(boxes=[[-1, -2, 0, 1, 2, 1, 7, 0, 0, 0]],
                              vertices=[(-3, -3, 0), (3, -3, 3), (0, 3, 0)],
                              faces=[(0, 1, 2, 1, 0)]))
        graph = r.generate(source)
        self.assertEqual(len(graph["rails"]),2)
        self.assertEqual(graph["stats"]["occluded_edges_excluded"],2)

    def test_open_ground_bounds_do_not_occlude_elevated_track(self):
        source = model(vertices=[(0,0,1),(4,0,1),(4,.1,1),(0,.1,1),
                                 (-8,-8,-1),(8,-8,1.5),(-8,8,1.5)],
                       faces=[(0,1,2,178,0),(0,2,3,178,0),(4,5,6,1,0)])
        graph = r.generate(region(source))
        self.assertEqual(len(graph["rails"]),2)
        self.assertTrue(all(p[2] == 1 for rail in graph["rails"] for p in rail["points_gta"]))
        self.assertFalse(r.segment_crosses_triangle((0,0,1),(1,0,1),[(0,0,1),(0,2,0),(0,2,2)],.002))
        self.assertTrue(r.segment_crosses_triangle((-1,0,1),(1,0,1),[(0,-2,0),(0,2,0),(0,0,3)],.002))


if __name__ == "__main__":
    unittest.main()
