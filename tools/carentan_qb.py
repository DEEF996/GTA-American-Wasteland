"""Create a minimal THAW nodearray using the installed level's original restart.

New-file output only. This replaces level data, not original movement/trick code.
Only one Player1 restart survives; no old rails, triggers, cars or template nodes.
Zone scripts and supplementary nodearrays are separate and need runtime testing.
"""
import argparse
import json
import math
from pathlib import Path
import struct

from assets_export import qbkey, region, require
from carentan_col import replace_pak_member

NODEARRAY_TYPE = 0x49875607


def read_member(pak, member, kind=NODEARRAY_TYPE):
    cursor, first_payload, matches = 0, len(pak), []
    while cursor + 32 <= first_payload:
        fields = struct.unpack("<8I", region(pak, cursor, 32, "PAK header"))
        if fields[0] in (0xB524565F, 0x2CB3EF3B):
            require(len(matches) == 1, "Expected exactly one source nodearray")
            return matches[0]
        start = cursor + fields[1]
        first_payload = min(first_payload, start)
        if fields[0] == kind and fields[3] == qbkey(member):
            matches.append(bytes(region(pak, start, fields[2], "QB payload")))
        cursor += 192 if fields[7] & 0x20 else 32
    raise ValueError("PAK terminator missing")


def node_fields(data, node):
    marker, field = struct.unpack("<2I", region(data, node, 8, "QB struct header"))
    require(marker == 0x10000, "Unsupported QB struct marker")
    result, visited = {}, set()
    while field:
        require(field not in visited and len(visited) < 128, "Invalid QB field chain")
        visited.add(field)
        kind, key, value, following = struct.unpack("<4I", region(data, field, 16, "QB field"))
        require(key not in result, "Ambiguous duplicate restart field")
        result[key] = (kind, value)
        field = following
    return result


def compile_restart_qb(template, spawn, angles=(0.0, 0.0, 0.0), zone="z_el"):
    require(len(spawn) == len(angles) == 3 and all(math.isfinite(v) for v in (*spawn, *angles)), "Invalid restart transform")
    expected_header = bytes.fromhex("1c0802041004080c0c0802041402040c10100c00")
    require(template[8:28] == expected_header, "Unsupported THAW QB file header")
    section, key, file_key, array, _ = struct.unpack("<5I", region(template, 28, 20, "QB section"))
    require(section == 0xC0400 and key == qbkey(zone + "_NodeArray"), "Unexpected source nodearray")
    kind, count, pointer = struct.unpack("<3I", region(template, array, 12, "Node array"))
    require(kind == 0xA0100 and count > 0, "Source has no struct nodearray")
    first = pointer if count == 1 else struct.unpack("<I", region(template, pointer, 4, "First node pointer"))[0]
    original = node_fields(template, first)
    require(original.get(qbkey("Class")) == (0x1B00, qbkey("Restart")), "First source node is not an original restart")
    require(original.get(qbkey("Type")) == (0x1B00, qbkey("Player1")), "First source restart is not Player1")
    name_kind, name = original[qbkey("Name")]
    require(name_kind == 0x1B00, "Unexpected original restart name type")
    result = bytearray(template[:28])
    result.extend(struct.pack("<5I", 0xC0400, key, file_key, 48, 0))
    result.extend(struct.pack("<3I", 0xA0100, 1, 60))
    result.extend(struct.pack("<2I", 0x10000, 68))
    previous = None

    def field(kind, key, value=0, payload=None):
        nonlocal previous
        at = len(result)
        if previous is not None:
            struct.pack_into("<I", result, previous + 12, at)
        previous = at
        result.extend(struct.pack("<4I", kind, key, at + 16 if payload is not None else value, 0))
        if payload is not None:
            result.extend(payload)
            result.extend(bytes(-len(result) % 4))

    field(0x1B00, qbkey("Name"), name)
    field(0x0D00, qbkey("Pos"), payload=struct.pack("<I3f", 0x100, *spawn))
    field(0x0D00, qbkey("Angles"), payload=struct.pack("<I3f", 0x100, *angles))
    field(0x1B00, qbkey("Class"), qbkey("Restart"))
    field(0x1B00, qbkey("Type"), qbkey("Player1"))
    field(0x1B00, 0, qbkey("CreatedAtStart"))
    field(0x0700, qbkey("RestartName"), payload=b"start\0")
    field(0x1900, qbkey("restart_types"), payload=struct.pack("<3I", 0xD0100, 1, qbkey("Player1")))
    for suffix, array_kind, value in [("_NodeArray_SortedNames", 0xD0100, name),
                                      ("_NodeArray_SortedIndices", 0x10100, 0)]:
        at = len(result)
        result.extend(struct.pack("<5I", 0xC0400, qbkey(zone + suffix), file_key, at + 20, 0))
        result.extend(struct.pack("<3I", array_kind, 1, value))
    struct.pack_into("<I", result, 4, len(result))
    validate_restart_qb(result, spawn, zone)
    return bytes(result)


def validate_restart_qb(data, spawn, zone="z_el"):
    require(struct.unpack_from("<I", data, 4)[0] == len(data), "QB length mismatch")
    require(struct.unpack_from("<3I", data, 48) == (0xA0100, 1, 60), "Expected exactly one node")
    fields = node_fields(data, 60)
    require(len(fields) == 8 and fields[qbkey("Class")] == (0x1B00, qbkey("Restart")), "Unexpected restart fields")
    pointer = fields[qbkey("Pos")][1]
    marker, *actual = struct.unpack("<I3f", region(data, pointer, 16, "Restart position"))
    require(marker == 0x100 and all(abs(a - b) <= 0.01 for a, b in zip(actual, spawn)), "Restart position mismatch")
    for offset, suffix, kind, value in [(len(data) - 64, "_NodeArray_SortedNames", 0xD0100, fields[qbkey("Name")][1]),
                                       (len(data) - 32, "_NodeArray_SortedIndices", 0x10100, 0)]:
        section = struct.unpack_from("<5I", data, offset)
        require(section[0] == 0xC0400 and section[1] == qbkey(zone + suffix) and section[3] == offset + 20,
                "Invalid sorted-node section")
        require(struct.unpack_from("<3I", data, offset + 20) == (kind, 1, value), "Invalid sorted-node array")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_pak", type=Path)
    parser.add_argument("output_qb", type=Path)
    parser.add_argument("output_pak", type=Path)
    parser.add_argument("--spawn", type=float, nargs=3, required=True, help="Original THAW XYZ coordinates")
    parser.add_argument("--angles", type=float, nargs=3, default=(0.0, 0.0, 0.0), help="Original THAW Euler radians")
    args = parser.parse_args()
    require(not args.output_qb.exists() and not args.output_pak.exists(), "Outputs must be new files")
    require(args.output_qb.resolve() != args.output_pak.resolve(), "Output paths must differ")
    member = "worlds/worldzones/z_el/z_el.qb"
    pak = args.base_pak.read_bytes()
    template = read_member(pak, member)
    qb = compile_restart_qb(template, args.spawn, args.angles)
    patched = replace_pak_member(pak, member, qb, NODEARRAY_TYPE)
    require(read_member(patched, member) == qb, "Patched QB roundtrip failed")
    for path, payload in [(args.output_qb, qb), (args.output_pak, patched)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(payload)
    print(json.dumps({"qb": str(args.output_qb.resolve()), "pak": str(args.output_pak.resolve()),
                      "nodes": 1, "spawn_thaw": args.spawn, "angles_radians": args.angles,
                      "rail_nodes": 0, "runtime_tested": False}, indent=2))


if __name__ == "__main__":
    main()
