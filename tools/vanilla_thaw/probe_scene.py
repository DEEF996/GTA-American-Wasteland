"""Original stock COL loading and feeler queries on one authored THAW-unit floor.

Called by the bounded loader after genuine MemMan/QB startup. The existing
retained command thread executes every native call. No GTA scale is assumed;
scene, COL, list node and QB source stay owned by this child until termination.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import sys

import probe_loader as host

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assets_export import qbkey
from carentan_col import compile_col

ENGINE_PATH = "data/scripts/engine/engine_params.qb.wpc"
ENGINE_NAME = "scripts/engine/engine_params.qb.wpc"
ENGINE_BYTES = 1500
ENGINE_SHA = "0b06d36b0b2060f460b8d855269b3da05fd8f6f119e326fba58ae43a85af944e"
FLOOR_SHA = "3713f1effc5406339e0b646e508755bef6ba08895367b1f3a25c6b72a03304d5"
# Full original function pins, except the protected six-byte PCScene entry.
# Its caller is stock CRT slot 69 (0x689080); PCScene's vtable is independently
# stored by the complete stock destructor 0x51AAF0. No destructor is invoked.
PINS = {
    "exit_table": ("ExitTableInitialize", 0x66c0a3, 40, "20636c06ae19be1b75841eed6fc4e923a2b146b1e5b8893ffce79cc56f2220a7"),
    "moving_list": ("MovingListCRT", 0x685b80, 22, "fc048017b58da57a86790f045407f808fb0274f3757ecfe974affbe9aba96fea"),
    "world_list": ("WorldListCRT", 0x688ea0, 22, "23d55b49d9ff9dac409b20049a2353d329cfeff867cd5e27595b2db702a589c0"),
    "sector": ("SuperSectorInitialize", 0x520300, 407, "a5994de4b594c86f7cd22906460f11256d62a8e50475254c381284ebe0cd8077"),
    "platform_key": ("OriginalSectorPlatformKey", 0x4cf526, 22, "c08c6d54720588768e39b4a0636f570b2d0bb2e60d6544309b3d1bde4be65ac2"),
    "scene": ("PCSceneConstructor", 0x51a940, 6, "4a7a87b0511740732379367300757f7af630b13498fc452cb740102e71f92890"),
    "load_col": ("LoadCOLMemory", 0x4e3bb0, 434, "f39bfa41d7dcc69b2f1c97145b8afdbc504ce42568e47ff98233b9dcd3c4bf32"),
    "node": ("SceneListNodeConstructor", 0x4cfca0, 29, "0fa9894e78941131e4cc137411c32e83c6ed90f12ae75c93aa5495b08296459f"),
    "feeler": ("FeelerConstructor", 0x5ade40, 99, "7460b14671aa6d1df28531e51b0959d2ca18547e20b7c9b23a4fbe3f1d2da29c"),
    "query": ("FeelerQuery", 0x5adf60, 503, "bde976fae6ba996c557e73061ae255a8ce5fce800a44b003500f3b7f96b6141e"),
}


def floor_fixture():
    """Two upward faces, authored in native THAW units, with no GTA conversion."""
    result = compile_col({
        "positions": [[-128, 0, -128], [-128, 0, 128], [128, 0, 128], [128, 0, -128]],
        "indices": [[0, 1, 2], [0, 2, 3]],
    }, coordinates="thaw", scale=1.0)
    host.require(len(result) == 236 and hashlib.sha256(result).hexdigest() == FLOOR_SHA,
                 "Authored floor compiler output differs")
    return result


def select_engine(rows):
    matches = [row for row in rows if row.get("path", "").casefold() == ENGINE_PATH]
    host.require(len(matches) == 1 and matches[0].get("path") == ENGINE_PATH and
                 matches[0].get("bytes") == ENGINE_BYTES and matches[0].get("sha256") == ENGINE_SHA,
                 "Private resources must contain exactly one pinned whole engine parameters source")
    return matches[0]


def read_inputs(manifest_path, image_manifest):
    host.require(manifest_path.is_absolute() and image_manifest.is_absolute(), "Scene manifests must be absolute")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    image = json.loads(image_manifest.read_text(encoding="utf-8-sig"))
    directory = manifest_path.resolve().parent
    original = Path(image["source"]).resolve()
    host.require(manifest.get("format") == "gta-thaw-private-stock-resources-v1" and
                 Path(manifest["directory"]).resolve() == directory and
                 Path(manifest["source"]).resolve() == original and not directory.is_relative_to(original),
                 "Scene resources must belong to the selected stock source in a separate private cache")
    host.require(manifest.get("sources_revalidated_before_publish") is True and
                 manifest.get("original_files_modified") is False, "Private source validation is absent")
    select_engine(manifest.get("files", []))
    path = (directory / ENGINE_PATH).resolve()
    host.require(path.is_relative_to(directory) and not path.samefile(original / ENGINE_PATH),
                 "Scene source escapes cache or aliases installed original")
    source = path.read_bytes()
    host.require(len(source) == ENGINE_BYTES and hashlib.sha256(source).hexdigest() == ENGINE_SHA,
                 "Whole engine parameters bytes differ")
    return {"source": source, "floor": floor_fixture()}


def query_result(contents, hit, start, end, expected):
    host.require(len(contents) == 0xc0 and hit == expected, "Original floor hit/miss differs from control ray")
    normal, point = struct.unpack_from("<4f", contents, 0x24), struct.unpack_from("<4f", contents, 0x34)
    host.require(all(math.isfinite(value) for value in normal + point), "Original feeler returned nonfinite data")
    if hit:
        host.require(normal == (0., 1., 0., 0.) and abs(point[0] - start[0]) < 0.00001 and
                     abs(point[1]) < 0.00001 and abs(point[2] - start[2]) < 0.00001 and point[3] == 1.,
                     "Original floor contact disagrees with authored THAW geometry")
    # Feeler+4C/+4E are input masks. Contact commit428902/428915 writes
    # result flags WORD at+50 and terrain BYTE at+52 (not a second WORD).
    masks = struct.unpack_from("<2H", contents, 0x4c)
    flags, terrain = struct.unpack_from("<HB", contents, 0x50)
    return {"start": start, "end": end, "hit": hit, "normal": normal, "point": point,
            "flags": flags, "terrain": terrain, "input_masks": masks}


def run(api, process, record, deadline, log, inputs):
    source, floor = inputs["source"], inputs["floor"]
    host.require(len(source) == ENGINE_BYTES and hashlib.sha256(source).hexdigest() == ENGINE_SHA and
                 floor == floor_fixture(), "Scene inputs changed after validation")

    def read(address, size):
        return host.remote_read(api, process, address, size)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    def write(address, contents):
        host.remote_write(api, process, address, contents)

    def allocate(contents):
        address = host.success(api.VirtualAllocEx(process, None, max(4096, len(contents)), 0x3000, 4), "Allocate owned scene input")
        host.require(address % 16 == 0 and 0x10000 <= address < 0x100000000 - len(contents), "Scene allocation exceeds x86 space")
        write(address, contents)
        return address

    def call(name, args=(), this=None):
        pin = PINS[name] if name in PINS else host.QB_PINS[name]
        return host.native_call(api, process, pin, list(args), record, deadline, log, this)

    def check_pins():
        for name, address, size, digest in PINS.values():
            host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, "Scene entry differs: " + name)

    check_pins()
    arena_start, arena_end = word(0x84376c), word(0x843770)
    host.require(arena_end - arena_start == 256 * 1024 * 1024 and word(0x6dd0fc) != 0,
                 "Scene requires existing genuine MemMan and QB bootstrap")
    host.require(word(0x8405c0) == 0, "Scene probe requires no active collision cache")
    # Preserve the real protected instruction sequence used by this stock image.
    host.require(word(0x13dcfc6) ^ word(0x13ce53c) == 0xc6ad2f34, "Original sector Platform key differs")
    exit_begin, exit_current = word(0x8505f0), word(0x8505ec)
    if exit_begin == exit_current == 0:
        host.require(call("exit_table") == 0, "Original CRT exit table initialization failed")
        exit_begin, exit_current = word(0x8505f0), word(0x8505ec)
    host.require(0x10000 <= exit_begin <= exit_current < 0x100000000 and (exit_current - exit_begin) % 4 == 0,
                 "Original CRT exit table is incomplete")
    for name, address, vtable in (("moving_list", 0x6dd48c, 0x68f744), ("world_list", 0x7689f8, 0x6936a8)):
        if read(address, 20) == bytes(20):
            call(name)
        host.require(struct.unpack("<5I", read(address, 20)) == (vtable, 0xffffffff, 0, address, address),
                     "Original scene list is nonempty or has an unexpected layout")

    # Load the whole real loose QB. Existing symbols are rejected, never replaced.
    sections = ((0x08123851, 28, 48), (0xafb711d4, 732, 752))
    host.require(all(call("lookup", (key,)) == 0 for key, _, _ in sections), "Engine parameters are already registered")
    name_offset = (len(source) + 15) & ~15
    owned_source = allocate(source + bytes(name_offset - len(source)) + ENGINE_NAME.encode("ascii") + b"\0")
    source_crc = call("crc", (owned_source + name_offset,))
    host.require(source_crc == qbkey(ENGINE_NAME), "Original engine source filename checksum differs")
    call("load", (owned_source + name_offset, owned_source, 0))
    host.require(read(owned_source, 1) == b"\1", "Original engine source did not load")
    for key, offset, array in sections:
        symbol = call("lookup", (key,))
        host.require(symbol == owned_source + offset and
                     struct.unpack("<4I", read(symbol, 16)) == (0xc0400, key, source_crc, owned_source + array),
                     "Original engine array type, ownership or rebased source pointer differs")
    call("sector")
    host.require(read(0x7740f0, 8) == struct.pack("<fI", 1000.0, 40), "Original engine sector configuration differs")

    scene, col = allocate(bytes(0xa0)), allocate(floor)
    host.require(call("scene", (8,), scene) == scene and word(scene) == 0x69520c and word(scene + 0x94) == 0,
                 "Original PC scene constructor layout differs")
    write(scene + 0x10, struct.pack("<I", 1))  # Adapter-supplied static-world input flag.
    host.require(call("load_col", (col, 0), scene) & 0xff != 0, "Original COL memory loader failed")
    objects, sectors = word(scene + 0x6c), word(scene + 0x60)
    host.require(word(scene + 0x68) == 1 and word(scene + 0x94) == 0 and
                 objects % 4 == 0 and arena_start <= objects <= arena_end - 24 and
                 sectors % 4 == 0 and arena_start <= sectors < arena_end and
                 word(objects + 8) & 0xc000 == 0, "Native collision objects, sectors or borrowed ownership differ")
    node, root = allocate(bytes(20)), 0x7689f8
    host.require(call("node", this=node) == node and
                 struct.unpack("<5I", read(node, 20)) == (0x6936a8, 0xffffffff, 0, node, node),
                 "Original scene node constructor differs")
    # Publish one adapter-owned scene through the already initialized native list.
    write(node + 4, struct.pack("<I", scene))
    write(node + 12, struct.pack("<2I", root, root))
    write(root + 12, struct.pack("<2I", node, node))
    results = []
    for start, end, expected in (
        ((31., 64., -17.), (31., -64., -17.), True),
        ((-29., 64., 41.), (-29., -64., 41.), True),
        ((256., 64., 0.), (256., -64., 0.), False),
        ((-50., 64., -50.), (50., 64., 50.), False),
    ):
        feeler = allocate(bytes(0xc0))
        host.require(call("feeler", this=feeler) == feeler, "Original feeler constructor failed")
        write(feeler, struct.pack("<8f", *start, 1., *end, 1.))
        hit = bool(call("query", (0, 0, 0), feeler) & 0xff)
        result = query_result(read(feeler, 0xc0), hit, start, end, expected)
        results.append(result)
        log("scene_feeler", **result)
    check_pins()
    host.require(word(0x8405c0) == 0 and struct.unpack("<2I", read(root + 12, 8)) == (node, node),
                 "Original collision cache or owned scene publication changed")
    log("scene_pass", scene=scene, col_sha256=FLOOR_SHA, col_bytes=len(floor), objects=1, triangles=2,
        original_col_loader=True, original_feelers=True, hits=2, misses=2,
        engine_source_sha256=ENGINE_SHA, sector_max_dim=1000., sector_max_rows_columns=40,
        units="explicit THAW units; GTA physical scale unresolved", lifetime="child process owned",
        scene_swapping_verified=False, gameplay_ready=False, queries=results)
    return {"scene": scene, "col": col, "node": node, "source": owned_source}
