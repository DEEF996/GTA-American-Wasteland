"""Load the whole private stock Global PAK through its authored original command.

The native parser reads the cached whole startup source. No replacement tokens,
resource objects, callback implementation or Script PC/flags are supplied here.
Resources and parameter storage remain owned by the bounded child until exit.
"""
import hashlib
import json
from pathlib import Path
import struct

import probe_core
import probe_fonts as fonts
import probe_loader as host
import probe_native
import probe_resources
from import_assets import GLOBAL, INPUTS

PAIRS = (GLOBAL, GLOBAL.replace(".pak.", ".pab."))
LOAD_PAK_KEY = 0x35460f50
LOAD_PAK = ("OriginalLoadPakDispatch", 0x49e5d0, 6,
            "3ea40501c11584c33057dac5ca72268885feb21d1cecf4d97570a4a1e66b5c25")
PARAM_BEGIN, PARAM_END = 507, 552
PARAM_SHA = "06e3015b171b1ec658fcfabd4e0b96c7a4f0b585661218e22cf4ba57ac7738c8"
FILENAME = b"worlds/global/global.pak\0"
FLAGS = tuple(probe_native.qbkey(name) for name in ("localized", "supress_scene", "splitfile"))
BALANCE_KEY, FONT_KEY = 0xeeff724d, 0x8c95dd40
RESOURCE_MAP, MAP_DESTRUCTOR, MAP_CRT_INDEX = 0x7740b4, 0x68d080, 72
MAP_PINS = {
    "ResourceMapCRT": ("OriginalResourceMapCRT", 0x6890e0, 22,
                       "29d1ce84bdc07810f8c71f4ce0a48525ef5c3f2830590927887ef88eb0e92759"),
    "ResourceMapCtor": ("OriginalResourceMapCtorDispatch", 0x51c9b0, 6,
                        "aedc5adfb17e436c89d0bf4b1cb8c6d105f936faa8fda384df05e2e4daebd548"),
    "ResourceMapExit": ("OriginalResourceMapExit", MAP_DESTRUCTOR, 10,
                        "fc0062173abf05ba1e37bd8495e19d5b01cd73a9f67159452e88c011a680d8f1"),
}


def initialize_resource_map(system, call, log, metadata):
    """Invoke its actual CRT initializer; never synthesize a tree or its links."""
    read, word = system.read, system.word
    for name, address, size, digest in MAP_PINS.values():
        host.require(hashlib.sha256(read(address, size)).hexdigest() == digest,
                     "Original resource-map entry differs: " + name)
    crt = metadata["crt_table"]
    host.require(hashlib.sha256(read(crt["va"], crt["bytes"])).hexdigest() == crt["sha256"] and
                 word(crt["va"] + MAP_CRT_INDEX * 4) == MAP_PINS["ResourceMapCRT"][1],
                 "Resource-map initializer is not the original CRT table member")

    def callbacks():
        begin, end = word(0x8505f0), word(0x8505ec)
        host.require(begin >= 0x10000 and begin % 4 == end % 4 == 0 and 0 <= end - begin <= 1024,
                     "Resource-map CRT callback table is absent or oversized")
        return struct.unpack(f"<{(end - begin) // 4}I", read(begin, end - begin))

    preserved = read(RESOURCE_MAP, 12)
    before, heap = callbacks(), word(0x84f5c4)
    vm = read(0x6dcf84, 16)
    host.require(preserved == bytes(12) and heap >= 0x10000 and MAP_DESTRUCTOR not in before,
                 "Original resource map is not cold or its CRT heap is absent")
    call("ResourceMapCRT")
    head = word(RESOURCE_MAP + 4)
    host.require(head >= 0x10000 and head % 4 == 0,
                 "Original resource-map constructor did not publish an aligned sentinel")
    size = system.call("kernel32.dll", "HeapSize", (heap, 0, head))
    host.require(size != 0xffffffff and size >= 20,
                 "Original resource-map sentinel is outside its actual CRT allocation")
    node = struct.unpack("<5I", read(head, 20))
    # The native lookup reads +11 as isNil. Key/padding are uninitialized;
    # neither those bytes nor a guessed adjacent object participate in this check.
    host.require(read(RESOURCE_MAP, 4) == preserved[:4] and word(RESOURCE_MAP + 8) == 0 and
                 node[:3] == (head, head, head) and (node[4] >> 8 & 0xff) != 0 and
                 callbacks() == before + (MAP_DESTRUCTOR,) and word(0x84f5c4) == heap and
                 read(0x6dcf84, 16) == vm,
                 "Original resource-map sentinel, destructor registration or VM preservation differs")
    log("global_resource_map_pass", map=RESOURCE_MAP, sentinel=head, count=0,
        original_crt_index=MAP_CRT_INDEX, original_initializer=MAP_PINS["ResourceMapCRT"][1],
        original_destructor=MAP_DESTRUCTOR, crt_heap_allocation_checked=True,
        host_tree_writes=False, lifetime="retained child process")


