"""Author rail-only QB metadata for the original stock NodeArray loader.

This exports conservative GTA candidates, not gameplay code. Original native
THAW owns rail allocation, linking and grind physics. No game files are edited.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys

import geometry as g
import rails
import terrain_mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assets_export import qbkey, region, require
from carentan_qb import node_fields

BASENAME = "gta_rails"
ARRAY = qbkey(BASENAME + "_NodeArray")
MAX_NODES = 8192  # Below the original 0x4000-entry u16 rail hash table.
HEADER = bytes.fromhex("1c0802041004080c0c0802041402040c10100c00")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _basename(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_]{1,32}", value), "Invalid authored rail basename")
    return value


def _write(nodes, basename=BASENAME):
    """Same retail structs/vectors/arrays as carentan_rails, without a level template."""
    count = len(nodes)
    basename = _basename(basename)
    owner = qbkey(f"worlds/{basename}/{basename}.qb")
    out = bytearray(struct.pack("<2I", 0, 0) + HEADER)
    out.extend(struct.pack("<5I3I", 0xc0400, qbkey(basename+"_NodeArray"), owner, 48, 0, 0xa0100, count, 60))
    out.extend(bytes(count * 4))
    for index, row in enumerate(nodes):
        struct.pack_into("<I", out, 60 + index*4, len(out))
        out.extend(struct.pack("<2I", 0x10000, len(out)+8))
        fields = [(0x1b00, qbkey("Name"), row["name"]),
                  (0xd00, qbkey("Pos"), row["position_thaw"]),
                  (0xd00, qbkey("Angles"), (0., 0., 0.)),
                  (0x1b00, qbkey("Class"), qbkey("RailNode")),
                  (0x1b00, qbkey("Type"), qbkey(row["thaw_type"])),
                  (0x1b00, qbkey("TerrainType"), qbkey(row["thaw_terrain"])),
                  (0x1b00, 0, qbkey("CreatedAtStart"))]
        if row["next_index"] is not None:
            fields.append((0x1900, qbkey("Links"), row["next_index"]))
        previous = None
        for kind, key, value in fields:
            at = len(out)
            if previous is not None:
                struct.pack_into("<I", out, previous+12, at)
            previous = at
            out.extend(struct.pack("<4I", kind, key, value if kind == 0x1b00 else at+16, 0))
            if kind == 0xd00:
                out.extend(struct.pack("<I3f", 0x100, *value))
            elif kind == 0x1900:
                out.extend(struct.pack("<3I", 0x10100, 1, value))
    order = sorted(range(count), key=lambda i: nodes[i]["name"])
    for suffix, kind, values in (("_NodeArray_SortedNames", 0xd0100, [nodes[i]["name"] for i in order]),
                                 ("_NodeArray_SortedIndices", 0x10100, order)):
        at = len(out)
        out.extend(struct.pack("<5I3I", 0xc0400, qbkey(basename+suffix), owner, at+20, 0, kind, count, at+32))
        out.extend(struct.pack("<"+str(count)+"I", *values))
    struct.pack_into("<I", out, 4, len(out))
    return bytes(out)


def read_nodes(payload, basename=BASENAME):
    """Bounded independent readback; no source bytecode or executable callbacks."""
    require(60 <= len(payload) <= 2*1024*1024 and payload[:4] == bytes(4) and payload[8:28] == HEADER,
            "Unexpected authored QB header")
    require(struct.unpack_from("<I", payload, 4)[0] == len(payload), "Authored QB length differs")
    basename = _basename(basename)
    kind, key, owner, array, following = struct.unpack_from("<5I", payload, 28)
    require((kind, key, array, following) == (0xc0400, qbkey(basename+"_NodeArray"), 48, 0), "Unexpected authored NodeArray")
    kind, count, pointer = struct.unpack_from("<3I", payload, 48)
    require(kind == 0xa0100 and 2 <= count <= MAX_NODES and pointer == 60, "Invalid rail array extent")
    pointers = struct.unpack("<"+str(count)+"I", region(payload, pointer, count*4, "rail pointers"))
    require(len(set(pointers)) == count, "Duplicate rail structure pointers")
    # These authored field identities are constant across every node in the graph.
    name_key, pos_key, angles_key, class_key, type_key, terrain_key, links_key = (
        qbkey(name) for name in ("Name", "Pos", "Angles", "Class", "Type", "TerrainType", "Links"))
    required_fields = {0, name_key, pos_key, angles_key, class_key, type_key, terrain_key}
    allowed_fields = (required_fields, required_fields | {links_key})
    terrain_pairs = {(qbkey(kind), qbkey(terrain)): (kind, terrain)
                     for kind, terrain in terrain_mapping.RAIL_PAIRS}
    created = (0x1b00, qbkey("CreatedAtStart"))
    result = []
    for pointer in pointers:
        fields = node_fields(payload, pointer)
        require(set(fields) in allowed_fields, "Unexpected authored node fields")
        require(fields[class_key] == (0x1b00, qbkey('RailNode')) and
                fields[type_key][0] == fields[terrain_key][0] == 0x1b00 and
                (fields[type_key][1], fields[terrain_key][1]) in terrain_pairs,
                "Unexpected rail class/terrain")
        thaw_type, thaw_terrain = terrain_pairs[fields[type_key][1], fields[terrain_key][1]]
        require(fields[0] == created and fields[name_key][0] == 0x1b00, "Invalid rail activation/name")
        vectors = []
        for key in (pos_key, angles_key):
            kind, pointer = fields[key]
            marker, *vector = struct.unpack("<I3f", region(payload, pointer, 16, "rail vector"))
            require(kind == 0xd00 and marker == 0x100 and all(math.isfinite(x) for x in vector), "Invalid rail vector")
            vectors.append(vector)
        require(vectors[1] == [0., 0., 0.], "Unexpected rail angles")
        next_index = None
        if links_key in fields:
            kind, pointer = fields[links_key]
            array_kind, n, next_index = struct.unpack("<3I", region(payload, pointer, 12, "rail link"))
            require((kind, array_kind, n) == (0x1900, 0x10100, 1) and next_index < count, "Invalid rail link")
        result.append(dict(name=fields[name_key][1], position_thaw=vectors[0], next_index=next_index,
                           thaw_type=thaw_type, thaw_terrain=thaw_terrain))
    names = [row["name"] for row in result]
    require(len(set(names)) == count, "Duplicate rail checksums")
    tail = len(payload)-2*(32+count*4)
    for at, suffix, kind, values in ((tail, "_NodeArray_SortedNames", 0xd0100, sorted(names)),
                                   (tail+32+count*4, "_NodeArray_SortedIndices", 0x10100, sorted(range(count), key=names.__getitem__))):
        require(struct.unpack_from("<5I3I", payload, at) ==
                (0xc0400, qbkey(basename+suffix), owner, at+20, 0, kind, count, at+32), "Invalid sorted rail header")
        require(list(struct.unpack_from("<"+str(count)+"I", payload, at+32)) == values, "Invalid sorted rail lookup")
    return result


def build_rails(graph, scene, *, basename=BASENAME, sort_native=False):
    basename = _basename(basename)
    require(graph.get("format") == "gta-grind-candidates-v1" and graph.get("algorithm_version") == rails.ALGORITHM_VERSION
            and graph.get("unit") == "metre" and graph.get("coordinate_system") == "gta-z-up", "Unsupported rail candidates")
    source, coords = graph["source_region"], scene["coordinates"]
    require(scene.get("format") == "gta-thaw-static-scene-v1" and source["sha256"] == scene["source_region_sha256"]
            and source["interior"] == scene["selection"]["interior"] and source["scene_stamp"] == scene["scene_stamp"],
            "Rail candidates and COL must share the same complete region/scene selection")
    require(coords["thaw_units_per_gta_unit"] == g.THAW_UNITS_PER_GTA_UNIT and coords["axes"] == "(x,z,-y)",
            "Rail coordinates must use the validated physical COL transform")
    origin = coords["origin_gta"]
    require(len(origin) == 3 and all(math.isfinite(v) for v in origin), "Invalid rail origin")
    count = sum(len(row["points_gta"]) for row in graph["rails"])
    require(2 <= count <= MAX_NODES, "Rail node budget exceeded or graph empty; nothing was truncated")
    require(not graph["budget"]["applied"], "Candidate budget already dropped chains; choose a smaller complete region")
    nodes, names, segments = [], set(), set()
    for chain in graph["rails"]:
        label = chain["name"]
        require(isinstance(label, str) and re.fullmatch(r"[a-zA-Z0-9_]{1,64}", label), "Invalid rail chain name")
        points = chain["points_gta"]
        require(len(points) >= 2 and chain["sources"] and type(chain["gta_surface_material"]) is int
                and 0 <= chain["gta_surface_material"] <= 255, "Missing rail primitive/material provenance")
        converted = []
        for point in points:
            require(len(point) == 3 and all(math.isfinite(v) for v in point), "Invalid rail point")
            local = g.gta_to_thaw(point, origin=origin)
            require(all(abs(v) <= 1e6 for v in local), "Rail exceeds bounded local scene")
            converted.append(struct.unpack("<3f", struct.pack("<3f", *local)))
        require(len(set(converted)) == len(converted), "Collapsed/repeated rail vertices or closed ring require a separate validated path")
        for a, b in zip(converted, converted[1:]):
            edge = tuple(sorted((a, b)))
            require(edge not in segments, "Duplicate rail segment")
            segments.add(edge)
        first = len(nodes)
        for index, point in enumerate(converted):
            name = qbkey(f"{basename}_{label}_{index}")
            require(name not in names and name != 0, "Duplicate rail name/checksum")
            names.add(name)
            thaw_type, thaw_terrain = terrain_mapping.rail(chain['gta_surface_material'])
            nodes.append(dict(name=name, position_thaw=list(point), next_index=first+index+1 if index+1<len(points) else None,
                              previous_index=first+index-1 if index else None,
                              gta_surface_material=chain["gta_surface_material"], source_chain=label,
                              thaw_type=thaw_type, thaw_terrain=thaw_terrain))
    if sort_native:
        # Stock RailManagerBuild bubble-sorts names before linking. Authored
        # sorted input makes that original pass linear for large cached regions.
        order = sorted(range(len(nodes)), key=lambda i: nodes[i]['name'])
        remap = {old: new for new, old in enumerate(order)}
        nodes = [dict(nodes[i], **{key: remap[nodes[i][key]] if nodes[i][key] is not None else None
                                  for key in ('next_index', 'previous_index')}) for i in order]
    payload = _write(nodes, basename)
    actual = read_nodes(payload, basename)
    require(actual == [{key: row[key] for key in ("name", "position_thaw", "next_index", "thaw_type", "thaw_terrain")} for row in nodes],
            "Authored QB roundtrip differs")
    return payload, dict(format="gta-thaw-rail-export-v1", qb_sha256=hashlib.sha256(payload).hexdigest(), qb_bytes=len(payload),
                         basename=basename, array_checksum=qbkey(basename+"_NodeArray"), nodes=nodes, source_region=source,
                         source_candidates_sha256=hashlib.sha256(encoded(graph)).hexdigest(),
                         candidate_provenance=graph, coordinates=coords, scene_col_sha256=scene["col_sha256"],
                         node_count=len(nodes), link_count=len(segments),
                         terrain_policy=terrain_mapping.policy(),
                         closed_rings_supported=False, runtime_tested=False, grind_contact_verified=False, physics_ready=False)
