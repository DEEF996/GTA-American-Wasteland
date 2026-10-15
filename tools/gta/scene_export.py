"""Compile a bounded static GTA region into original THAW COL v10 geometry.

Triangles, solid boxes and planar boxes have exact polygonal representations.
The caller explicitly chooses exact geometry, an incomplete research subset, or
spheres tessellated with a certified 2 cm error bound including float storage.
No live process or collision solver is changed. GTA materials are preserved.
"""
from collections import Counter
from functools import lru_cache
import hashlib
import itertools
import math
from pathlib import Path
import struct
import sys

import geometry as g
import terrain_mapping
import surface_flags
from rails import BOX_QUADS, cross, dot, sub, unit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assets_export import parse_col, qbkey
from carentan_col import compile_col


REJECT_SPHERES = "reject_spheres"
RESEARCH_SUBSET = "research_mesh_boxes"
TESSELLATE_SPHERES = "bounded_spheres"
SPHERE_ERROR_METRES = .02


def _box_quads(box):
    g.require(len(box) == 10, "Invalid GTA collision box")
    g.checked_box(box[:3], box[3:6])
    flat = [axis for axis in range(3) if box[axis] == box[axis+3]]
    if not flat:
        return BOX_QUADS
    if len(flat) == 1:
        # GTA tests both min/max planes even when they coincide. Keep those
        # opposing faces at the original coordinates, with no invented depth.
        return BOX_QUADS[flat[0]*2:flat[0]*2+2]
    return ()  # A line/point has no polygonal surface; never inflate it.


def _vector(value):
    g.require(isinstance(value, (list, tuple)) and len(value) == 3, "Expected a three-dimensional vector")
    g.finite(value)


@lru_cache(maxsize=7)
def _icosphere(level):
    """Deterministic closed unit mesh and its largest inward plane distance."""
    g.require(type(level) is int and 0 <= level <= 6, "Invalid sphere subdivision")
    if level == 0:
        phi = (1 + math.sqrt(5)) / 2
        vertices = [unit(p) for p in
                    [(a, b, 0) for a in (-1, 1) for b in (-phi, phi)] +
                    [(0, a, b) for a in (-1, 1) for b in (-phi, phi)] +
                    [(b, 0, a) for a in (-1, 1) for b in (-phi, phi)]]
        faces = []
        for a, b, c in itertools.combinations(range(12), 3):
            normal = cross(sub(vertices[b], vertices[a]), sub(vertices[c], vertices[a]))
            side = [dot(normal, sub(p, vertices[a])) for p in vertices]
            if max(side) <= 1e-10 or min(side) >= -1e-10:
                faces.append((a, b, c) if dot(normal, vertices[a]) > 0 else (a, c, b))
    else:
        old_vertices, old_faces, _ = _icosphere(level - 1)
        vertices, faces, edges = list(old_vertices), [], {}

        def midpoint(a, b):
            key = tuple(sorted((a, b)))
            if key not in edges:
                edges[key] = len(vertices)
                vertices.append(unit(tuple(x+y for x, y in zip(vertices[a], vertices[b]))))
            return edges[key]

        for a, b, c in old_faces:
            x, y, z = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            faces.extend(((a, x, z), (x, b, y), (z, y, c), (x, y, z)))
    error = max(1-dot(unit(cross(sub(vertices[b], vertices[a]), sub(vertices[c], vertices[a]))),
                      vertices[a]) for a, b, c in faces)
    return tuple(vertices), tuple(faces), error


def _sphere_level(center_thaw, radius):
    # Every float32-rounded vertex is within this conservative half-ULP bound.
    # Add a double-precision margin before selecting the geometric subdivision.
    maximum = [abs(v) + radius*g.THAW_UNITS_PER_GTA_UNIT for v in center_thaw]
    g.require(all(math.isfinite(v) and v < 2**127 for v in maximum), "Sphere exceeds float32 range")
    rounding = math.sqrt(sum(2**(2*(max(-126, math.floor(math.log2(v))) - 24))
                             if v else 2**-300 for v in maximum)) / g.THAW_UNITS_PER_GTA_UNIT
    rounding += 1e-9
    g.require(rounding < SPHERE_ERROR_METRES, "Scene origin cannot preserve the sphere error bound")
    for level in range(7):
        if radius * _icosphere(level)[2] + rounding <= SPHERE_ERROR_METRES:
            return level
    raise ValueError("Sphere error bound exceeds subdivision budget; request a smaller region")