def validate_parameters(body):
    host.require(len(body) == fonts.BODY_BYTES and body[502] == 0x43 and
                 struct.unpack_from("<I", body, 503)[0] == LOAD_PAK[1] and
                 hashlib.sha256(body[PARAM_BEGIN:PARAM_END + 1]).hexdigest() == PARAM_SHA,
                 "Whole cached startup Global LoadPak call/parameters differ")


def validate_fields(read, words, params):
    """Inspect original CStruct output, without constructing replacement fields."""
    host.require(read(params, 4) == bytes(4), "Original parameter CStruct header differs")
    pointer = struct.unpack("<I", read(params + 4, 4))[0]
    result, seen = [], set()
    while pointer:
        host.require(pointer not in seen and len(seen) < 4, "Global parameter chain cycles or exceeds four fields")
        seen.add(pointer)
        flags, key, value, pointer = words(pointer, 4)
        result.append(((flags >> 9) & 0x7f, key, value))
    strings = [row for row in result if row[:2] == (3, 0)]
    host.require(len(result) == 4 and len(strings) == 1, "Global filename is not the authored unnamed string")
    host.require(read(strings[0][2], len(FILENAME)) == FILENAME and
                 {row for row in result if row[:2] != (3, 0)} == {(13, 0, key) for key in FLAGS},
                 "Native parser changed the complete authored Global filename/flags")


def read_inputs(qb_manifest, resource_manifest, image_manifest):
    source = fonts.read_inputs(qb_manifest, image_manifest)
    host.require(resource_manifest.is_absolute(), "Global resource manifest must be absolute")
    manifest = json.loads(resource_manifest.read_text(encoding="utf-8-sig"))
    image = json.loads(image_manifest.read_text(encoding="utf-8-sig"))
    directory, original = resource_manifest.resolve().parent, Path(image["source"]).resolve()
    host.require(manifest.get("format") == "gta-thaw-private-stock-resources-v1" and
                 manifest.get("sources_revalidated_before_publish") is True and
                 manifest.get("original_files_modified") is False and
                 Path(manifest["source"]).resolve() == original and
                 Path(manifest["directory"]).resolve() == directory and not directory.is_relative_to(original),
                 "Global files must use the verified private copy of the selected original source")
    files = []
    for name in PAIRS:
        size, digest = INPUTS[name]
        rows = [row for row in manifest["files"] if row["path"].lower() == name.lower()]
        host.require(len(rows) == 1 and rows[0]["bytes"] == size and rows[0]["sha256"] == digest,
                     "Whole original Global archive pin differs")
        path, source_path = (directory / name).resolve(), (original / name).resolve()
        host.require(path.is_relative_to(directory) and source_path.is_relative_to(original) and
                     not path.samefile(source_path), "Global cache path aliases or escapes its owned directory")
        actual, expected = path.read_bytes(), source_path.read_bytes()
        host.require(len(actual) == size and hashlib.sha256(actual).hexdigest() == digest and actual == expected,
                     "Private and original whole Global archive bytes differ")
        files.append({"path": name, "bytes": size, "sha256": digest})
    return {"source": source, "directory": directory, "files": files}


