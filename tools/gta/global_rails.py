"""Prepare grind edges once for the whole static GTA world; query their index.

Geometry classification runs only in ``build_world_rails``. Gameplay reads
predefined chains and filters their original area, time and conditional IPL.
The stock rail manager remains bounded, so callers load a nearby selection.
"""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import zlib

import geometry as g
import rails
import terrain_mapping

SCHEMA = "gta-sa-global-grind-rails-v1"
MAX_LOCAL_MODELS = 64


def file_stamp(path):
    status = Path(path).stat()
    return status.st_size, status.st_mtime_ns, status.st_ctime_ns


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _metadata(db):
    return {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM metadata")}


def _region(model, quaternion=(0, 0, 0, 1), packed=0):
    instance = dict(instance_id=1, model_id=model["model_id_hint"], name=model["name"],
                    source="cached-model", position=[0., 0., 0.], quaternion=list(quaternion),
                    instance_flags=packed, interior=0, dynamic=False, conditional=False)
    low, high = g.world_bounds(model["bounds"], instance)
    center = [(a+b)/2 for a, b in zip(low, high)]
    radius = max(.01, max((b-a)/2 for a, b in zip(low, high))+.01)
    g.require(radius <= 512, "Model exceeds the bounded rail-classification extent")
    return dict(format="gta-sa-region-v1", coordinate_system="gta-z-up", center=center,
                radius=radius, interior=0, instances=[instance], models={model["name"]: model}, omitted={})


def _normal(source, model, matrix):
    result = []
    if source["primitive"] == "box":
        for face in source["supporting_faces"]:
            axis = "xyz".index(face[0])
            normal = tuple((-1 if face[1] == "-" else 1) if i == axis else 0 for i in range(3))
            result.append(g.transform(normal, matrix, (0, 0, 0)))
    else:
            # The source winding may be inward. The generator's selected pair
            # uniquely identifies the outward top/side orientation below.
        for face in source["supporting_faces"]:
            a, b, c = (model["vertices"][i] for i in model["faces"][face][:3])
            normal = rails.unit(rails.cross(rails.sub(b, a), rails.sub(c, a)))
            g.require(normal is not None, "A selected supporting face is degenerate")
            result.append(g.transform(normal, matrix, (0, 0, 0)))
        if source.get("edge_evidence") == "open_panel_top":
            g.require(len(result) == 1 and abs(result[0][2]) <= .25,
                      "Upper panel border has no near-vertical supporting face")
            return result
        if source.get("edge_evidence") == "narrow_strip_border":
            g.require(len(result) == 2 and all(abs(n[2]) >= .75 for n in result),
                      "Narrow strip border has no supporting top faces")
            return [n if n[2] > 0 else [-v for v in n] for n in result]
        if max(n[2] for n in result) < .5 or min(n[2] for n in result) < -.1:
            result = [[-value for value in normal] for normal in result]
        g.require(max(n[2] for n in result) >= .5 and min(n[2] for n in result) >= -.1,
                  "Supporting face normals do not identify the selected top/side edge")
    return result


def _split_ring(chain):
    points = chain["points_gta"]
    if points[0] != points[-1]:
        yield chain
        return
    # Retail export currently validates acyclic forward/backward chains. Keep
    # every edge of a closed ring as its own chain rather than losing the ring.
    for a, b in zip(points, points[1:]):
        value = dict(chain, points_gta=[a, b], length_metres=math.dist(a, b))
        yield value


def _chain_in_col(chain, instances, primitives):
    """Every supporting original face must exist in this exact compiled COL."""
    if not chain["sources"]:
        return False
    for source in chain["sources"]:
        ident = source["instance_id"]
        if instances.get(ident) != source["model"] or not source["supporting_faces"]:
            return False
        if source["primitive"] == "mesh":
            if any(primitives[(ident,"triangle",face)] != 1 for face in source["supporting_faces"]):
                return False
        elif source["primitive"] == "box":
            if (primitives[(ident,"box",source["primitive_index"])] != 12 or
                    any(face not in rails.BOX_FACE_NAMES for face in source["supporting_faces"])):
                return False
        else:
            return False
    return True


def build_world_rails(database, output, *, progress=None):
    database, output = Path(database).resolve(), Path(output).absolute()
    g.require(database.is_file() and not output.exists(), "Expected world.db and a new rail-cache destination")
    g.require(output.resolve() != database, "Rail cache cannot replace the source world database")
    before = database.stat()
    world_sha = _digest(database)
    source = sqlite3.connect(database.as_uri()+"?mode=ro", uri=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb"):
        pass
    target = sqlite3.connect(output)
    stats, areas = Counter(), Counter()
    models = OrderedDict()
    try:
        metadata = _metadata(source)
        g.require(metadata.get("schema") == g.SCHEMA and metadata.get("complete") is True,
                  "Incomplete or unsupported source world database")
        target.executescript("""
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE chains(id INTEGER PRIMARY KEY, area INTEGER NOT NULL,
                stream TEXT NOT NULL, conditional INTEGER NOT NULL,
                start_hour INTEGER, end_hour INTEGER, instance_id INTEGER NOT NULL,
                length REAL NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX chain_area ON chains(area);
            CREATE VIRTUAL TABLE rail_bounds USING rtree(id,minx,maxx,miny,maxy,minz,maxz);
        """)
        target.executemany("INSERT INTO metadata VALUES (?,?)", (
            ("schema", json.dumps(SCHEMA)), ("complete", "false"),
            ("algorithm_version", json.dumps(rails.ALGORITHM_VERSION)),
            ("material_mapping_sha256", json.dumps(terrain_mapping.fingerprint())),
            ("source_world_sha256", json.dumps(world_sha))))
        target.commit()
        rows = source.execute("SELECT id,name,payload FROM instances WHERE has_collision=1 AND dynamic=0 ORDER BY name,id")
        for instance_id, name, payload in rows:
            instance = json.loads(payload)
            stats["static_collidable_instances"] += 1
            if name not in models:
                row = source.execute("SELECT payload,sha256,source FROM models WHERE name=?", (name,)).fetchone()
                raw = zlib.decompress(row[0])
                g.require(len(raw) <= g.MAX_MEMBER and hashlib.sha256(raw).hexdigest() == row[1],
                          "Stored collision-model content identity differs")
                model = json.loads(raw)
                try:
                    graph = rails.generate(_region(model), maximum_nodes=100000)
                    g.require(not graph["budget"]["applied"], "One collision model exceeds the complete rail node budget")
                except ValueError as error:
                    if str(error) != "Model exceeds the bounded rail-classification extent":
                        raise
                    graph = None
                    stats["oversize_models_excluded"] += 1
                models[name] = model, graph, row[2]
                stats["collision_models_classified"] += 1
                if graph is not None:
                    stats["models_with_edges"] += bool(graph["rails"])
                    for key, value in graph["stats"].items():
                        stats["model_"+key] += value
                if len(models) > MAX_LOCAL_MODELS:
                    models.popitem(last=False)
                if progress is not None and stats["collision_models_classified"] % 100 == 0:
                    progress(dict(stats))
            model, graph, col_source = models[name]
            if graph is None:
                stats["oversize_instances_excluded"] += 1
                continue
            matrix = g.rotation_matrix(instance["quaternion"], instance["instance_flags"])
            z_only = matrix[0][2] == matrix[1][2] == matrix[2][0] == matrix[2][1] == 0 and matrix[2][2] == 1
            if not z_only:
                try:
                    graph = rails.generate(_region(model, instance["quaternion"], instance["instance_flags"]), maximum_nodes=100000)
                except ValueError as error:
                    if str(error) != "Model exceeds the bounded rail-classification extent":
                        raise
                    stats["oversize_tilted_instances_excluded"] += 1
                    continue
                g.require(not graph["budget"]["applied"], "A tilted model exceeds the complete rail node budget")
                stats["tilted_instances_classified"] += 1
            for chain_index, original in enumerate(graph["rails"]):
                chain = deepcopy(original)
                chain["points_gta"] = [g.transform(point, matrix, instance["position"]) if z_only else
                                        [point[i]+instance["position"][i] for i in range(3)]
                                        for point in chain["points_gta"]]
                for ref in chain["sources"]:
                    ref.update(instance_id=instance_id, model_id=instance["model_id"],
                               source=instance["source"], collision_source=col_source,
                               area=instance["interior"], stream=instance["stream"],
                               conditional=instance["conditional"],
                               time_window=instance["definition"]["time_window"],
                               supporting_normals_gta=_normal(ref, model, matrix))
                for part_index, part in enumerate(_split_ring(chain)):
                    part["name"] = f"gta_rail_{instance_id}_{chain_index}_{part_index}"
                    low, high = rails.bounds(part["points_gta"])
                    window = instance["definition"]["time_window"] or (None, None)
                    cursor = target.execute("INSERT INTO chains(area,stream,conditional,start_hour,end_hour,instance_id,length,payload) VALUES (?,?,?,?,?,?,?,?)",
                        (instance["interior"], instance["stream"], int(instance["conditional"]), *window,
                         instance_id, part["length_metres"], g.encoded(part).decode()))
                    target.execute("INSERT INTO rail_bounds VALUES (?,?,?,?,?,?,?)", (cursor.lastrowid,
                        low[0], high[0], low[1], high[1], low[2], high[2]))
                    stats["rail_chains"] += 1
                    stats["rail_nodes"] += len(part["points_gta"])
                    stats["rail_segments"] += len(part["points_gta"])-1
                    areas[instance["interior"]] += 1
            if stats["static_collidable_instances"] % 1000 == 0:
                target.commit()
        after = database.stat()
        g.require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and
                  world_sha == _digest(database), "Source world database changed during rail preparation")
        report = dict(format=SCHEMA, source_world_sha256=world_sha, stats=dict(stats),
            chains_by_area=dict(sorted(areas.items())),
            classification="Precomputed box roofs, locally convex creases, upper open panel borders and narrow strip borders; original vertices/materials retained.",
            limitations=["Wide flat borders, lower panel borders, nonmanifold/concave/steep edges and dynamic placements do not generate rails.",
                         "Occlusion is checked inside each model; overlapping different world instances can still overlap rails.",
                         "Runtime selects current area/hour/conditional IPL and a bounded nearby graph; it does not classify edges.",
                         "Named materials use the authored GTA-to-THAW terrain policy; ambiguous or unknown materials retain the default."])
        target.execute("INSERT INTO metadata VALUES (?,?)", ("report", g.encoded(report).decode()))
        target.execute("UPDATE metadata SET value='true' WHERE key='complete'")
        target.commit()
        g.require(target.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "Rail-cache integrity check failed")
        return report
    finally:
        source.close()
        target.close()


class RailCache:
    def __init__(self, path, *, world_sha256=None):
        self.path = Path(path).resolve()
        self.stamp = file_stamp(self.path)
        self.db = sqlite3.connect(self.path.as_uri()+"?mode=ro", uri=True)
        try:
            self.metadata = _metadata(self.db)
            g.require(self.metadata.get("schema") == SCHEMA and self.metadata.get("complete") is True and
                      self.metadata.get("algorithm_version") == rails.ALGORITHM_VERSION and
                      self.metadata.get("material_mapping_sha256") == terrain_mapping.fingerprint(),
                      "Incomplete or unsupported global rail cache")
            if world_sha256 is not None:
                g.require(self.metadata["source_world_sha256"] == world_sha256, "Rail cache belongs to another GTA world database")
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def query(self, center, radius, interior=0, *, hour=12, active_streams=(), maximum_nodes=8192, scene=None):
        g.require(file_stamp(self.path) == self.stamp, "Global rail cache changed after opening")
        g.require(len(center) == 3 and math.isfinite(radius) and 0 < radius <= 512,
                  "Invalid global rail query bounds")
        g.finite(center)
        g.require(type(interior) is int and 0 <= interior <= 255 and math.isfinite(hour) and 0 <= hour < 24,
                  "Invalid global rail area/hour")
        g.require(type(maximum_nodes) is int and 2 <= maximum_nodes <= 8192, "Invalid stock rail node budget")
        active = set(active_streams)
        g.require(all(isinstance(name, str) and name and len(name) <= 63 and
                      all(c.isascii() and (c.isalnum() or c == "_") for c in name) for name in active),
                  "Invalid active conditional IPL names")
        col_instances, col_primitives = {}, Counter()
        if scene is not None:
            expected = scene["selection"]
            g.require(scene.get("format") == "gta-thaw-static-scene-v1" and
                      expected["center_gta"] == list(center) and expected["radius_gta"] == radius and
                      expected["interior"] == interior and expected["hour"] == hour and
                      expected["active_streams"] == sorted(active), "Rail selection and collision scene differ")
            if scene.get("selected_primitives_complete") is True:
                col_instances = {row["instance_id"]: row["model"] for row in scene["instances"]}
                col_primitives.update(tuple(row[:3]) for row in scene["face_provenance_in_col_order"])
        low, high = [[value+sign*radius for value in center] for sign in (-1, 1)]
        rows = self.db.execute("""SELECT c.id,c.payload,c.conditional,c.stream,c.start_hour,c.end_hour
            FROM rail_bounds b CROSS JOIN chains c ON c.id=b.id WHERE c.area IN (?,13)
            AND b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=? AND b.minz<=? AND b.maxz>=?
            ORDER BY c.id LIMIT 100001""", (interior, high[0],low[0],high[1],low[1],high[2],low[2])).fetchall()
        g.require(len(rows) <= 100000, "Global rail selection exceeds bounded query rows")
        segments, omitted, extensions = [], Counter(), Counter()
        for ident, payload, conditional, stream, start, end in rows:
            if conditional and stream not in active:
                omitted["inactive_conditional"] += 1
                continue
            if not g.active_hour(None if start is None else (start, end), hour):
                omitted["inactive_time_model"] += 1
                continue
            chain = json.loads(payload)
            complete_in_col = _chain_in_col(chain,col_instances,col_primitives)
            for a, b in zip(chain["points_gta"], chain["points_gta"][1:]):
                if any(not low[i] <= point[i] <= high[i] for point in (a, b) for i in range(3)):
                    if not complete_in_col:
                        omitted["crossing_region_segments"] += 1
                        continue
                    # The source query includes whole placed COL models. Their
                    # proven rail chains need no artificial end at its cube.
                    extensions["outside_query_segments_from_verified_col"] += 1
                for source in chain["sources"]:
                    segments.append(dict(points=[a,b], material=chain["gta_surface_material"], source=source))
        stats = Counter(extensions)
        selected, budget = rails.chain_segments(segments, rails.DEFAULT_WELD_METRES,
            rails.DEFAULT_MINIMUM_METRES, 20., maximum_nodes, stats)
        g.require(not budget["applied"], "Precomputed rails exceed stock node budget; select a smaller complete region")
        # Breaking smooth closed rings may add one duplicate endpoint. Check
        # the final stock extent after preserving every segment.
        selected = [part for chain in selected for part in _split_ring(chain)]
        for chain in selected:
            ident = hashlib.sha256(g.encoded(dict(points=chain["points_gta"], sources=chain["sources"]))).hexdigest()[:24]
            chain["name"] = "gta_cached_"+ident
        count = sum(len(chain["points_gta"]) for chain in selected)
        g.require(count <= maximum_nodes, "Split rail rings exceed stock node budget")
        stats["rail_nodes"] = count
        stats["rail_chains"] = len(selected)
        stats["rail_segments"] = sum(len(chain["points_gta"])-1 for chain in selected)
        selection = dict(center=list(center), radius=radius, interior=interior,
                         hour=hour, active_streams=sorted(active), world_sha256=self.metadata["source_world_sha256"])
        source_region = dict(sha256=hashlib.sha256(g.encoded(selection)).hexdigest(), center=list(center),
                             radius=radius, interior=interior, scene_stamp=None, omitted=dict(omitted))
        if scene is not None:
            source_region.update(sha256=scene["source_region_sha256"], scene_stamp=scene["scene_stamp"])
        g.require(file_stamp(self.path) == self.stamp, "Global rail cache changed during selection")
        return dict(format="gta-grind-candidates-v1", algorithm_version=rails.ALGORITHM_VERSION,
            coordinate_system="gta-z-up", unit="metre", source_region=source_region,
            parameters=dict(weld_metres=rails.DEFAULT_WELD_METRES, minimum_length_metres=rails.DEFAULT_MINIMUM_METRES,
                            maximum_slope_degrees=35., maximum_turn_degrees=20.),
            stats=dict(stats), budget=budget, rails=selected,
            cache_selection=selection, edge_classification_at_runtime=False,
            physics_ready=False, runtime_tested=False, thaw_qb_emitted=False,
            limitations=self.metadata["report"]["limitations"])


def cache_identity(path, *, world_sha256, digest=None):
    """Validate/hash once before gameplay; the compiler checks this file stamp."""
    path = Path(path).resolve()
    before = file_stamp(path)
    checksum = (digest or _digest)(path)
    cache = RailCache(path, world_sha256=world_sha256)
    cache.close()
    g.require(before == file_stamp(path), "Global rail cache changed during initialization")
    return checksum, before


def prepare_rails(path, world_sha256, scene, *, cache_sha256, stamp):
    """Background-process read/selection/QB authoring; no original native calls."""
    import rail_export
    g.require(isinstance(cache_sha256, str) and len(cache_sha256) == 64 and
              all(c in "0123456789abcdef" for c in cache_sha256), "Invalid validated rail-cache identity")
    g.require(file_stamp(path) == tuple(stamp), "Global rail cache changed before preparation")
    cache = RailCache(path, world_sha256=world_sha256)
    try:
        selected = scene["selection"]
        radius = selected["radius_gta"]
        while True:
            try:
                graph = cache.query(selected["center_gta"], radius, selected["interior"],
                    hour=selected["hour"], active_streams=selected["active_streams"],
                    scene=scene if radius == selected["radius_gta"] else None)
                break
            except ValueError as error:
                if str(error) not in (
                        "Precomputed rails exceed stock node budget; select a smaller complete region",
                        "Split rail rings exceed stock node budget") or radius <= .5:
                    raise
                # Keep all chains in a smaller nearby volume instead of making
                # a dense grind selection abort the player's collision stream.
                radius = max(.5, radius*.5)
        if radius != selected["radius_gta"]:
            graph["source_region"].update(sha256=scene["source_region_sha256"], scene_stamp=scene["scene_stamp"])
        graph["cache_selection"]["requested_radius"] = selected["radius_gta"]
        graph["cache_selection"]["node_budget_reduced_radius"] = radius != selected["radius_gta"]
    finally:
        cache.close()
    graph["cache_selection"]["rail_cache_sha256"] = cache_sha256
    if graph["rails"]:
        identity = dict(graph=graph, coordinates=scene["coordinates"], col_sha256=scene["col_sha256"])
        basename = "gta_r_"+hashlib.sha256(g.encoded(identity)).hexdigest()[:16]
        source, report = rail_export.build_rails(graph, scene, basename=basename, sort_native=True)
    else:
        source, report = None, None
    g.require(file_stamp(path) == tuple(stamp), "Global rail cache changed while preparing authored rails")
    return dict(source=source, report=report, selection=graph["cache_selection"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_world_rails(args.database, args.output,
        progress=lambda row: print(json.dumps(dict(progress=row)), flush=True))
    print(json.dumps(dict(output=str(args.output), report=report)), flush=True)


if __name__ == "__main__":
    main()