def build_scene(region, *, primitive_policy, hour, active_streams=(), origin=None,
                max_triangles=200000, max_col_bytes=16 * 1024 * 1024):
    """Return (COL bytes, private provenance); all budgets fail without truncation.

    Selection must already pass the caller's live scene/clock/IPL guards. This
    function rechecks area/time/conditional eligibility but cannot certify live
    entity existence. Origin stays fixed for a retained native scene.
    """
    g.require(primitive_policy in (REJECT_SPHERES, RESEARCH_SUBSET, TESSELLATE_SPHERES),
              "Explicit primitive policy is required")
    g.require(region.get("format") == "gta-sa-region-v1" and region.get("coordinate_system") == "gta-z-up",
              "Expected the original GTA static region format")
    g.require(type(max_triangles) is int and 1 <= max_triangles <= 200000 and
              type(max_col_bytes) is int and 236 <= max_col_bytes <= 16 * 1024 * 1024,
              "Invalid native scene output budget")
    _vector(region["center"])
    g.require(math.isfinite(region["radius"]) and 0 < region["radius"] <= 512 and
              math.isfinite(hour) and 0 <= hour < 24, "Invalid scene region/time")
    area = region["interior"]
    g.require(type(area) is int and 0 <= area <= 255, "Invalid scene area")
    origin = list(region["center"] if origin is None else origin)
    _vector(origin)
    active = set(active_streams)
    g.require(all(isinstance(name, str) and name and len(name) <= 63 for name in active), "Invalid active IPL name")
    models, instances = region["models"], region["instances"]
    g.require(len(models) <= 10000 and len(instances) <= 10000, "Scene exceeds model/instance budget")
    g.require(all(instance.get("name") in models for instance in instances), "Scene has a missing collision model")
    identities = [instance.get("instance_id") for instance in instances]
    g.require(all(type(value) is int and value > 0 for value in identities) and
              len(set(identities)) == len(identities), "Invalid or duplicate scene instance")
    g.require(sum(len(model.get("vertices", ())) for model in models.values()) <= 750000,
              "Source exceeds scene vertex budget")
    required = ("vertices", "faces", "boxes", "spheres")
    for model in models.values():
        g.require(all(key in model and isinstance(model[key], list) for key in required),
                  "Collision model lacks explicit primitive arrays")
    counts = Counter({kind: sum(len(models[i["name"]][kind]) for i in instances)
                      for kind in ("faces", "boxes", "spheres")})
    g.require(sum(counts.values()) <= 250000, "Instanced source exceeds scene primitive budget")
    if primitive_policy == REJECT_SPHERES:
        g.require(counts["spheres"] == 0,
                  f"THAW COL has no exact sphere representation: {counts['spheres']} spheres; select another explicit policy")
    planned_triangles = counts["faces"] + sum(2*len(_box_quads(box))
        for instance in instances for box in models[instance["name"]]["boxes"])
    g.require(planned_triangles <= max_triangles,
              "Native scene exceeds triangle budget; request a smaller region")

    points, faces, face_flags, evidence, used_instances, zero_area_faces = [], [], [], [], [], []
    zero_area_counts = Counter()
    skipped_boxes, planar_boxes, maximum_rounding_error_metres = 0, 0, 0.0
    material_counts = Counter()
    sphere_levels = Counter()
    maximum_sphere_error_metres, sphere_triangles = 0.0, 0
    for instance in instances:
        # GTA CEntity::IsInArea accepts area 13 in every current area.
        g.require(instance["interior"] in (area, 13) and
                  instance["instance_flags"] & 255 == instance["interior"],
                  "Instance belongs to a different area")
        g.require(instance.get("dynamic") is False, "Static scene cannot use an unobserved dynamic transform")
        g.require(not instance["conditional"] or instance["stream"] in active, "Inactive conditional IPL in scene")
        g.require(g.active_hour(instance["definition"]["time_window"], hour), "Inactive time model in scene")
        g.placement(instance["model_id"], instance["position"], instance["quaternion"],
                    instance["instance_flags"], instance["lod"], instance["name"])
        model = models[instance["name"]]
        g.require(type(model.get("flags")) is int and model["flags"] & 2, "Disabled collision model in scene")
        matrix = g.rotation_matrix(instance["quaternion"], instance["instance_flags"])
        first = len(faces)

        def world_point(world):
            nonlocal maximum_rounding_error_metres
            _vector(world)
            result = g.gta_to_thaw(world, origin=origin)
            # The writer stores float32; no transform or extra scale is applied there.
            rounded = tuple(struct.unpack("<3f", struct.pack("<3f", *result)))
            rounding_error = math.dist(result, rounded) / g.THAW_UNITS_PER_GTA_UNIT
            maximum_rounding_error_metres = max(maximum_rounding_error_metres, rounding_error)
            result = rounded
            g.finite(result)
            points.append(result)
            return len(points) - 1, rounding_error

        def point(value):
            _vector(value)
            return world_point(g.transform(value, matrix, instance["position"]))[0]

        def face(indices, kind, index, surface, *, source_zero_area=False):
            g.require(all(type(v) is int and 0 <= v <= 255 for v in surface), "Invalid GTA surface bytes")
            a, b, c = (points[i] for i in indices)
            # Exact equality only: retain every nonzero float32 triangle, however small.
            normal = cross(sub(b, a), sub(c, a))
            if source_zero_area or normal == (0., 0., 0.):
                reason = "source_zero_area" if source_zero_area else "float32_zero_area"
                zero_area_counts[reason] += 1
                zero_area_faces.append(dict(instance_id=instance["instance_id"], primitive_kind=kind,
                    primitive_index=index, raw_gta_surface_bytes=list(surface), reason=reason))
                return
            faces.append(tuple(indices))
            evidence.append([instance["instance_id"], kind, index, list(surface)])
            material_counts[surface[0]] += 1

        vertex_map = {}
        for index, row in enumerate(model["faces"]):
            g.require(len(row) == 5 and all(type(i) is int and 0 <= i < len(model["vertices"]) for i in row[:3]),
                      "Invalid GTA collision triangle")
            for vertex in row[:3]:
                if vertex not in vertex_map:
                    vertex_map[vertex] = point(model["vertices"][vertex])
            # GTA's native plane is (C-A)x(B-A), opposite THAW's native floor.
            a, b, c = (model["vertices"][i] for i in row[:3])
            face([vertex_map[row[i]] for i in (0, 2, 1)], "triangle", index, row[3:],
                 source_zero_area=cross(sub(b, a), sub(c, a)) == (0., 0., 0.))
        for index, row in enumerate(model["boxes"]):
            quads = _box_quads(row)
            if not quads:
                g.require(primitive_policy == RESEARCH_SUBSET, "Line/point box has no polygonal surface representation")
                skipped_boxes += 1
                continue
            planar_boxes += len(quads) == 2
            vertices = [point(p) for p in itertools.product(*zip(row[:3], row[3:6]))]
            for a, b, c, d in quads:
                face((vertices[a], vertices[b], vertices[c]), "box", index, row[6:])
                face((vertices[a], vertices[c], vertices[d]), "box", index, row[6:])
        for index, row in enumerate(model["spheres"]):
            g.require(len(row) == 8, "Invalid GTA collision sphere")
            g.finite(row[:4])
            g.require(row[3] >= 0 and all(type(v) is int and 0 <= v <= 255 for v in row[4:]),
                      "Invalid sphere radius/surface")
            if primitive_policy != TESSELLATE_SPHERES:
                continue
            radius = row[3]
            g.require(radius > 0, "Zero-radius sphere has no closed polygonal representation")
            # Original GTA ProcessColModels transforms the center, not the radius.
            center = g.transform(row[:3], matrix, instance["position"])
            center_thaw = g.gta_to_thaw(center, origin=origin)
            level = _sphere_level(center_thaw, radius)
            unit_vertices, unit_faces, unit_error = _icosphere(level)
            planned_triangles += len(unit_faces)
            g.require(planned_triangles <= max_triangles,
                      "Native scene exceeds triangle budget after sphere subdivision; request a smaller region")
            vertices, rounding_error = [], 0.0
            for vertex in unit_vertices:
                world = tuple(c + radius*v for c, v in zip(center, vertex))
                vertex_index, vertex_error = world_point(world)
                vertices.append(vertex_index)
                rounding_error = max(rounding_error, vertex_error)
            # Corresponding barycentric points differ by at most the maximum
            # vertex perturbation. Add it to the ideal mesh's Hausdorff bound.
            error = radius*unit_error + rounding_error + 1e-9
            g.require(error <= SPHERE_ERROR_METRES, "Rounded sphere exceeds the declared geometric error bound")
            for a, b, c in unit_faces:
                ids = (vertices[a], vertices[b], vertices[c])
                pa, pb, pc = (points[i] for i in ids)
                g.require(dot(cross(sub(pb, pa), sub(pc, pa)), sub(pa, center_thaw)) > 0,
                          "Rounded sphere has a degenerate or inward face")
                face(ids, "sphere", index, row[4:])
            sphere_levels[level] += 1
            sphere_triangles += len(unit_faces)
            maximum_sphere_error_metres = max(maximum_sphere_error_metres, error)
        used_instances.append({"instance_id": instance["instance_id"], "model": instance["name"],
                               "source": instance["source"], "stream": instance["stream"],
                               "input_triangle_start": first, "input_triangle_count": len(faces)-first,
                               "omitted_spheres": 0 if primitive_policy == TESSELLATE_SPHERES else len(model["spheres"])})
    g.require(faces, "Selected scene has no supported collision surfaces")
    face_flags = surface_flags.collision_flags(points, faces)
    source_hash = hashlib.sha256(g.encoded(region)).hexdigest()
    scene_hash = hashlib.sha256(g.encoded([source_hash, origin, primitive_policy,
        terrain_mapping.fingerprint(), surface_flags.fingerprint()])).hexdigest()
    compiled, face_order = compile_col({"positions": points, "indices": faces, "format": "thaw-col-v10",
        "face_flags": face_flags,
        "terrain_types": [terrain_mapping.ground(row[3][0]) for row in evidence]},
        coordinates="thaw", scale=1.0, return_face_order=True)
    payload = bytearray(compiled)
    g.require(len(payload) <= max_col_bytes, "Native scene exceeds COL byte budget")
    # Reuse the verified writer's spatial partition, preserving its face order
    # for raw GTA material provenance. Object names are unique to this scene.
    ordered = [evidence[index] for index in face_order]
    keys = set()
    for index in range(struct.unpack_from("<I", payload, 4)[0]):
        key = qbkey(f"gta_scene_{scene_hash}_{index}")
        g.require(key not in keys, "Native scene object checksum collision")
        keys.add(key)
        struct.pack_into("<I", payload, 80 + index * 64, key)
    parsed = parse_col(payload)
    g.require(parsed["stats"]["triangles"] == len(faces) == len(ordered), "Compiled scene lost collision faces")
    rows, columns = struct.unpack_from("<2I", payload, 40)
    bounds = parsed["bounds"]
    g.require(len(keys) <= 4096 and parsed["stats"]["vertices"] <= 500000 and
              1 <= rows <= 40 and 1 <= columns <= 40 and
              all(bounds["min"][axis] < bounds["max"][axis] for axis in (0, 2)),
              "Scene exceeds the existing bounded native COL profile; reduce region or inspect oversized model")
    report = {"format": "gta-thaw-static-scene-v1", "primitive_policy": primitive_policy,
              "source_region_sha256": source_hash, "scene_stamp": region.get("scene"),
              "selection": {"center_gta": region["center"], "radius_gta": region["radius"],
                            "interior": area, "hour": hour, "active_streams": sorted(active)},
              "coordinates": {"origin_gta": origin, "thaw_units_per_gta_unit": g.THAW_UNITS_PER_GTA_UNIT,
                              "axes": "(x,z,-y)", "position_storage": "float32", "mesh_winding": "swap B/C",
                              "maximum_rounding_error_metres": maximum_rounding_error_metres},
              "stats": {"instances": len(instances), "source_triangles": counts["faces"],
                        "source_boxes": counts["boxes"], "source_spheres": counts["spheres"],
                        "planar_boxes": planar_boxes,
                        "col_triangles": len(faces), "col_objects": len(keys), "col_bytes": len(payload),
                        "supersector_rows": rows, "supersector_columns": columns,
                        "degenerate_triangles": parsed["stats"]["degenerate_triangles_preserved"],
                        "zero_area_faces_removed": len(zero_area_faces),
                        "zero_area_source_faces_removed": zero_area_counts["source_zero_area"],
                        "zero_area_float32_faces_removed": zero_area_counts["float32_zero_area"]},
              "omitted": {"spheres": counts["spheres"] - sum(sphere_levels.values()), "zero_volume_boxes": skipped_boxes,
                          "zero_area_faces": len(zero_area_faces),
                          "query": region.get("omitted", {})},
              "selected_primitives_complete": counts["spheres"] == sum(sphere_levels.values()) and skipped_boxes == 0,
              "planar_box_representation": "Coincident original min/max faces; both windings, no added thickness.",
              "sphere_approximation": {"method": "icosphere", "levels": dict(sorted(sphere_levels.items())),
                                       "triangles": sphere_triangles, "maximum_allowed_error_metres": SPHERE_ERROR_METRES,
                                       "maximum_certified_error_metres": maximum_sphere_error_metres,
                                       "includes_float32_rounding": True},
              "geometry_exact": sphere_triangles == 0 and counts["spheres"] == skipped_boxes == 0 and
                                zero_area_counts["float32_zero_area"] == 0,
              "zero_area_faces": {"rule": "exact zero cross product in source or final float32 coordinates; no epsilon",
                                  "removed": zero_area_faces},
              "surface_policy": surface_flags.surface_policy(face_flags),
              "wallride_triangles": sum(bool(flag & terrain_mapping.WALL_RIDABLE) for flag in face_flags),
              "vert_triangles": sum(bool(flag & surface_flags.VERT) for flag in face_flags),
              "bank_triangles": sum(bool(flag & surface_flags.BANK) for flag in face_flags),
              "gta_material_triangle_counts": dict(sorted(material_counts.items())),
              "face_provenance_fields": ["instance_id", "primitive_kind", "primitive_index", "raw_gta_surface_bytes"],
              "face_provenance_in_col_order": ordered, "instances": used_instances,
              "col_sha256": hashlib.sha256(payload).hexdigest(), "live_collision_complete": False,
              "runtime_tested": False, "physics_ready": False}
    return bytes(payload), report
