"""Bounded native import/query proof for a privately exported GTA static region.

Prepare all Python ray references before creating the owned stock child. Run
only after probe_scene.run in the same retained worker, with no game updates.
The existing floor guard and loader are unchanged. All allocations, including
the detached floor, remain alive until the parent's Job terminates the child.
"""
import hashlib
import json
import math
import struct

import probe_loader as host
import probe_scene as floor
from assets_export import parse_col
from carentan_col import validate_bsp
from native_worker import retained_worker, STATE, TID, COUNT, IDLE
from gta import terrain_mapping, surface_flags

WORLD = 0x7689f8
LIST_VTABLE = 0x6936a8
SCALE = 1 / .0254
POINT_TOLERANCE = .1  # THAW inches: 2.54 mm, distinct from sphere mesh error.
PINS = {name: floor.PINS[name] for name in ("scene", "load_col", "node", "feeler", "query")}
# Read-only layout witness, not a new function invocation. FeelerQuery passes
# this+0x24 as CollData (5AE02B). Contact commit reads CollData+28/+2A as
# exclusion/required masks (42889F/4288AB), writes output flags at +2C (428902)
# and a BYTE terrain at +2E (428915). Thus Feeler outputs are +50/+52;
# +4C/+4E are input masks. Original ctor5ADE61 initializes mask+4C to0x10.
PINS["contact_layout"] = ("OriginalContactLayout", 0x428830, 418,
                          "4004478c8caae9ad509454cd996f1818ad4189cc027f77275ea4ffeae07493ac")