def run(api, process, record, deadline, log, inputs, preloaded, system):
    source = fonts.validate_source(inputs["source"])
    metadata = probe_resources.read_metadata()
    pins = {**host.QB_PINS, **host.VM_PINS, **probe_native.PINS,
            **{name: fonts.PINS[name] for name in ("ParseParameters", "StructCtor", "FontLookup", "FontVtable")},
            **{row[0]: row for row in metadata["functions"]}, **MAP_PINS, "LoadPak": LOAD_PAK}
    read, word = system.read, system.word

    def call(name, args=(), this=None):
        return host.native_call(api, process, pins[name], list(args), record, deadline, log, this)

    start, end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and start <= address <= end - count * 4,
                     "Global resource or Script escapes its original arena")
        return struct.unpack(f"<{count}I", read(address, count * 4))

    def registry():
        expected, pointer, previous, result = word(0x6dcf90), word(0x6dcf8c), 0, {}
        host.require(expected <= 4096, "Global helper Script registry exceeds its bound")
        while pointer:
            host.require(pointer not in result and len(result) < expected, "Global helper Script registry cycles")
            row = words(pointer, 63)
            host.require(row[2] == previous, "Original Script back-link differs")
            result[pointer] = (row[0xb0 // 4], row[0xc4 // 4])
            previous, pointer = pointer, row[1]
        host.require(len(result) == expected and len({row[0] for row in result.values()}) == expected,
                     "Original Script count/identity differs")
        return result

    cwd = system.allocate(bytes(2048))
    length = system.call("kernel32.dll", "GetCurrentDirectoryW", (1024, cwd))
    host.require(0 < length < 1024 and Path(read(cwd, length * 2).decode("utf-16le")).resolve() == inputs["directory"],
                 "Native Global file access is outside the verified private working directory")
    host.require(word(0x844230) == 7 and word(fonts.FONT_MAP) == 4 and word(fonts.TEXT_POOL) != 0 and
                 word(0x6dcf84) == 0 and read(0x6dcf98, 2) == bytes(2) and read(0x6dd4a4, 1) == b"\0",
                 "Global LoadPak requires original PC/font/text prerequisites between gameplay phases")
    initialize_resource_map(system, call, log, metadata)
    table_address, count, stride, digest = probe_native.TABLES[0]
    table = read(table_address, count * stride)
    host.require(hashlib.sha256(table).hexdigest() == digest and
                 struct.unpack_from("<2I", table, 1318 * 8) == (0x6996c8, LOAD_PAK[1]) and
                 read(0x6996c8, 8) == b"LoadPak\0", "Original LoadPak registration provenance differs")
    symbol = call("lookup", (LOAD_PAK_KEY,))
    flags, key, owner, callback = words(symbol, 4)
    host.require((flags >> 16 & 0xff) == 8 and (flags >> 8 & 7) == 1 and
                 key == LOAD_PAK_KEY and owner == 0 and callback == LOAD_PAK[1],
                 "Original published LoadPak callback differs")
    source_pointer = probe_core.reuse(api, process, preloaded, fonts.SOURCE_NAME, source)
    host.require(call("lookup", (fonts.SCRIPT_KEY,)) == source_pointer + 740 and
                 struct.unpack("<4I", read(source_pointer + 740, 16)) ==
                 (0x70400, fonts.SCRIPT_KEY, probe_native.qbkey(fonts.SOURCE_NAME), source_pointer + 760),
                 "Whole permanent-assets source provenance differs")
    cache = word(0x6dcfac)
    cached = call("ScriptCacheLoad", (fonts.SCRIPT_KEY,), cache)
    words(cached, (fonts.BODY_BYTES + 3) // 4)
    body = read(cached, fonts.BODY_BYTES)
    validate_parameters(body)
    baseline = registry()
    manager = 0x842b18
    heap_context = (word(manager + 0xc8), word(manager + 0x10c))
    call("HeapContextPush", (word(manager + 0x128),), manager)
    script = call("ScriptAllocate", (0xfc, 1))
    words(script, 63)
    host.require(call("ScriptCtor", this=script) == script, "Original Global helper Script constructor failed")
    call("ScriptSetSource", (259952684, 0, 0, 0), script)
    call("HeapContextPop", this=manager)
    host.require((word(manager + 0xc8), word(manager + 0x10c)) == heap_context, "Global helper changed original heap context")
    locals_ = word(script + 0x1c)
    words(locals_, 2)
    state_offsets = (0x10, 0x1c, 0xc4, 0xd0, 0xd4)
    helper_state = tuple(word(script + offset) for offset in state_offsets)
    host.require(helper_state[0] != 0 and helper_state[2:] == (0, 259952684, 0),
                 "Original Global helper must remain an unspawned NullScript without host PC/flag writes")
    expected_registry = registry()
    host.require(set(expected_registry) == set(baseline) | {script} and
                 all(expected_registry[key] == value for key, value in baseline.items()),
                 "Global helper replaced an existing owner Script")
    params = system.allocate(bytes(32))
    scratch = params + 16
    host.require(call("StructCtor", this=params) == params and read(params, 8) == bytes(8),
                 "Original Global parameter constructor failed")
    consumed = call("ParseParameters", (params, cached + PARAM_BEGIN, locals_, scratch))
    host.require(consumed == cached + PARAM_END and word(scratch) == 0, "Original Global parser did not consume its entire statement")
    def parameter_read(address, size):
        host.require(address == params or address == params + 4 or start <= address <= end - size,
                     "Original parsed Global string leaves its native arena")
        return read(address, size)

    validate_fields(parameter_read, words, params)
    dictionary = word(0x76b2a8)
    host.require(dictionary >= 0x10000 and word(dictionary) == 0x695500, "Original sprite dictionary is absent")
    name = system.allocate(b"balancemeter\0")
    host.require(call("DictionaryFindTextureName", (name,), dictionary) == 0 and call("FontLookup", (FONT_KEY,)) == 0,
                 "Global witnesses were already present before the authored LoadPak")
    log("global_assets_begin", source=fonts.SOURCE_NAME, source_sha256=fonts.SOURCE_SHA,
        authored_parameters=[PARAM_BEGIN, PARAM_END], parameter_sha256=PARAM_SHA,
        whole_archives=inputs["files"], entry_pin_scope="protected dispatch; complete stock image retained",
        host_source_writes=False, host_resource_objects_created=0)
    host.require(call("LoadPak", (params, script)) & 0xff, "Original stock Global LoadPak returned false")
    call("async_file_wait_all", (1,))
    host.require(tuple(word(script + offset) for offset in state_offsets) == helper_state and
                 registry() == expected_registry and word(0x6dcf84) == 0,
                 "Original Global LoadPak changed helper PC/flags or another Script identity")
    texture = call("DictionaryFindTextureName", (name,), dictionary)
    texture_words = words(texture, 6)
    host.require(texture_words[0] == 0x695430 and texture_words[1] == BALANCE_KEY and texture_words[4] != 0,
                 "Original Global balance texture was not published")
    font = call("FontLookup", (FONT_KEY,))
    font_words = words(font, 3)
    host.require(font_words[0] == 0x694db4 and font_words[2] != 0, "Original Global testtitle font was not published")
    font_data = words(font_words[2], 0x17c // 4)
    font_texture = words(font_data[0x178 // 4], 0x2c // 4)
    host.require(font_texture[0x14 // 4] >= 0x10000, "Original Global font has no native GPU texture")
    call("ScriptDtor", this=script)
    call("ScriptFree", (script,))
    call("ScriptCacheReleaseReference", (fonts.SCRIPT_KEY,), cache)
    host.require(registry() == baseline and word(0x6dcf84) == 0, "Global helper cleanup changed baseline Script identities")
    log("global_assets_pass", filename=FILENAME[:-1].decode("ascii"), original_parameter_parser=True,
        original_load_pak_dispatch=True, original_source_updates=0, script_registry_restored=True,
        balance_texture=texture, balance_texture_key=BALANCE_KEY, testtitle_font=font,
        font_gpu_texture=font_texture[0x14 // 4], source_sha256=fonts.SOURCE_SHA,
        whole_archives=inputs["files"], resources_and_parameters_retained_until_child_exit=True,
        host_source_writes=False, host_resource_objects_created=0, gta_hud_published=False, backend_ready=False)
    return {"texture": texture, "font": font}
