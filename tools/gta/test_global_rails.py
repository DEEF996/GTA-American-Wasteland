"""Small synthetic whole-world cache and original stock QB export checks."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zlib

import geometry as g
import global_rails as r
import rail_export
import rails
import scene_export
from test_rails import cube_mesh, model as geometry_model


def fixture(path, placements):
    model = dict(cube_mesh(), name="fixture", model_id_hint=1000, bounds=[[-1,-2,0],[1,2,1]])
    raw = g.encoded(model)
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE models(name TEXT,payload BLOB,sha256 TEXT,source TEXT);"
        "CREATE TABLE instances(id INTEGER PRIMARY KEY,name TEXT,has_collision INTEGER,dynamic INTEGER,payload TEXT);")
    db.executemany("INSERT INTO metadata VALUES (?,?)", (("schema",json.dumps(g.SCHEMA)),("complete","true")))
    db.execute("INSERT INTO models VALUES (?,?,?,?)", ("fixture",zlib.compress(raw),hashlib.sha256(raw).hexdigest(),"fixture.col"))
    for ident, changes in enumerate(placements, 1):
        instance = dict(g.placement(1000, (ident*20,0,0), (0,0,0,1), 0,-1,"fixture"),
            source="fixture.ipl", stream="fixture", conditional=False, dynamic=False,
            definition=dict(time_window=None))
        instance.update(changes)
        db.execute("INSERT INTO instances VALUES (?,?,?,?,?)", (ident,"fixture",1,int(instance["dynamic"]),g.encoded(instance).decode()))
    db.commit()
    db.close()


class GlobalRailsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.world = Path(self.folder.name)/"world.db"
        self.output = Path(self.folder.name)/"rails.db"

    def prepare(self, placements):
        fixture(self.world, placements)
        report = r.build_world_rails(self.world,self.output)
        cache = r.RailCache(self.output, world_sha256=r._digest(self.world))
        self.addCleanup(cache.close)
        return report, cache

    def test_classifies_one_model_once_and_queries_without_geometry_classification(self):
        original = rails.generate
        with patch.object(rails,"generate",wraps=original) as generate:
            report, cache = self.prepare([{},{}])
            self.assertEqual(generate.call_count,1)
        self.assertEqual(report["stats"]["rail_chains"],8)
        with patch.object(rails,"generate",side_effect=AssertionError("runtime classification")):
            graph = cache.query([20,0,1],5)
        self.assertEqual(len(graph["rails"]),4)
        self.assertEqual(graph["stats"]["rail_chains"],4)
        self.assertEqual(graph["stats"]["rail_nodes"],8)
        self.assertEqual(graph["stats"]["rail_segments"],4)
        self.assertFalse(graph["edge_classification_at_runtime"])
        self.assertFalse(graph["budget"]["applied"])
        for chain in graph["rails"]:
            self.assertTrue(all(point[2] == 1 for point in chain["points_gta"]))
            self.assertIn([0.,0.,1.], chain["sources"][0]["supporting_normals_gta"])

    def test_area_13_time_windows_conditional_activation_and_dynamic_placement(self):
        _, cache = self.prepare([{},dict(interior=13,position=[20,0,0]),
            dict(interior=2,position=[20,0,2]),dict(position=[20,0,4],definition=dict(time_window=[18,6])),
            dict(position=[20,0,6],conditional=True,stream="optional"),dict(dynamic=True)])
        graph = cache.query([20,0,4],10,0,hour=12)
        self.assertEqual({source["instance_id"] for chain in graph["rails"] for source in chain["sources"]},{1,2})
        graph = cache.query([20,0,4],10,0,hour=20,active_streams=["optional"])
        self.assertEqual({source["instance_id"] for chain in graph["rails"] for source in chain["sources"]},{1,2,4,5})
        graph = cache.query([20,0,4],10,2,hour=12)
        self.assertEqual({source["instance_id"] for chain in graph["rails"] for source in chain["sources"]},{2,3})

    def test_tilted_instance_reclassified_in_world_up_and_heading_rotated_without_reclassification(self):
        original = rails.generate
        with patch.object(rails,"generate",wraps=original) as generate:
            _,cache = self.prepare([dict(quaternion=[0,0,math.sqrt(.5),math.sqrt(.5)]),
                dict(position=[20,0,4],quaternion=[math.sin(math.pi/12),0,0,math.cos(math.pi/12)])])
            self.assertEqual(generate.call_count,2)
        graph=cache.query([20,0,3],10)
        self.assertEqual({source["instance_id"] for chain in graph["rails"] for source in chain["sources"]},{1,2})

    def test_precomputed_selection_exports_original_qb_with_matching_scene_identity(self):
        _,cache = self.prepare([{}])
        graph=cache.query([20,0,0],8,hour=12)
        model=dict(cube_mesh(),name="fixture",flags=2)
        region=dict(format="gta-sa-region-v1",coordinate_system="gta-z-up",center=[20,0,0],radius=8,
            interior=0,instances=[dict(g.placement(1000,[20,0,0],[0,0,0,1],0,-1,"fixture"),
                instance_id=1,source="fixture.ipl",stream="fixture",dynamic=False,conditional=False,
                definition=dict(time_window=None))],models={"fixture":model},omitted={})
        _,scene=scene_export.build_scene(region,primitive_policy=scene_export.REJECT_SPHERES,hour=12)
        graph=cache.query([20,0,0],8,hour=12,scene=scene)
        payload,report=rail_export.build_rails(graph,scene)
        self.assertEqual(report["node_count"],8)
        self.assertEqual(len(rail_export.read_nodes(payload)),8)
        digest,stamp=r.cache_identity(self.output,world_sha256=r._digest(self.world))
        prepared=r.prepare_rails(self.output,r._digest(self.world),scene,cache_sha256=digest,stamp=stamp)
        self.assertEqual(prepared["report"]["node_count"],8)
        self.assertTrue(prepared["report"]["basename"].startswith("gta_r_"))
        self.assertEqual(len(rail_export.read_nodes(prepared["source"],basename=prepared["report"]["basename"])),8)
        other_origin=deepcopy(scene)
        other_origin["coordinates"]["origin_gta"]=[19,0,0]
        translated=r.prepare_rails(self.output,r._digest(self.world),other_origin,cache_sha256=digest,stamp=stamp)
        self.assertNotEqual(prepared["report"]["basename"],translated["report"]["basename"])
        self.assertNotEqual(prepared["source"],translated["source"])
        original_query=r.RailCache.query
        def dense_selection(cache,center,radius,*args,**kwargs):
            if radius == 8:
                raise ValueError("Precomputed rails exceed stock node budget; select a smaller complete region")
            return original_query(cache,center,radius,*args,**kwargs)
        with patch.object(r.RailCache,"query",new=dense_selection):
            smaller=r.prepare_rails(self.output,r._digest(self.world),scene,cache_sha256=digest,stamp=stamp)
        self.assertEqual(smaller["report"]["node_count"],8)
        self.assertEqual(smaller["selection"]["radius"],4)
        self.assertEqual(smaller["selection"]["requested_radius"],8)
        self.assertTrue(smaller["selection"]["node_budget_reduced_radius"])
        changed=deepcopy(scene)
        changed["selection"]["interior"]=2
        with self.assertRaisesRegex(ValueError,"differ"):
            cache.query([20,0,0],8,hour=12,scene=changed)

    def test_duplicate_world_instances_merge_without_duplicate_native_segments(self):
        _,cache=self.prepare([{},dict(position=[20,0,0])])
        graph=cache.query([20,0,0],8)
        self.assertEqual(len(graph["rails"]),4)
        self.assertEqual({source["instance_id"] for chain in graph["rails"] for source in chain["sources"]},{1,2})

    def test_crossing_chain_retains_complete_original_segments_inside_region(self):
        _, cache = self.prepare([{}])
        cache.close()
        with sqlite3.connect(self.output) as db:
            ident, payload = db.execute("SELECT id,payload FROM chains ORDER BY id LIMIT 1").fetchone()
            chain = json.loads(payload)
            chain["points_gta"] = [[18,0,1],[20,0,1],[22,0,1],[26,0,1]]
            db.execute("UPDATE chains SET payload=? WHERE id=?", (g.encoded(chain).decode(), ident))
            db.execute("UPDATE rail_bounds SET minx=18,maxx=26,miny=0,maxy=0,minz=1,maxz=1 WHERE id=?", (ident,))
        db.close()
        cache = r.RailCache(self.output)
        self.addCleanup(cache.close)
        graph = cache.query([20,0,1],3)
        selected = next(c for c in graph["rails"] if (20,0,1) in c["points_gta"])
        self.assertEqual(set(selected["points_gta"]), {(18,0,1),(20,0,1),(22,0,1)})
        self.assertGreater(graph["source_region"]["omitted"]["crossing_region_segments"],0)

    def test_extent_budget_and_cache_identity_fail_explicitly(self):
        _,cache=self.prepare([{}])
        with self.assertRaisesRegex(ValueError,"node budget"):
            cache.query([20,0,0],8,maximum_nodes=2)
        with self.assertRaisesRegex(ValueError,"another GTA"):
            r.RailCache(self.output,world_sha256="0"*64)
        with self.assertRaisesRegex(ValueError,"new rail-cache"):
            r.build_world_rails(self.world,self.output)

    def test_crossing_full_chain_requires_every_supporting_face_in_exact_col(self):
        fixture(self.world,[{}])
        mesh = cube_mesh()
        mesh['vertices'] = [(x*8,y,z) for x,y,z in mesh['vertices']]
        mesh.update(name='fixture',model_id_hint=1000,bounds=[[-8,-2,0],[8,2,1]],flags=2)
        raw = g.encoded(mesh)
        with sqlite3.connect(self.world) as db:
            db.execute('UPDATE models SET payload=?,sha256=?',(zlib.compress(raw),hashlib.sha256(raw).hexdigest()))
        db.close()
        r.build_world_rails(self.world,self.output)
        cache = r.RailCache(self.output)
        self.addCleanup(cache.close)
        source = dict(format='gta-sa-region-v1',coordinate_system='gta-z-up',center=[20,0,0],radius=3,
            interior=0,instances=[dict(g.placement(1000,[20,0,0],[0,0,0,1],0,-1,'fixture'),
                instance_id=1,source='fixture.ipl',stream='fixture',dynamic=False,conditional=False,
                definition=dict(time_window=None))],models={'fixture':mesh},omitted={})
        _,scene = scene_export.build_scene(source,primitive_policy=scene_export.REJECT_SPHERES,hour=12)
        bounded = cache.query([20,0,0],3)
        self.assertEqual(bounded['rails'],[])
        complete = cache.query([20,0,0],3,scene=scene)
        self.assertEqual(len(complete['rails']),2)  # Only the two long chains intersect this cube.
        self.assertEqual(complete['stats']['outside_query_segments_from_verified_col'],2)
        self.assertTrue(all(rail['length_metres'] == 16 for rail in complete['rails']))
        payload,report = rail_export.build_rails(complete,scene)
        self.assertEqual(len(rail_export.read_nodes(payload)),report['node_count'])
        for edit in ('missing_faces','missing_instance','different_model','incomplete_col'):
            changed = deepcopy(scene)
            if edit == 'missing_faces':
                changed['face_provenance_in_col_order'] = []
            elif edit == 'missing_instance':
                changed['instances'] = []
            elif edit == 'different_model':
                changed['instances'][0]['model'] = 'other'
            else:
                changed['selected_primitives_complete'] = False
            self.assertEqual(cache.query([20,0,0],3,scene=changed)['rails'],[],edit)
        changed = deepcopy(scene)
        missing = complete['rails'][0]['sources'][0]['supporting_faces'][0]
        changed['face_provenance_in_col_order'] = [row for row in changed['face_provenance_in_col_order']
                                                 if row[:3] != [1,'triangle',missing]]
        selected = cache.query([20,0,0],3,scene=changed)
        self.assertLess(len(selected['rails']),2)
        self.assertTrue(all(missing not in ref['supporting_faces'] for chain in selected['rails'] for ref in chain['sources']))
        with self.assertRaisesRegex(ValueError,'node budget'):
            cache.query([20,0,0],3,scene=scene,maximum_nodes=2)
        digest,stamp = r.cache_identity(self.output,world_sha256=r._digest(self.world))
        original = r.RailCache.query
        def constrained(cache,center,radius,*args,**kwargs):
            if kwargs.get('scene') is not None:
                kwargs['maximum_nodes'] = 2
            return original(cache,center,radius,*args,**kwargs)
        with patch.object(r.RailCache,'query',new=constrained):
            reduced = r.prepare_rails(self.output,r._digest(self.world),scene,cache_sha256=digest,stamp=stamp)
        self.assertTrue(reduced['selection']['node_budget_reduced_radius'])
        self.assertEqual(reduced['selection']['radius'],1.5)
        self.assertIsNone(reduced['source'])  # Reduced scene=None cannot extend an unproven chain.

    def test_box_extension_requires_all_twelve_original_col_faces(self):
        chain = dict(sources=[dict(instance_id=1,model='box',primitive='box',primitive_index=2,
                                   supporting_faces=['z+','x+'])])
        self.assertTrue(r._chain_in_col(chain,{1:'box'},r.Counter({(1,'box',2):12})))
        self.assertFalse(r._chain_in_col(chain,{1:'box'},r.Counter({(1,'box',2):11})))

    def test_open_fence_and_track_provenance_survives_cache_and_qb_export(self):
        fixture(self.world,[{}])
        mesh = geometry_model(vertices=[(0,0,0),(4,0,0),(4,0,2),(0,0,2),
                                        (0,4,1),(4,4,1),(4,4.1,1),(0,4.1,1)],
                              faces=[(0,1,2,66,0),(0,2,3,66,0),(4,5,6,178,0),(4,6,7,178,0)])
        mesh.update(name="fixture",model_id_hint=1000,bounds=[[0,0,0],[4,4.1,2]],flags=2)
        raw = g.encoded(mesh)
        with sqlite3.connect(self.world) as db:
            db.execute("UPDATE models SET payload=?,sha256=?",(zlib.compress(raw),hashlib.sha256(raw).hexdigest()))
        db.close()
        r.build_world_rails(self.world,self.output)
        cache = r.RailCache(self.output)
        self.addCleanup(cache.close)
        graph = cache.query([22,2,1],8)
        self.assertEqual(len(graph["rails"]),3)
        self.assertEqual({s["edge_evidence"] for chain in graph["rails"] for s in chain["sources"]},
                         {"open_panel_top","narrow_strip_border"})
        for chain in graph["rails"]:
            for source in chain["sources"]:
                for normal in source["supporting_normals_gta"]:
                    self.assertEqual(normal[2],0 if source["edge_evidence"] == "open_panel_top" else 1)
        source = dict(format="gta-sa-region-v1",coordinate_system="gta-z-up",center=[22,2,1],radius=8,
            interior=0,instances=[dict(g.placement(1000,[20,0,0],[0,0,0,1],0,-1,"fixture"),
                instance_id=1,source="fixture.ipl",stream="fixture",dynamic=False,conditional=False,
                definition=dict(time_window=None))],models={"fixture":mesh},omitted={})
        _,scene = scene_export.build_scene(source,primitive_policy=scene_export.REJECT_SPHERES,hour=12)
        graph = cache.query([22,2,1],8,hour=12,scene=scene)
        payload,report = rail_export.build_rails(graph,scene)
        self.assertEqual(report["node_count"],6)
        self.assertEqual(len(rail_export.read_nodes(payload)),6)
        cache.close()
        with sqlite3.connect(self.output) as db:
            db.execute("UPDATE metadata SET value='2' WHERE key='algorithm_version'")
        db.close()
        with self.assertRaisesRegex(ValueError,"unsupported"):
            r.RailCache(self.output)

    def test_changed_cache_is_rejected_before_query(self):
        _,cache=self.prepare([{}])
        before=self.output.stat()
        import os
        os.utime(self.output,ns=(before.st_atime_ns,before.st_mtime_ns+1000000000))
        with self.assertRaisesRegex(ValueError,"changed after opening"):
            cache.query([20,0,0],8)

    def test_changed_material_policy_invalidates_precomputed_world_rails(self):
        _, cache = self.prepare([{}])
        cache.close()
        with patch.object(r.terrain_mapping, 'fingerprint', return_value='0'*64):
            with self.assertRaisesRegex(ValueError, 'unsupported'):
                r.cache_identity(self.output, world_sha256=r._digest(self.world))


if __name__ == "__main__":
    unittest.main()