def _sub(a, b):
    return tuple(x-y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _float_vector(value):
    host.require(len(value) == 3 and all(math.isfinite(v) and abs(v) <= 10_000_000 for v in value),
                 "Invalid GTA scene control vector")
    return struct.unpack("<3f", struct.pack("<3f", *value))


def _mesh(parsed):
    result = []
    ids = parsed["indices"]
    for i in range(0, len(ids), 3):
        a, b, c = [parsed["positions"][j] for j in ids[i:i+3]]
        e1, e2 = (b[0]-a[0], b[1]-a[1], b[2]-a[2]), (c[0]-a[0], c[1]-a[1], c[2]-a[2])
        normal = _cross(e1, e2)
        length = math.sqrt(sum((normal[0]*normal[0], normal[1]*normal[1], normal[2]*normal[2])))
        host.require(length > 0, "Native GTA scene proof requires nondegenerate control geometry")
        lower = (min(a[0], b[0], c[0]), min(a[1], b[1], c[1]), min(a[2], b[2], c[2]))
        upper = (max(a[0], b[0], c[0]), max(a[1], b[1], c[1]), max(a[2], b[2], c[2]))
        result.append((a, e1, e2, (normal[0]/length, normal[1]/length, normal[2]/length), lower, upper))
    return result


def _cast(mesh, start, end):
    """Nearest front-only and two-sided segment hits, using final float vertices."""
    direction = _sub(end, start)
    host.require(_dot(direction, direction) > 0, "Zero-length control ray")
    lower, upper = [tuple(op(a, b) for a, b in zip(start, end)) for op in (min, max)]
    hits = []
    for index, (a, e1, e2, normal, lo, hi) in enumerate(mesh):
        if any(upper[j] < lo[j] or lower[j] > hi[j] for j in range(3)):
            continue
        p = _cross(direction, e2)
        det = _dot(e1, p)
        if abs(det) <= 1e-12:
            continue
        offset = _sub(start, a)
        u = _dot(offset, p)/det
        if not -1e-9 <= u <= 1+1e-9:
            continue
        q = _cross(offset, e1)
        v = _dot(direction, q)/det
        if v < -1e-9 or u+v > 1+1e-9:
            continue
        t = _dot(e2, q)/det
        if 0 <= t <= 1:
            hits.append({"t": t, "face": index, "normal": normal, "front": det > 0,
                         "edge_margin": min(u, v, 1-u-v),
                         "point": tuple(a+t*b for a, b in zip(start, direction))})
    hits.sort(key=lambda item: (item["t"], item["face"]))
    front = [hit for hit in hits if hit["front"]]
    return front, hits


def _control(mesh, name, start, end, expected_hit, terrains=None, flags=None):
    start, end = _float_vector(start), _float_vector(end)
    front, both = _cast(mesh, start, end)
    host.require(bool(front) == bool(both) == expected_hit, "Control depends on unknown backface policy or misses")
    result = {"name": name, "start": start, "end": end, "hit": expected_hit}
    if expected_hit:
        first = both[0]
        host.require(first["front"] and front[0]["face"] == first["face"] and first["edge_margin"] > 1e-4,
                     "Control first hit is ambiguous or on a triangle edge")
        for other in both[1:]:
            if math.dist(first["point"], other["point"]) > POINT_TOLERANCE:
                break
            host.require(_dot(first["normal"], other["normal"]) > .99999,
                         "Control has competing contact normals")
            host.require(terrains is None or terrains[first['face']] == terrains[other['face']],
                         'Control has competing contact materials')
            host.require(flags is None or flags[first['face']] == flags[other['face']],
                         'Control has competing contact flags')
        result.update(point=first["point"], normal=first["normal"], face=first["face"], flags=0, terrain=0)
    return result


def _validate_col(payload, report):
    host.require(isinstance(payload, bytes) and 236 <= len(payload) <= 16*1024*1024,
                 "GTA scene COL exceeds byte budget")
    header = struct.unpack_from("<12I", payload)
    version, objects, vertices, faces = header[:4]
    host.require(version == 10 and 1 <= objects <= 4096 and 1 <= vertices <= 500000 and
                 1 <= faces <= 200000 and header[4:10] == (0, vertices, 0, 0, 0, 0) and
                 1 <= header[10] <= 40 and 1 <= header[11] <= 40, "Unsupported GTA scene COL profile")
    host.require(report.get("format") == "gta-thaw-static-scene-v1" and
                 report.get("col_sha256") == hashlib.sha256(payload).hexdigest() and
                 report.get("selected_primitives_complete") is True and
                 report.get("live_collision_complete") is False and report.get("runtime_tested") is False,
                 "GTA scene payload/report provenance differs or is incomplete")
    stats, coordinates = report["stats"], report["coordinates"]
    host.require((stats["col_triangles"], stats["col_objects"], stats["col_bytes"]) == (faces, objects, len(payload)) and
                 coordinates["thaw_units_per_gta_unit"] == SCALE and coordinates["axes"] == "(x,z,-y)" and
                 coordinates["position_storage"] == "float32", "GTA scene counts or physical transform differ")
    _float_vector(coordinates["origin_gta"])
    provenance = report["face_provenance_in_col_order"]
    host.require(len(provenance) == faces and
                 all(len(row) == 4 and row[1] in ("triangle", "box", "sphere") for row in provenance),
                 "GTA scene face provenance is incomplete")
    parsed = parse_col(payload)
    validate_bsp(payload)
    indices, positions = parsed['indices'], parsed['positions']
    flags = surface_flags.collision_flags(positions,
        [indices[offset:offset+3] for offset in range(0, len(indices), 3)])
    host.require(parsed["stats"]["vertices"] == vertices and parsed["stats"]["triangles"] == faces and
                 parsed["stats"]["degenerate_triangles_preserved"] == 0 and
                 parsed['face_flags'] == flags and report.get('surface_policy') == surface_flags.surface_policy(flags) and
                 report.get('wallride_triangles') == sum(bool(flag & terrain_mapping.WALL_RIDABLE) for flag in flags) and
                 report.get('vert_triangles', 0) == sum(bool(flag & surface_flags.VERT) for flag in flags) and
                 report.get('bank_triangles', 0) == sum(bool(flag & surface_flags.BANK) for flag in flags) and
                 parsed['terrain_types'] == [terrain_mapping.ground(row[3][0]) for row in provenance],
                 "GTA scene proof requires nondegenerate faces with the declared material policy")
    for offset, side in ((48, "min"), (64, "max")):
        bounds = struct.unpack_from("<4f", payload, offset)
        host.require(bounds[:3] == tuple(parsed["bounds"][side]) and bounds[3] == 1 and
                     all(math.isfinite(v) and abs(v) <= 10_000_000 for v in bounds[:3]),
                     "GTA scene header bounds differ from its actual vertices")
    host.require(all(parsed["bounds"]["min"][i] < parsed["bounds"]["max"][i] for i in (0, 2)),
                 "GTA scene has no horizontal extent")
    first_vertex = first_face = 0
    checksums = set()
    for index, obj in enumerate(parsed["objects"]):
        at = 80 + index*64
        key, flags, nv, nf, small, fixed, fo = struct.unpack_from("<IHHHBBI", payload, at)
        vo, bsp, io, extra = struct.unpack_from("<4I", payload, at+48)
        host.require(key not in checksums and flags == small == fixed == extra == 0 and
                     1 <= nv <= 150 and 1 <= nf <= 50 and
                     struct.unpack_from("<f", payload, at+28)[0] == struct.unpack_from("<f", payload, at+44)[0] == 1 and
                     (fo, vo, bsp, io) == (first_face*10, first_vertex*12, index*8, first_vertex),
                     "GTA scene object packing/offsets/ownership differs")
        checksums.add(key)
        first_vertex += nv
        first_face += nf
        for axis in range(3):
            host.require(parsed["bounds"]["min"][axis] <= obj["bounds"]["min"][axis] <=
                         obj["bounds"]["max"][axis] <= parsed["bounds"]["max"][axis], "GTA scene object bounds differ")
    host.require((first_vertex, first_face) == (vertices, faces), "GTA scene object totals differ")
    vertex_base = (80+objects*64+15) & ~15
    face_base = (vertex_base+vertices*13+3) & ~3
    tail = (face_base+faces*11+3) & ~3
    host.require(struct.unpack_from("<I", payload, tail)[0] == objects*8 and
                 tail+4+objects*8+faces*2 == len(payload), "GTA scene tail is outside the generated COL profile")
    return parsed


def _seal(inputs):
    # Rails are an independently validated auxiliary resource containing raw
    # QB bytes. RailStream checks its hash, full nodes and matched COL/origin;
    # they do not alter this collision metadata or its byte-hash check.
    metadata = {key: value for key, value in inputs.items() if key not in ("col", "seal", "rails")}
    return hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def prepare_inputs(payload, report):
    """Validate private bytes/report and compute controls BEFORE child startup."""
    parsed = _validate_col(payload, report)
    mesh, cases = _mesh(parsed), []
    origin, center = report["coordinates"]["origin_gta"], report["selection"]["center_gta"]
    center = tuple((center[i]-origin[i])*SCALE for i in range(3))
    center = (center[0], center[2], -center[1])
    try:
        cases.append(_control(mesh, "query_center_vertical", (center[0], center[1]+5*SCALE, center[2]),
                              (center[0], center[1]-100*SCALE, center[2]), True, parsed['terrain_types'], parsed['face_flags']))
    except RuntimeError:
        pass  # This query can be over a hole or an ambiguous seam; primitive controls remain mandatory.
    provenance = report["face_provenance_in_col_order"]
    kinds = sorted({row[1] for row in provenance})
    for kind in kinds:
        candidates = []
        for index, (a, e1, e2, normal, _, _) in enumerate(mesh):
            if provenance[index][1] != kind:
                continue
            centroid = tuple(a[j]+(e1[j]+e2[j])/3 for j in range(3))
            candidates.append((math.dist(centroid, center), index, centroid, normal))
        candidates.sort(key=lambda row: (row[0], row[1]))
        probes = candidates[:16]
        # A tessellated primitive may occupy all nearest facets yet be occluded.
        # Keep the original controls first, then try other source primitives.
        seen_primitives = {tuple(provenance[row[1]][:3]) for row in probes}
        for row in candidates[16:]:
            primitive = tuple(provenance[row[1]][:3])
            if primitive in seen_primitives:
                continue
            seen_primitives.add(primitive)
            probes.append(row)
            if len(probes) == 32:
                break
        selected = None
        for _, index, centroid, normal in probes:
            start, end = [tuple(c+sign*4*n for c, n in zip(centroid, normal)) for sign in (1, -1)]
            try:
                candidate = _control(mesh, "primitive_"+kind, start, end, True, parsed['terrain_types'], parsed['face_flags'])
            except RuntimeError:
                continue
            if provenance[candidate["face"]][1] == kind:
                selected = candidate
                break
        host.require(selected is not None, "No unambiguous front-facing control for GTA primitive " + kind)
        cases.append(selected)
    lo, hi = parsed["bounds"]["min"], parsed["bounds"]["max"]
    cases.append(_control(mesh, "outside_bounds", (hi[0]+128, hi[1]+128, hi[2]+128),
                          (hi[0]+128, lo[1]-128, hi[2]+128), False))
    for case in cases:
        if case["hit"]:
            case['terrain'] = parsed['terrain_types'][case['face']]
            case['flags'] = parsed['face_flags'][case['face']]
            case["source_primitive"] = provenance[case["face"]]
            objects, vertices = parsed["stats"]["objects"], parsed["stats"]["vertices"]
            vertex_base = (80+objects*64+15) & ~15
            case["col_face_offset"] = ((vertex_base+vertices*13+3) & ~3)+case["face"]*10
            case["source_face_bytes"] = payload[case["col_face_offset"]:case["col_face_offset"]+10].hex()
    result = {"format": "gta-thaw-scene-probe-v1", "col": payload,
              "col_sha256": report["col_sha256"], "source_region_sha256": report["source_region_sha256"],
              "stats": dict(parsed["stats"]), "bounds": parsed["bounds"], "cases": cases,
              "coordinates": dict(report["coordinates"]), "selection": dict(report["selection"]),
              "backface_policy": "unverified; all controls agree for front-only and two-sided intersections"}
    result["seal"] = _seal(result)
    return result


def query_result(contents, hit, case):
    host.require(len(contents) == 0xc0 and type(hit) is bool and hit == case["hit"],
                 "Original GTA scene hit/miss disagrees with independent control")
    normal, point = struct.unpack_from("<4f", contents, 0x24), struct.unpack_from("<4f", contents, 0x34)
    host.require(all(math.isfinite(v) for v in normal+point), "Original GTA scene contact is nonfinite")
    masks = struct.unpack_from("<2H", contents, 0x4c)
    flags, terrain = struct.unpack_from("<HB", contents, 0x50)
    host.require(masks == (0x10, 0), "Original feeler input masks changed")
    if hit:
        length = math.sqrt(_dot(normal[:3], normal[:3]))
        host.require(normal[3] == 0 and point[3] == 1 and abs(length-1) <= .001 and
                     _dot(normal[:3], case["normal"])/length > .99999 and
                     math.dist(point[:3], case["point"]) <= POINT_TOLERANCE and
                     flags == case["flags"] and terrain == case["terrain"],
                     "Original GTA scene contact point/normal/material differs")
    return {"name": case["name"], "hit": hit, "normal": normal, "point": point,
            "flags": flags, "terrain": terrain, "input_masks": masks}


def run(api, process, record, deadline, log, inputs, previous):
    """Replace the sole owned floor at idle; retain both scenes until child exit."""
    host.require(inputs.get("format") == "gta-thaw-scene-probe-v1" and inputs.get("seal") == _seal(inputs) and
                 hashlib.sha256(inputs["col"]).hexdigest() == inputs["col_sha256"] and
                 236 <= len(inputs["col"]) <= 16*1024*1024, "Prepared GTA scene changed after validation")
    worker = retained_worker(api, process)

    def read(address, size):
        return host.remote_read(api, process, address, size)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    def write(address, contents):
        host.remote_write(api, process, address, contents)

    def allocate(contents):
        address = host.success(api.VirtualAllocEx(process, None, max(4096, len(contents)), 0x3000, 4),
                               "Allocate owned GTA scene input")
        host.require(address % 16 == 0 and 0x10000 <= address < 0x100000000-len(contents), "GTA scene allocation exceeds x86 space")
        write(address, contents)
        return address

    def call(name, args=(), this=None):
        return host.native_call(api, process, PINS[name], list(args), record, deadline, log, this)

    def idle():
        state = worker.snapshot()
        host.require(state[STATE//4] == IDLE and state[TID//4] == worker.tid and state[COUNT//4] == worker.count and
                     api.WaitForSingleObject(worker.thread, 0) == 258 and word(0x8405c0) == 0,
                     "Scene publication requires its idle owned worker and no active collision cache")

    def pins():
        for name, address, size, digest in PINS.values():
            host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, "GTA scene entry changed: " + name)

    def published(scene, node):
        host.require(struct.unpack("<5I", read(WORLD, 20)) == (LIST_VTABLE, 0xffffffff, 0, node, node) and
                     struct.unpack("<5I", read(node, 20)) == (LIST_VTABLE, scene, 0, WORLD, WORLD),
                     "World list must contain exactly the expected owned scene node")

    idle()
    pins()
    old_scene, old_node = previous["scene"], previous["node"]
    host.require(all(type(previous.get(key)) is int and 0x10000 <= previous[key] < 0xfffff000 and previous[key] % 16 == 0
                     for key in ("scene", "node", "col", "source")), "Previous floor ownership record is invalid")
    published(old_scene, old_node)
    host.require(word(old_scene) == 0x69520c and word(old_scene+0x68) == 1 and word(old_scene+0x94) == 0 and
                 read(0x7740f0, 8) == struct.pack("<fI", 1000., 40), "Existing native floor/configuration differs")
    arena_start, arena_end = word(0x84376c), word(0x843770)
    host.require(arena_end-arena_start == 256*1024*1024, "Existing native arena differs")
    scene, col, node = allocate(bytes(0xa0)), allocate(inputs["col"]), allocate(bytes(20))
    host.require(call("scene", (8,), scene) == scene and word(scene) == 0x69520c and word(scene+0x94) == 0,
                 "Original GTA PC scene constructor differs")
    write(scene+0x10, struct.pack("<I", 1))
    host.require(call("load_col", (col, 0), scene) & 0xff != 0, "Original GTA region COL import failed")
    count = inputs["stats"]["objects"]
    objects, sectors = word(scene+0x6c), word(scene+0x60)
    host.require(word(scene+0x68) == count and word(scene+0x94) == 0 and objects % 4 == 0 and
                 arena_start <= objects <= arena_end-count*24 and sectors % 4 == 0 and arena_start <= sectors < arena_end,
                 "Original GTA scene object count, supersectors or allocation differs")
    object_words = struct.unpack("<"+str(count*6)+"I", read(objects, count*24))
    host.require(all(object_words[i+2] & 0xc000 == 0 for i in range(0, len(object_words), 6)),
                 "Original GTA scene objects unexpectedly own the borrowed COL buffer")
    host.require(call("node", this=node) == node and struct.unpack("<5I", read(node, 20)) ==
                 (LIST_VTABLE, 0xffffffff, 0, node, node), "Original GTA scene list node differs")
    write(node+4, struct.pack("<I", scene))
    write(node+12, struct.pack("<2I", WORLD, WORLD))
    idle()
    published(old_scene, old_node)
    # Only this host produces worker commands. No game update runs during these
    # two writes; this is idle publication, not a hardware-atomic 64-bit store.
    write(WORLD+12, struct.pack("<2I", node, node))
    write(old_node+12, struct.pack("<2I", old_node, old_node))
    published(scene, node)
    host.require(struct.unpack("<2I", read(old_node+12, 8)) == (old_node, old_node), "Old floor did not detach")
    results = []
    for case in inputs["cases"]:
        feeler = allocate(bytes(0xc0))
        host.require(call("feeler", this=feeler) == feeler, "Original GTA feeler construction failed")
        host.require(read(feeler+0x4c, 4) == struct.pack("<2H", 0x10, 0), "Original feeler constructor masks differ")
        write(feeler, struct.pack("<8f", *case["start"], 1., *case["end"], 1.))
        hit = bool(call("query", (0, 0, 0), feeler) & 0xff)
        contents = read(feeler, 0xc0)
        source_face = None
        if case["hit"]:
            source_face = read(col+case["col_face_offset"], 10)
            host.require(source_face.hex() == case["source_face_bytes"], "Original COL loader/query changed selected face bytes")
        # Preserve diagnostics even when the following independent assertion fails.
        log("gta_scene_contact", name=case["name"], hit=hit,
            native_normal=struct.unpack_from("<4f", contents, 0x24),
            native_point=struct.unpack_from("<4f", contents, 0x34),
            native_input_masks=struct.unpack_from("<2H", contents, 0x4c),
            native_material=struct.unpack_from("<HB", contents, 0x50),
            native_face_index=struct.unpack_from("<I", contents, 0x44)[0],
            selected_source_material=struct.unpack_from("<2H", source_face) if source_face else None,
            selected_source_face_unchanged=source_face is not None, expected=case)
        result = query_result(contents, hit, case)
        results.append(result)
        log("gta_scene_feeler", **result, expected=case)
    idle()
    published(scene, node)
    pins()
    log("gta_scene_pass", col_sha256=inputs["col_sha256"], col_bytes=len(inputs["col"]), **inputs["stats"],
        original_col_loader=True, original_feelers=True, point_tolerance_thaw=POINT_TOLERANCE,
        backface_policy=inputs["backface_policy"], coordinates=inputs["coordinates"],
        compiler_supersector_divisor=10000, native_sector_max_dim=1000.,
        hits=sum(result["hit"] for result in results), misses=sum(not result["hit"] for result in results),
        idle_scene_replacement=True, retired_allocations_retained=True,
        streaming_verified=False, gameplay_ready=False)
    return {"scene": scene, "col": col, "node": node, "source": previous["source"], "retired": previous,
            "queries": results, "col_sha256": inputs["col_sha256"]}
