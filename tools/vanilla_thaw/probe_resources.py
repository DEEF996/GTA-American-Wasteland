"""Bounded stock file/texture/decal/cache bootstrap on the retained worker.

The original factories own their objects. Our platform glue supplies an actual
hidden Win32 window and system D3D9 device; no fake renderer or game callback.
Resources remain child-owned until the parent's bounded process termination.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

import probe_loader as host
from native_worker import retained_worker

METADATA_SHA = "f36b398b97c737b0a0d7793a0e013a03565066b0ed7ea00c1700760dc059dffc"


def read_metadata():
    data = Path(__file__).with_name("resource_bootstrap.json").read_bytes()
    host.require(hashlib.sha256(data).hexdigest() == METADATA_SHA, "Resource proof metadata differs")
    value = json.loads(data)
    host.require(value["format"] == "stock-resource-proof-pins-v1" and
                 value["stock_exe_sha256"] == host.FILES["THAW.exe"][1] and
                 len(value["functions"]) == 31 and len(value["files"]) == 3 and
                 len(value["qb_sources"]) == 2, "Resource metadata shape differs")
    return value


def selected_files(rows, directory, metadata):
    host.require(len(rows) >= 3 and len({row["path"] for row in rows}) == len(rows),
                 "Resource cache requires unique entries including the three pinned files")
    result = []
    for expected in metadata["files"]:
        relative = expected["relative_path"]
        matches = [row for row in rows if row["path"] == relative]
        host.require(len(matches) == 1 and all(matches[0][key] == expected[key] for key in ("bytes", "sha256")), "Resource source pin differs or is duplicated")
        path = (directory / relative).resolve()
        host.require(path.is_relative_to(directory), "Resource source escapes private cache")
        result.append((path, expected))
    return result


def read_inputs(manifest_path, qb_manifest, image_manifest):
    host.require(manifest_path.is_absolute() and qb_manifest.is_absolute(), "Resource/cache manifests must be absolute")
    metadata = read_metadata()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    image = json.loads(image_manifest.read_text(encoding="utf-8-sig"))
    qb = json.loads(qb_manifest.read_text(encoding="utf-8-sig"))
    directory = manifest_path.resolve().parent
    original = Path(image["source"]).resolve()
    host.require(manifest.get("format") == "gta-thaw-private-stock-resources-v1" and
                 Path(manifest["directory"]).resolve() == directory and
                 Path(manifest["source"]).resolve() == original and directory != original,
                 "Resource cache directory or original source differs")
    host.require(manifest.get("sources_revalidated_before_publish") is True and
                 manifest.get("original_files_modified") is False,
                 "Resource source verification is absent")
    files = {}
    for path, expected in selected_files(manifest["files"], directory, metadata):
        host.require(not path.samefile(original / expected["relative_path"]), "Resource cache must not alias the original")
        contents = path.read_bytes()
        host.require(len(contents) == expected["bytes"] and hashlib.sha256(contents).hexdigest() == expected["sha256"], "Original resource input differs")
        files[expected["relative_path"]] = contents
    host.require(qb.get("format") == "stock-thaw-core-qb-cache-v1" and
                 Path(qb["source_directory"]).resolve() == original and
                 qb.get("sources_revalidated_before_publish") is True,
                 "QB cache does not match resource source")
    sources = []
    for expected in metadata["qb_sources"]:
        rows = [row for row in qb["members"] if row["order"] == expected["order"]]
        host.require(len(rows) == 1 and all(rows[0][key] == expected[key] for key in expected), "Whole resource QB pin differs")
        path = (qb_manifest.resolve().parent / expected["cache_file"]).resolve()
        host.require(path.is_relative_to(qb_manifest.resolve().parent), "Resource QB escapes private cache")
        contents = path.read_bytes()
        host.require(len(contents) == expected["bytes"] and hashlib.sha256(contents).hexdigest() == expected["sha256"], "Whole resource QB contents differ")
        sources.append((contents, expected["canonical_name"]))
    host.require(str(directory).isascii(), "Original ANSI metadata loader requires an ASCII private directory")
    return {"metadata": metadata, "directory": directory, "files": files, "sources": sources}


def platform_stub(function, arguments, record):
    """Owned stdcall glue for real Win32 exports and real COM methods only."""
    host.require(0x10000 <= function < 0x100000000 and len(arguments) <= 12,
                 "Unexpected system ABI call")
    pack = lambda value: struct.pack("<I", value)
    code = bytearray(b"\xc7\x05" + pack(record) + pack(2))
    for value in reversed(arguments):
        code.extend(b"\x68" + pack(value))
    code.extend(b"\xb8" + pack(function) + b"\xff\xd0")
    code.extend(b"\xa3" + pack(record + 68))
    code.extend(b"\xc7\x05" + pack(record) + pack(6) + host.STUB)
    return bytes(code)


class Platform:
    def __init__(self, api, process, record, deadline, log, modules):
        self.api, self.process, self.record = api, process, record
        self.deadline, self.log, self.modules = deadline, log, modules
        self.worker = retained_worker(api, process)
        self.exports = {}
        self.scratch = self.allocate(bytes(256))

    def read(self, address, size):
        return host.remote_read(self.api, self.process, address, size)

    def word(self, address):
        return struct.unpack("<I", self.read(address, 4))[0]

    def write(self, address, contents):
        host.remote_write(self.api, self.process, address, contents)

    def allocate(self, contents):
        address = host.success(self.api.VirtualAllocEx(self.process, None, max(4096, len(contents)), 0x3000, 4), "Allocate owned platform input")
        host.require(address < 0x100000000, "Owned platform input exceeds x86 space")
        self.write(address, contents)
        return address

    def invoke(self, label, function, arguments=()):
        self.worker.check_call_access()
        timing = {} if host.PROFILE_RESOURCE_CALLS else None
        started = time.perf_counter() if timing is not None else None
        code = platform_stub(function, arguments, self.record)
        address = self.worker.prepare_command(code)
        self.write(self.record + 36, bytes(36))
        self.log("before_platform_call", function=label, address=function, arguments=arguments, worker_tid=self.worker.tid)
        if timing is not None:
            executing = time.perf_counter()
            timing['prepare_ms'] = (executing-started)*1000
            waited, returned = self.worker.execute(address, self.deadline, timings=timing)
            completed = time.perf_counter()
            timing['execute_ms'] = (completed-executing)*1000
        else:
            waited, returned = self.worker.execute(address, self.deadline)
        values = struct.unpack("<18I", self.read(self.record, 72))
        self.log("platform_call_result", function=label, wait_result=waited, return_value=values[17],
                 exception_code=values[9], eip=values[10], worker_tid=self.worker.tid)
        host.require(waited == 0 and returned == 0 and values[0] == 6,
                     f"System call {label} failed: wait={waited} status={values[0]} EIP={values[10]:#x}")
        host.require(self.read(address, len(code)) == code, "Owned platform glue changed")
        if timing is not None:
            finished = time.perf_counter()
            timing['post_guard_ms'] = (finished-completed)*1000
            timing['total_ms'] = (finished-started)*1000
            if timing['total_ms'] > 8:
                self.log('native_slow_platform_call', function=label, worker_tid=self.worker.tid, **timing)
        return values[17]

    def export(self, module, name):
        key = module, name
        if key not in self.exports:
            filename = self.allocate(name.encode("ascii") + b"\0")
            value = self.invoke("GetProcAddress:" + name, self.word(0x68e0f4), (self.modules[module]["base"], filename))
            host.require(value >= 0x10000, "Actual system export is absent: " + name)
            self.exports[key] = value
        return self.exports[key]

    def call(self, module, name, arguments=()):
        return self.invoke(name, self.export(module, name), arguments)

    def com(self, label, object_address, slot, arguments=()):
        host.require(object_address >= 0x10000 and object_address % 4 == 0 and 0 <= slot <= 119, "Invalid genuine COM object/slot")
        table = self.word(object_address)
        host.require(table >= 0x10000 and table % 4 == 0, "Genuine COM vtable is absent")
        return self.invoke(label, self.word(table + slot * 4), (object_address, *arguments))


def create_device(system):
    user, kernel = "user32.dll", "kernel32.dll"
    name = system.allocate("GTA_THAW_StockResourceProbe\0".encode("utf-16le"))
    procedure = system.export(user, "DefWindowProcW")
    # WNDCLASSW32: 40 bytes, instance is the actual original image module.
    window_class = system.allocate(struct.pack("<10I", 0, procedure, 0, 0, 0x400000, 0, 0, 0, 0, name))
    atom = system.call(user, "RegisterClassW", (window_class,))
    host.require(atom & 0xffff, "Hidden resource window class registration failed")
    window = system.call(user, "CreateWindowExW", (0, name, name, 0x80000000, 0, 0, 16, 16, 0, 0, 0x400000, 0))
    host.require(window != 0 and system.call(user, "IsWindowVisible", (window,)) == 0, "Resource window is absent or visible")
    system.write(system.scratch, bytes(4))
    owner = system.call(user, "GetWindowThreadProcessId", (window, system.scratch))
    host.require(owner == system.worker.tid, "Hidden window belongs to a different native thread")
    library_name = system.allocate("d3d9.dll\0".encode("utf-16le"))
    library = system.call(kernel, "LoadLibraryExW", (library_name, 0, 0x800))
    host.require(library >= 0x10000, "Cannot load genuine system D3D9")
    system.modules["d3d9.dll"] = {"base": library}
    d3d = system.call("d3d9.dll", "Direct3DCreate9", (32,))
    host.require(d3d >= 0x10000, "Genuine Direct3DCreate9 failed")
    presentation = system.allocate(struct.pack("<14I", 16, 16, 0, 1, 0, 0, 1, window, 1, 0, 0, 0, 0, 0x80000000))
    system.write(system.scratch, bytes(4))
    result = system.com("IDirect3D9::CreateDevice", d3d, 16, (0, 1, window, 0x26, presentation, system.scratch))
    device = system.word(system.scratch)
    host.require(result < 0x80000000 and device >= 0x10000, f"Genuine HAL device failed: {result:#x}")
    host.require(system.word(0x7ecd04) == 0, "Original device slot was already occupied")
    system.write(0x7ecd04, struct.pack("<I", device))
    system.log("resource_device_created", window=window, owner_tid=owner, direct3d=d3d, device=device,
               dimensions=[16, 16], behavior_flags=0x26, visible=False,
               original_renderer_started=False, system_d3d9=True, retained_until_child_exit=True)
    system.window = window
    return device


def gpu_pixels(system, texture, width, expected, arena_start, arena_end):
    def words(address, count):
        host.require(address % 4 == 0 and arena_start <= address and address + count * 4 <= arena_end, "Native texture object escapes original arena")
        return struct.unpack("<" + "I" * count, system.read(address, count * 4))

    obj = words(texture, 6)
    host.require(obj[0] == 0x695430, "Original TextureRAMFactory class differs")
    data = words(obj[4], 11)
    gpu = data[5]
    system.write(system.scratch, bytes(64))
    result = system.com("IDirect3DTexture9::GetLevelDesc", gpu, 17, (0, system.scratch))
    desc = struct.unpack("<8I", system.read(system.scratch, 32))
    levels = system.com("IDirect3DTexture9::GetLevelCount", gpu, 13)
    host.require(result < 0x80000000 and desc[0] == 0x35545844 and desc[3] == 1 and desc[6:] == (width, width) and levels == 1,
                 "Original GPU texture description differs from the pinned DXT5 source")
    system.write(system.scratch, bytes(8))
    result = system.com("IDirect3DTexture9::LockRect", gpu, 19, (0, system.scratch, 0, 0x10))
    host.require(result < 0x80000000, "Cannot read genuine uploaded GPU texture")
    try:
        pitch, pixels = struct.unpack("<iI", system.read(system.scratch, 8))
        host.require(width * 4 <= pitch <= 0x100000 and pixels >= 0x10000 and len(expected) == width * width, "Unexpected DXT5 lock extent")
        actual = b"".join(system.read(pixels + row * pitch, width * 4) for row in range(width // 4))
    finally:
        unlocked = system.com("IDirect3DTexture9::UnlockRect", gpu, 20, (0,))
    host.require(unlocked < 0x80000000 and actual == expected, "Original GPU pixels differ from original source")
    system.log("resource_texture_pixels", native_texture=texture, gpu_texture=gpu, width=width, height=width,
               format="DXT5", levels=levels, pixels_bit_identical=True,
               source_pixels_sha256=hashlib.sha256(expected).hexdigest(), actual_gpu_pixels_sha256=hashlib.sha256(actual).hexdigest())


def metadata_ids(path):
    # Use the real Win32 conversion, including the source's oversized hex value.
    getter = C.WinDLL("kernel32", use_last_error=True).GetPrivateProfileIntA
    getter.argtypes, getter.restype = [C.c_char_p, C.c_char_p, C.c_int, C.c_char_p], W.UINT
    filename = str(path).encode("ascii")
    keys = {getter(f"ENTRY_{index}".encode("ascii"), b"TEXTURE_0", 0, filename) for index in range(2048)} - {0}
    host.require(len(keys) == 139, "Original metadata source must contain 139 Win32-decoded unique IDs")
    return keys


def verify_metadata(system, address, expected):
    heap = system.word(0x84f5c4)

    def words(pointer, count):
        host.require(pointer >= 0x10000 and pointer % 4 == 0 and 0 < count <= 0x10000, "Native metadata pointer/extent is invalid")
        size = system.call("kernel32.dll", "HeapSize", (heap, 0, pointer))
        host.require(size != 0xffffffff and size >= count * 4, "Metadata is outside its original CRT allocation")
        return struct.unpack("<" + "I" * count, system.read(pointer, count * 4))

    context = words(address, 25)
    host.require(context[21] == 6 and all(context[offset // 4] == 0 for offset in (0x38, 0x40, 0x44, 0x48, 0x58, 0x5c)), "Metadata constructor left an active movie source/handle")
    sentinel, count = context[2:4]
    host.require(count == 139, "Metadata node count differs")
    root = words(sentinel, 4)
    pointer, previous = root[0], sentinel
    nodes, keys = set(), set()
    while pointer != sentinel:
        host.require(len(nodes) < count and pointer not in nodes, "Metadata node chain is cyclic or oversized")
        node = words(pointer, 4)
        host.require(node[1] == previous and node[3] == 0 and node[2] in expected and node[2] not in keys, "Original metadata link/key/state differs")
        nodes.add(pointer)
        keys.add(node[2])
        previous, pointer = pointer, node[0]
    host.require(root[1] == previous and keys == expected, "Original metadata source IDs or final link differ")
    begin, end, capacity = context[5:8]
    host.require(begin % 4 == 0 and begin < end <= capacity and (capacity - begin) % 4 == 0, "Metadata bucket vector extent differs")
    buckets = words(begin, (capacity - begin) // 4)
    host.require(all(value == sentinel or value in nodes for value in buckets[:(end - begin) // 4]), "Metadata buckets reference foreign nodes")
    system.log("resource_metadata_verified", native_context=address, original_source_unique_ids=len(keys),
               source_ids_from_real_win32_ini=True, crt_heap_ownership_checked=True,
               native_map_lookup_invoked=False, native_init_result=context[21], movie_handles=0)


def run(api, process, record, deadline, log, inputs, modules, preloaded=None):
    metadata, directory = inputs["metadata"], inputs["directory"]
    pins = {row[0]: row for row in metadata["functions"]}
    globals_ = metadata["globals"]
    system = Platform(api, process, record, deadline, log, modules)
    read, word = system.read, system.word

    def call(name, arguments=(), this=None):
        pin = pins.get(name) or host.QB_PINS[name]
        return host.native_call(api, process, pin, list(arguments), record, deadline, log, this)

    for name, address, size, digest in metadata["functions"]:
        host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, "Original resource function differs: " + name)
    crt = metadata["crt_table"]
    host.require(hashlib.sha256(read(crt["va"], crt["bytes"])).hexdigest() == crt["sha256"], "Original CRT initializer pointer table differs")
    for address, index in metadata["crt_indices"]:
        host.require(word(crt["va"] + index * 4) == address, "Resource initializer provenance differs")
    for address, size, digest in ((0x695430, 112, "e5099186bff144e83386c923bf694c8ee6f522eba2805c97e5a71c700656f34c"),
                                  (0x695500, 36, "9bc00130569be1ea0f4f1319de66ef87af3275ae5d181d40cef871f5120d7931")):
        host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, "Original texture class vtable differs")
    zero_slots = ("device", "sprite_dictionary", "sprite_alias", "metadata", "observer_queue",
                  "asset_manager", "asset_manager_refs", "file_worker_handle", "decal_primary", "decal_secondary")
    host.require(all(word(globals_[key]) == 0 for key in zero_slots), "Original resource services must initially be absent")
    working_directory = system.allocate((str(directory) + "\0").encode("utf-16le"))
    host.require(system.call("kernel32.dll", "SetCurrentDirectoryW", (working_directory,)) != 0, "Cannot select private resource working directory")
    call("async_file_bootstrap")
    call("async_file_wait_all", (1,))
    host.require(word(globals_["file_worker_handle"]) != 0 and word(globals_["file_worker_id"]) != 0 and
                 all(word(globals_[name]) != 0 for name in ("file_request_event", "file_complete_event")) and
                 all(word(globals_[name]) == 0 for name in ("file_active_count", "file_pending_count")),
                 "Original async FileService did not initialize idle")
    log("resource_file_service", worker_tid=word(globals_["file_worker_id"]), owned_native_worker=True, idle=True, retained_until_child_exit=True)
    host.require(word(globals_["crt_exit_begin"]) == 0 and word(globals_["crt_exit_current"]) == 0, "Original CRT exit table already owned")
    host.require(call("exit_table_initialize") == 0 and word(globals_["crt_exit_begin"]) >= 0x10000, "Original CRT exit table initialization failed")
    create_device(system)
    for name in ("RendererCriticalSectionCRTInitializer", "TextureRegistryCRT", "SpriteDictionaryCRT", "SpriteAliasCRT"):
        call(name)
    dictionary = word(globals_["sprite_dictionary"])
    host.require(dictionary >= 0x10000 and word(globals_["sprite_alias"]) == dictionary and word(dictionary) == globals_["sprite_vtable"], "Original sprite dictionary/alias/class differs")
    call("TextureObserverQueueSingleton")
    context = call("TextureMetadataSingleton")
    host.require(word(globals_["observer_queue"]) != 0 and context >= 0x10000 and word(globals_["metadata"]) == context, "Original metadata singleton was not published")
    expected = metadata_ids(directory / "data/streams/bik/mad.bik")
    verify_metadata(system, context, expected)
    arena_start, arena_end = word(0x84376c), word(0x843770)
    for filename, key, width, basename in (("data/images/tags/cas_01.img.wpc", 0xf4fd2d63, 128, b"tags\\cas_01\0"),
                                           ("data/images/tags/sticker.img.wpc", 0x70124302, 64, b"tags\\sticker\0")):
        source = inputs["files"][filename]
        owned = system.allocate(source)
        texture = call("DictionaryLoadTextureRAM", (owned, len(source), key, 1, 1, 0, 1), this=dictionary)
        host.require(arena_start <= texture < arena_end - 24 and word(texture + 4) == key, "Native texture identity differs")
        name = system.allocate(basename)
        host.require(call("DictionaryFindTextureName", (name,), this=dictionary) == texture, "Original Tagging name lookup does not resolve loaded texture")
        gpu_pixels(system, texture, width, source[28:], arena_start, arena_end)
        host.require(read(owned, len(source)) == source, "Original texture loader modified borrowed input")
    for source, filename in inputs["sources"]:
        if preloaded is not None:
            import probe_core
            probe_core.reuse(api, process, preloaded, filename, source)
            log("resource_qb_reused", filename=filename, bytes=len(source), sha256=hashlib.sha256(source).hexdigest())
            continue
        owned = system.allocate(source)
        name = system.allocate(filename.encode("ascii") + b"\0")
        call("load", (name, owned, 0))
        host.require(read(owned, 1) == b"\1", "Original resource QB source was not processed")
        log("resource_qb_loaded", filename=filename, bytes=len(source), sha256=hashlib.sha256(source).hexdigest())
    host.require(call("OriginalGlobalGetArray", (0xda967860, 0)) >= 0x10000, "Original splatignoreflags source is absent")
    call("DecalInitializeFromWholeSource")
    for key in ("decal_primary", "decal_secondary"):
        table = word(globals_[key])
        host.require(arena_start <= table <= arena_end - 28, "Original decal table escapes its arena")
        bits, entries, count = struct.unpack("<3I", read(table, 12))
        host.require(bits == 4 and count == 0 and arena_start <= entries <= arena_end - 192 and not any(read(entries, 192)), "Original decal map must be an empty4-bit table")
    host.require(word(globals_["decal_flags"]) == 0x3010, "Original decal flags differ from actual source")
    for name in ("VertexBufferMapCRT", "IndexBufferMapCRT", "PixelShaderCacheCRT", "VertexShaderCacheCRT"):
        call(name)
    manager = call("assetmanager_factory", (1,))
    host.require(arena_start <= manager <= arena_end - 0xe10 and word(globals_["asset_manager"]) == manager and word(globals_["asset_manager_refs"]) == 1,
                 "Original AssetsManager factory failed")
    slots = struct.unpack("<900I", read(manager, 0xe10))
    host.require(not any(slots[:0x600 // 4]) and slots[0x600 // 4] == 0xffffffff and
                 not any(slots[0x604 // 4:0xe04 // 4]) and slots[0xe08 // 4] == 0xffffffff and
                 slots[0xe0c // 4] == 0, "Original AssetsManager slots/cache are not empty")
    host.require(all(word(address) == 0 for address in (0x6dcf84, 0x6dcf8c, 0x6dcf90)), "Resource bootstrap changed active VM state")
    log("resources_pass", original_file_service=True, original_metadata_ids=139, original_gpu_textures=2,
        original_decal_tables=2, original_decal_flags=word(globals_["decal_flags"]), original_assets_manager=manager,
        original_renderer_started=False, shader_compilation_tested=False, native_resource_destructors_tested=False,
        resources_and_hidden_window_retained_until_child_exit=True, backend_ready=False,
        scope="original file/texture/decal/cache/AssetsManager bootstrap on real system D3D9; no skater or gameplay")
    return system


def self_test():
    metadata = read_metadata()
    directory = Path(__file__).resolve().parent
    rows = [{"path": row["relative_path"], "bytes": row["bytes"], "sha256": row["sha256"]} for row in metadata["files"]]
    host.require(len(selected_files(rows, directory, metadata)) == 3, "Valid resource source selection failed")
    host.require(len(selected_files(rows + [{"path": "data/unused.bin", "bytes": 1, "sha256": "0" * 64}], directory, metadata)) == 3,
                 "Resource cache rejected an unused safe superset entry")
    for invalid in (rows[:-1], rows + [rows[0]], [rows[0], rows[0], rows[2]], [{**rows[0], "bytes": 1}, *rows[1:]], [{**rows[0], "sha256": "0" * 64}, *rows[1:]]):
        try:
            selected_files(invalid, directory, metadata)
        except RuntimeError:
            pass
        else:
            raise RuntimeError("Resource source selection accepted invalid metadata")
    code = platform_stub(0x76001000, list(range(12)), 0x22000000)
    host.require(code.endswith(host.STUB) and b"\x83\xc4" not in code and code.count(b"\x68") == 12, "Platform stdcall ABI differs")
    try:
        platform_stub(0x76001000, list(range(13)), 0x22000000)
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Platform accepted too many arguments")
    print("PASS: resource pins, private source selection and stdcall glue; no child created")


if __name__ == "__main__":
    host.require(sys.argv[1:] == ["--self-test"], "Use probe_loader.py for native execution")
    self_test()
