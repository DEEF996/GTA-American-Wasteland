"""Conservative GTA metre-space grind candidates from local static COL geometry.

This authors geometry candidates only. It neither emits THAW QB nor assigns
THAW terrain enums. Source GTA material ids and primitive evidence are retained.
Boxes, locally convex creases, upper fence borders and narrow horizontal strips
supply original edges. No rail vertex is created away from source geometry.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import itertools
import json
import math
from pathlib import Path

from geometry import encoded, finite, query_world, require, rotation_matrix, transform


ALGORITHM_VERSION = 3
DEFAULT_WELD_METRES = .002
DEFAULT_MINIMUM_METRES = .6
BOX_QUADS = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
             (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
BOX_FACE_NAMES = ("x-", "x+", "y-", "y+", "z-", "z+")


def sub(a, b):
    return tuple(x-y for x, y in zip(a, b))


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def unit(v):
    size = math.sqrt(dot(v, v))
    return tuple(x/size for x in v) if size > 1e-12 else None


def bounds(points):
    return (tuple(min(p[i] for p in points) for i in range(3)),
            tuple(max(p[i] for p in points) for i in range(3)))


class Weld:
    def __init__(self, distance):
        self.distance, self.points, self.cells = distance, [], defaultdict(list)

    def add(self, point):
        cell = tuple(math.floor(v/self.distance) for v in point)
        for delta in itertools.product((-1, 0, 1), repeat=3):
            for index in self.cells.get(tuple(a+b for a, b in zip(cell, delta)), ()):
                if math.dist(point, self.points[index]) <= self.distance:
                    return index
        index = len(self.points)
        self.points.append(tuple(point))
        self.cells[cell].append(index)
        return index


def edge_map(faces):
    edges = defaultdict(list)
    for index, (ids, material, source_face) in enumerate(faces):
        for a, b in zip(ids, ids[1:]+ids[:1]):
            edges[tuple(sorted((a, b)))].append((index, a, b))
    return edges


def mesh_components(points, raw_faces, weld, stats):
    pool, remap = Weld(weld), []
    for point in points:
        finite(point)
        require(len(point) == 3, "Invalid model vertex")
        remap.append(pool.add(point))
    faces, seen = [], set()
    for index, row in enumerate(raw_faces):
        require(len(row) == 5 and all(type(v) is int for v in row) and
                all(0 <= v < len(points) for v in row[:3]) and
                all(0 <= v <= 255 for v in row[3:]), "Invalid source COL triangle")
        ids = tuple(remap[i] for i in row[:3])
        key = (min(ids, ids[1:]+ids[:1], ids[2:]+ids[:2]), row[3])
        if key in seen:
            stats["duplicate_mesh_faces"] += 1
            continue
        seen.add(key)
        # Keep degenerate faces in their component so they cannot make a hole
        # disappear by being silently dropped before the closure check.
        faces.append((ids, row[3], index))
    edges = edge_map(faces)
    adjacent = defaultdict(set)
    for neighbors in edges.values():
        ids = [item[0] for item in neighbors]
        for index in ids:
            adjacent[index].update(ids)
    unseen = set(range(len(faces)))
    while unseen:
        stack, selected = [min(unseen)], []
        unseen.remove(stack[0])
        while stack:
            index = stack.pop()
            selected.append(faces[index])
            for neighbor in adjacent[index]:
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        yield pool.points, selected


def classify_component(points, faces, weld):
    edges = edge_map(faces)
    if any(len(neighbors) != 2 for neighbors in edges.values()):
        return None, "open_or_nonmanifold"
    if any(a != d or b != c for neighbors in edges.values()
           for (_, a, b), (_, c, d) in [neighbors]):
        return None, "inconsistent_winding"
    used = sorted({v for ids, material, face in faces for v in ids})
    origin = points[used[0]]
    normals = []
    volume = 0.
    for ids, material, source_face in faces:
        a, b, c = (points[i] for i in ids)
        normal = unit(cross(sub(b, a), sub(c, a)))
        if normal is None:
            return None, "degenerate"
        normals.append(normal)
        volume += dot(sub(a, origin), cross(sub(b, origin), sub(c, origin))) / 6
    if abs(volume) <= weld**3:
        return None, "zero_volume"
    if volume < 0:
        faces = [((ids[0], ids[2], ids[1]), material, source_face) for ids, material, source_face in faces]
        normals = [tuple(-v for v in normal) for normal in normals]
        edges = edge_map(faces)
    planes = []
    for (ids, material, source_face), normal in zip(faces, normals):
        distance = dot(normal, points[ids[0]])
        if any(dot(normal, points[i]) > distance + weld for i in used):
            return None, "nonconvex"
        planes.append((normal, distance))
    return dict(faces=faces, normals=normals, planes=planes, edges=edges,
                bounds=bounds([points[i] for i in used]), flipped=volume < 0), None


def mesh_ridges(points, faces):
    """Keep original triangle-pair evidence even when the whole COL is open."""
    normals = [unit(cross(sub(points[ids[1]], points[ids[0]]),
                          sub(points[ids[2]], points[ids[0]]))) for ids, _, _ in faces]
    return dict(faces=faces, normals=normals, edges=edge_map(faces))


def open_mesh_edges(points, component, weld):
    """Upper panel borders and paired narrow strip borders, never face diagonals."""
    faces, normals, edges = (component[key] for key in ("faces", "normals", "edges"))
    selected = {}
    for edge, neighbors in edges.items():
        if len(neighbors) != 1:
            continue
        face = neighbors[0][0]
        ids, material, source = faces[face]
        normal = normals[face]
        if normal is None or any(len(edges[tuple(sorted(pair))]) > 2
                                 for pair in zip(ids, ids[1:]+ids[:1])):
            continue
        a, b = (points[i] for i in edge)
        direction = sub(b, a)
        horizontal2 = direction[0]**2+direction[1]**2
        if horizontal2 <= weld*weld:
            continue
        other = points[next(i for i in ids if i not in edge)]
        along = sum((other[i]-a[i])*direction[i] for i in (0, 1))/horizontal2
        below = a[2]+along*direction[2]-other[2]
        if abs(normal[2]) <= .25 and below >= .05:
            selected[edge] = (face, [source], "open_panel_top")
        if abs(normal[2]) < .75:
            continue
        # Each rail head is a thin quad: two long original borders joined by
        # two short spans. A narrow triangle beside a wide road is insufficient.
        for shared in (tuple(sorted(pair)) for pair in zip(ids, ids[1:]+ids[:1])):
            adjacent = edges[shared]
            if len(adjacent) != 2:
                continue
            (_, sa, sb), (_, sc, sd) = adjacent
            if sa != sd or sb != sc:
                continue
            partner = next(item[0] for item in adjacent if item[0] != face)
            other_ids, other_material, other_source = faces[partner]
            other_normal = normals[partner]
            if other_material != material or other_normal is None or abs(other_normal[2]) < .75:
                continue
            if dot(normal, other_normal) < .95 or len(set(ids+other_ids)) != 4:
                continue
            opposite = tuple(sorted(set(ids+other_ids)-set(edge)))
            if len(edges.get(opposite, ())) != 1:
                continue
            c, d = (points[i] for i in opposite)
            width = min(max(math.dist(a,c),math.dist(b,d)), max(math.dist(a,d),math.dist(b,c)))
            if weld < width <= .25 and min(math.dist(a,b),math.dist(c,d)) >= 4*width:
                selected[edge] = (face, [source, other_source], "narrow_strip_border")
                break
    return selected


def overlaps(a, b):
    return all(a[0][i] <= b[1][i] and b[0][i] <= a[1][i] for i in range(3))


def spatial_tree(items):
    if not items:
        return None
    region = (tuple(min(item["bounds"][0][a] for item in items) for a in range(3)),
              tuple(max(item["bounds"][1][a] for item in items) for a in range(3)))
    if len(items) <= 8:
        return (region, items, None)
    axis = max(range(3), key=lambda i: region[1][i]-region[0][i])
    items = sorted(items, key=lambda item: item["bounds"][0][axis]+item["bounds"][1][axis])
    mid = len(items)//2
    return (region, spatial_tree(items[:mid]), spatial_tree(items[mid:]))


def spatial_query(tree, region):
    if tree is None or not overlaps(tree[0], region):
        return
    if tree[2] is None:
        for item in tree[1]:
            if overlaps(item["bounds"], region):
                yield item
    else:
        yield from spatial_query(tree[1], region)
        yield from spatial_query(tree[2], region)


def segment_in_planes(a, b, planes, inward):
    """Whether a nonzero open segment interval is inside every halfspace."""
    lo, hi = 0., 1.
    direction = sub(b, a)
    for normal, distance in planes:
        value = dot(normal, a)-distance+inward
        change = dot(normal, direction)
        if abs(change) < 1e-12:
            if value >= 0:
                return False
        elif change > 0:
            hi = min(hi, -value/change)
        else:
            lo = max(lo, -value/change)
        if hi-lo <= 1e-9:
            return False
    return hi > 1e-6 and lo < 1-1e-6


def box_planes(box):
    return [(tuple(sign if i == axis else 0 for i in range(3)),
             sign*box[side][axis]) for axis in range(3) for side, sign in ((0, -1), (1, 1))]


def sphere_obscures(a, b, center, radius, weld):
    direction, start = sub(b, a), sub(a, center)
    length2 = dot(direction, direction)
    t = max(0., min(1., -dot(start, direction)/length2)) if length2 else 0.
    nearest = tuple(a[i]+t*direction[i] for i in range(3))
    return math.dist(nearest, center) < max(0., radius-weld)


def segment_crosses_triangle(a, b, triangle, weld):
    """Open surfaces obstruct crossings, not everything inside their AABB."""
    p, q, r = triangle
    direction, e1, e2 = sub(b,a), sub(q,p), sub(r,p)
    normal = unit(cross(e1,e2))
    if normal is None or abs(dot(normal,direction)) <= weld:
        return False
    h = cross(direction,e2)
    determinant = dot(e1,h)
    if abs(determinant) <= 1e-12:
        return False
    offset = sub(a,p)
    u = dot(offset,h)/determinant
    v = dot(direction,cross(offset,e1))/determinant
    t = dot(e2,cross(offset,e1))/determinant
    margin = weld/max(math.dist(a,b),weld)
    return u >= -1e-8 and v >= -1e-8 and u+v <= 1+1e-8 and margin < t < 1-margin


def source_ref(instance, kind, index, supporting, material):
    return {"instance_id": instance["instance_id"], "model_id": instance["model_id"],
            "model": instance["name"], "source": instance["source"],
            "primitive": kind, "primitive_index": index,
            "supporting_faces": supporting, "gta_surface_material": material}


def candidates(region, weld, maximum_slope_degrees, stats):
    segments, obstacles = [], []
    low = [v-region["radius"] for v in region["center"]]
    high = [v+region["radius"] for v in region["center"]]
    slope = math.sin(math.radians(maximum_slope_degrees))
    for instance in region["instances"]:
        if instance["dynamic"]:
            stats["dynamic_instances_excluded"] += 1
            continue
        if instance["conditional"]:
            stats["conditional_instances_excluded"] += 1
            continue
        model = region["models"].get(instance["name"])
        require(model is not None and all(key in model for key in ("vertices", "faces", "boxes", "spheres")),
                "Region is missing original model primitives")
        require(instance["interior"] == region["interior"], "Mixed interior geometry")
        matrix = rotation_matrix(instance["quaternion"], instance["instance_flags"])
        position = instance["position"]
        finite(position)
        components = []
        for index, box in enumerate(model["boxes"]):
            require(len(box) == 10 and all(box[a] <= box[a+3] for a in range(3)), "Invalid COL box")
            finite(box[:6])
            points = [transform(p, matrix, position) for p in itertools.product(*zip(box[:3], box[3:6]))]
            faces = [(ids, box[6], face_index) for face_index, quad in enumerate(BOX_QUADS)
                     for ids in ((quad[0], quad[1], quad[2]), (quad[0], quad[2], quad[3]))]
            components.append(("box", index, points, faces))
            stats["input_boxes"] += 1
        for index, (points, faces) in enumerate(mesh_components(model["vertices"], model["faces"], weld, stats)):
            points = [transform(p, matrix, position) for p in points]
            components.append(("mesh", index, points, faces))
            stats["input_mesh_components"] += 1
            stats["input_mesh_faces"] += len(faces)
        for index, sphere in enumerate(model["spheres"]):
            require(len(sphere) == 8 and sphere[3] >= 0, "Invalid COL sphere")
            finite(sphere[:4])
            center = transform(sphere[:3], matrix, position)
            radius = sphere[3]
            obstacles.append(dict(tag=(instance["instance_id"], "sphere", index), kind="sphere",
                                  center=center, radius=radius,
                                  bounds=(tuple(v-radius for v in center), tuple(v+radius for v in center))))
            stats["spheres_without_artificial_edges"] += 1
        for kind, index, points, faces in components:
            tag = (instance["instance_id"], kind, index)
            component, rejected = classify_component(points, faces, weld)
            if component is None:
                used = {v for ids, mat, face in faces for v in ids}
                if used:
                    obstacle = dict(tag=tag, kind="ambiguous", bounds=bounds([points[i] for i in used]))
                    if rejected == "open_or_nonmanifold":
                        triangles = [tuple(points[i] for i in ids) for ids, _, _ in faces]
                        obstacle.update(kind="open", triangles=spatial_tree([
                            dict(points=triangle, bounds=bounds(triangle)) for triangle in triangles]))
                    obstacles.append(obstacle)
                stats[rejected+"_components_without_verified_volume"] += 1
                if kind != "mesh":
                    continue
                component = mesh_ridges(points, faces)
                stats["mesh_components_checked_for_local_ridges"] += 1
            else:
                obstacles.append(dict(tag=tag, kind="convex", bounds=component["bounds"], planes=component["planes"]))
                stats["closed_convex_components"] += 1
                stats["inward_components_reoriented"] += int(component["flipped"])
            borders = open_mesh_edges(points, component, weld) if rejected and kind == "mesh" else {}
            for edge, neighbors in component["edges"].items():
                if len(neighbors) != 2:
                    if edge in borders:
                        face, support, evidence = borders[edge]
                        a, b = (points[i] for i in edge)
                        length = math.dist(a,b)
                        if length > weld and abs(b[2]-a[2])/length <= slope and all(
                                low[i] <= p[i] <= high[i] for p in (a,b) for i in range(3)):
                            material = component["faces"][face][1]
                            source = source_ref(instance, kind, index, support, material)
                            source["edge_evidence"] = evidence
                            segments.append(dict(points=[a,b], material=material, tag=tag, source=source))
                            stats[evidence+"_edges"] += 1
                            continue
                    stats["boundary_or_nonmanifold_edges_excluded"] += 1
                    continue
                (_, ea, eb), (_, ec, ed) = neighbors
                if ea != ed or eb != ec:
                    stats["inconsistently_wound_edges_excluded"] += 1
                    continue
                a, b = (points[i] for i in edge)
                length = math.dist(a, b)
                if length <= weld or abs(b[2]-a[2])/length > slope:
                    stats["vertical_or_steep_edges_excluded"] += 1
                    continue
                f0, f1 = (item[0] for item in neighbors)
                n0, n1 = component["normals"][f0], component["normals"][f1]
                if n0 is None or n1 is None:
                    stats["degenerate_support_edges_excluded"] += 1
                    continue
                if dot(n0, n1) > math.cos(math.radians(15)):
                    stats["coplanar_or_shallow_edges_excluded"] += 1
                    continue
                if rejected and (max(n0[2], n1[2]) < .5 or min(n0[2], n1[2]) < -.1):
                    n0, n1 = tuple(-v for v in n0), tuple(-v for v in n1)
                if max(n0[2], n1[2]) < .5 or min(n0[2], n1[2]) < -.1:
                    stats["underside_or_side_edges_excluded"] += 1
                    continue
                # A roof/wall crease can be valid in an open or concave COL.
                # Reject its inward corner using each original opposite vertex.
                if any(dot(normal, sub(points[v], a)) > weld
                       for face, normal in ((f0, n1), (f1, n0))
                       for v in component["faces"][face][0] if v not in edge):
                    stats["locally_concave_edges_excluded"] += 1
                    continue
                if any(not low[i] <= p[i] <= high[i] for p in (a, b) for i in range(3)):
                    stats["outside_region_edges_excluded"] += 1
                    continue
                top = f0 if n0[2] >= n1[2] else f1
                material = component["faces"][top][1]
                require(type(material) is int and 0 <= material <= 255, "Invalid GTA surface material")
                support = [component["faces"][f][2] for f in (f0, f1)]
                if kind == "box":
                    support = [BOX_FACE_NAMES[i] for i in support]
                segments.append(dict(points=[a, b], material=material, tag=tag,
                                     source=source_ref(instance, kind, index, support, material)))
    tree = spatial_tree(obstacles)
    exposed = []
    for segment in segments:
        a, b = segment["points"]
        box = bounds((a, b))
        expanded = (tuple(v-weld for v in box[0]), tuple(v+weld for v in box[1]))
        rejected = None
        for obstacle in spatial_query(tree, expanded):
            if obstacle["tag"] == segment["tag"]:
                continue
            kind = obstacle["kind"]
            if kind == "convex":
                blocked = segment_in_planes(a, b, obstacle["planes"], weld)
            elif kind == "sphere":
                blocked = sphere_obscures(a, b, obstacle["center"], obstacle["radius"], weld)
            elif kind == "open":
                blocked = any(segment_crosses_triangle(a,b,triangle["points"],weld)
                              for triangle in spatial_query(obstacle["triangles"],expanded))
            else:
                # A nonconvex/open model has no verified solid halfspaces. Reject
                # overlapping candidates rather than guessing which side is solid.
                blocked = segment_in_planes(a, b, box_planes(obstacle["bounds"]), -weld)
            if blocked:
                rejected = "ambiguous_overlap" if kind == "ambiguous" else "occluded"
                break
        if rejected:
            stats[rejected+"_edges_excluded"] += 1
        else:
            exposed.append(segment)
    stats["candidate_edges_before_occlusion"] = len(segments)
    stats["exposed_candidate_edges"] = len(exposed)
    return exposed


def chain_segments(segments, weld, minimum_length, max_turn_degrees, maximum_nodes, stats):
    pool, edges, ambiguous = Weld(weld), {}, set()
    for segment in segments:
        a, b = [pool.add(p) for p in segment["points"]]
        if a == b:
            stats["weld_collapsed_edges"] += 1
            continue
        key = tuple(sorted((a, b)))
        if key in edges and edges[key]["material"] != segment["material"]:
            ambiguous.add(key)
        if key not in edges:
            edges[key] = {"material": segment["material"], "sources": []}
        edges[key]["sources"].append(segment["source"])
    for key in ambiguous:
        del edges[key]
    stats["ambiguous_material_edges_excluded"] += len(ambiguous)
    adjacent = defaultdict(set)
    for a, b in edges:
        adjacent[a].add(b)
        adjacent[b].add(a)
    unused = set(edges)
    turn = math.cos(math.radians(max_turn_degrees))

    def continuation(previous, current):
        if len(adjacent[current]) != 2:
            return None
        following = next(v for v in adjacent[current] if v != previous)
        before, after = tuple(sorted((previous, current))), tuple(sorted((current, following)))
        if edges[before]["material"] != edges[after]["material"]:
            return None
        incoming = unit(sub(pool.points[current], pool.points[previous]))
        outgoing = unit(sub(pool.points[following], pool.points[current]))
        return following if dot(incoming, outgoing) >= turn else None

    rails = []
    while unused:
        first, second = min(unused)
        path, evidence = [first, second], [edges[(first, second)]]
        unused.remove((first, second))
        for reverse in (False, True):
            if reverse:
                path.reverse()
                evidence.reverse()
            while path[-1] != path[0]:
                following = continuation(path[-2], path[-1])
                key = tuple(sorted((path[-1], following))) if following is not None else None
                if key not in unused:
                    break
                unused.remove(key)
                path.append(following)
                evidence.append(edges[key])
        points = [pool.points[i] for i in path]
        length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        if length < minimum_length:
            stats["short_chains_excluded"] += 1
            continue
        sources = {encoded(source): source for edge in evidence for source in edge["sources"]}
        rails.append(dict(points_gta=points, length_metres=length,
                          gta_surface_material=evidence[0]["material"],
                          sources=[sources[key] for key in sorted(sources)]))
    rails.sort(key=lambda rail: (-rail["length_metres"], rail["points_gta"]))
    kept, dropped, remaining = [], [], maximum_nodes
    for index, rail in enumerate(rails):
        rail["name"] = f"gta_candidate_{index:05d}"
        if len(rail["points_gta"]) <= remaining:
            kept.append(rail)
            remaining -= len(rail["points_gta"])
        else:
            dropped.append(rail)
    stats.update(eligible_chains=len(rails), eligible_nodes=sum(len(r["points_gta"]) for r in rails),
                 rail_chains=len(kept), rail_nodes=sum(len(r["points_gta"]) for r in kept),
                 rail_segments=sum(len(r["points_gta"])-1 for r in kept))
    return kept, {"maximum_nodes": maximum_nodes, "applied": bool(dropped),
                  "policy": "complete chains, longest first; drops reported explicitly",
                  "dropped_chains": len(dropped), "dropped_nodes": sum(len(r["points_gta"]) for r in dropped),
                  "dropped_length_metres": sum(r["length_metres"] for r in dropped)}


def generate(region, *, weld_metres=DEFAULT_WELD_METRES, minimum_length_metres=DEFAULT_MINIMUM_METRES,
             maximum_slope_degrees=35., maximum_turn_degrees=20., maximum_nodes=8192):
    require(region.get("format") == "gta-sa-region-v1" and region.get("coordinate_system") == "gta-z-up",
            "Expected a GTA static region in original Z-up metre coordinates")
    require(len(region["center"]) == 3, "Invalid region center")
    finite(region["center"])
    require(math.isfinite(region["radius"]) and 0 < region["radius"] <= 512, "Invalid candidate region radius")
    require(all(math.isfinite(v) for v in (weld_metres, minimum_length_metres, maximum_slope_degrees, maximum_turn_degrees))
            and .0001 <= weld_metres <= .02 and .05 <= minimum_length_metres <= 100
            and 0 <= maximum_slope_degrees <= 60 and 0 <= maximum_turn_degrees <= 45,
            "Invalid metre/angle candidate parameters")
    require(type(maximum_nodes) is int and 2 <= maximum_nodes <= 100000, "Invalid candidate node budget")
    require(len(region["instances"]) <= 10000 and len(region["models"]) <= 10000, "Candidate region is too large")
    require(all(instance.get("name") in region["models"] for instance in region["instances"]),
            "Region is missing an instance model")
    ids = [instance.get("instance_id") for instance in region["instances"]]
    require(all(type(i) is int and i > 0 for i in ids) and len(set(ids)) == len(ids),
            "Invalid or duplicate region instance identity")
    primitive_count = sum(len(model.get(key, [])) for model in region["models"].values()
                          for key in ("faces", "boxes", "spheres"))
    require(primitive_count <= 250000, "Candidate source exceeds primitive budget")
    require(sum(len(model.get("vertices", [])) for model in region["models"].values()) <= 750000,
            "Candidate source exceeds vertex budget")
    require(sum(len(region["models"][instance["name"]].get(key, []))
                for instance in region["instances"] for key in ("faces", "boxes", "spheres")) <= 250000,
            "Instanced candidate source exceeds primitive budget")
    stats = Counter(input_instances=len(region["instances"]))
    segments = candidates(region, weld_metres, maximum_slope_degrees, stats)
    rails, budget = chain_segments(segments, weld_metres, minimum_length_metres,
                                   maximum_turn_degrees, maximum_nodes, stats)
    return {"format": "gta-grind-candidates-v1", "algorithm_version": ALGORITHM_VERSION,
            "coordinate_system": "gta-z-up", "unit": "metre",
            "source_region": {"sha256": hashlib.sha256(encoded(region)).hexdigest(),
                              "center": region["center"], "radius": region["radius"],
                              "interior": region["interior"], "scene_stamp": region.get("scene"),
                              "omitted": region.get("omitted", {})},
            "parameters": {"weld_metres": weld_metres, "minimum_length_metres": minimum_length_metres,
                           "maximum_slope_degrees": maximum_slope_degrees,
                           "maximum_turn_degrees": maximum_turn_degrees},
            "stats": dict(stats), "budget": budget, "rails": rails,
            "physics_ready": False, "runtime_tested": False, "thaw_qb_emitted": False,
            "limitations": ["Boxes, local convex creases, upper panel borders and narrow strip borders generate candidates.",
                            "Wide flat borders, lower panel borders and nonmanifold edges are excluded.",
                            "Open surfaces reject crossing edges; ambiguous closed volumes remain conservative.",
                            "Partial exposed edges are not clipped; whole obstructed edges are excluded.",
                            "Candidate materials remain GTA ids; no THAW terrain mapping or scale is asserted.",
                            "No dynamic/conditional geometry or original THAW grind capture has been validated."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--center", type=float, nargs=3, required=True)
    parser.add_argument("--radius", type=float, default=128)
    parser.add_argument("--interior", type=int, default=0)
    parser.add_argument("--hour", type=float, default=12)
    parser.add_argument("--minimum-length", type=float, default=DEFAULT_MINIMUM_METRES)
    parser.add_argument("--weld", type=float, default=DEFAULT_WELD_METRES)
    parser.add_argument("--maximum-nodes", type=int, default=8192)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Refusing to overwrite existing rail candidates")
    require(math.isfinite(args.radius) and 0 < args.radius <= 512, "Invalid candidate region radius")
    region = query_world(args.database, args.center, args.radius, args.interior, hour=args.hour)
    result = generate(region, weld_metres=args.weld, minimum_length_metres=args.minimum_length,
                      maximum_nodes=args.maximum_nodes)
    with args.output.open("xb") as stream:
        stream.write(encoded(result))
    print(json.dumps({"output": str(args.output), "stats": result["stats"], "budget": result["budget"],
                      "runtime_tested": False, "physics_ready": False}))


if __name__ == "__main__":
    main()
