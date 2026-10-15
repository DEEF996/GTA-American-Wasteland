"""Original animation system and human Skeleton resource for the stock skater.

Uses a complete preloaded stock startup script to check the actual source
arguments, then calls the original initializer and resource RAM loader.
The original context owns resources until the bounded child exits.
"""
import hashlib
from pathlib import Path
import struct

import probe_loader as host
import probe_animation as animation
import probe_native
import probe_profile

STARTUP_NAME = "scripts/game/startup.qb.wpc"
STARTUP_SHA = "907aa1e79e6445a7b47cf9959d7d4a17c323d406f0229be9bb1fb94d7bcc413a"
SCRIPT_KEY = 0xae754239
SKE_KEY = 0x39937d56
SCRATCH_KEY = 0x7c0b34b4
FIELDS = ((0x83dff28a, 1, 0x20000), (0x7aa8c11c, 1, 0x400),
          (0x891a96a9, 13, 0x7adb1481), (0x5f63f886, 1, 2),
          (0xd14e9a11, 1, 4), (0x416bc96b, 1, 3))
PINS = {
    "init": ("InitAnimSystem", 0x47d370, 247, "6f9988cab3f60891e0780ba204fcfedd311794a97bdbfcdb02e9abc246c944ed"),
    "scratch": ("AnimScratchInitializeDispatch", 0x4b9900, 6, "833845890f43d1ae16299a2319ea1f02a93d9bb9b4de371f502c83b91e960f78"),
    "struct_ctor": ("AnimationInputStructCtor", 0x414230, 11, "219b92061b08d719b045423de28a365462f20df22e2ca28f240d235c37052a90"),
    "load_resource": ("AnimationResourceRAM", 0x41aee0, 312, "1dcc0bced54e425a5d7641aa1656f65751faf0ccbda1adec5b7e74f3a1ab0dba"),
    "get_data": ("AnimationResourceGetData", 0x41ac50, 142, "e648e2890630061fc22f79aae2e30279207e19f8ff52f1cfdbaac24debb04524"),
    "get_resource": ("AnimationResourceLookup", 0x41abc0, 143, "557a8479d4685a4d8bfb102f03e972fcf4734364cd73068d0f62e5e5cb34f8c1"),
}


def source_call():
    """Typed source parameters encoded solely for checking the cached call site."""
    call = bytearray(b"\x43" + struct.pack("<I", PINS["init"][1]) + b"\x03\x01")
    for checksum, kind, value in FIELDS:
        call.extend(b"\x16" + struct.pack("<I", checksum) + b"\x07")
        call.extend(bytes([0x17 if kind == 1 else 0x16]) + struct.pack("<I", value) + b"\x01")
    call.extend(b"\x04\x01")
    return bytes(call)


def source_parameters(body):
    expected = source_call()
    host.require(body.count(expected) == 1, "Stock startup does not contain exactly one original PC animation configuration")
    return body.index(expected)


def parameter_storage(base):
    host.require(base % 16 == 0 and 0x10000 <= base <= 0xfffff000, "Invalid original animation parameter allocation")
    data = bytearray(16 + 16 * len(FIELDS))
    for index, (key, kind, value) in enumerate(FIELDS):
        following = base + 16 + 16 * (index + 1) if index + 1 < len(FIELDS) else 0
        struct.pack_into("<4I", data, 16 + 16 * index, kind << 9, key, value, following)
    return bytes(data)


def read_inputs(asset_manifest, image_manifest):
    return animation.read_assets(Path(asset_manifest), Path(image_manifest))


def run(api, process, record, deadline, log, inputs, preloaded):
    def read(address, size):
        return host.remote_read(api, process, address, size)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    def write(address, contents):
        host.remote_write(api, process, address, contents)

    def allocate(contents):
        address = host.success(api.VirtualAllocEx(process, None, max(4096, len(contents)), 0x3000, 4), "Allocate owned animation input")
        host.require(address % 16 == 0 and address < 0xfffff000, "Animation input is outside x86 address space")
        write(address, contents)
        return address

    def call(name, arguments=(), this=None):
        pin = PINS.get(name) or probe_native.PINS.get(name) or probe_profile.PINS.get(name) or host.QB_PINS[name]
        return host.native_call(api, process, pin, list(arguments), record, deadline, log, this)

    for name, address, size, digest in PINS.values():
        host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, "Original animation system entry differs: " + name)
    host.require(STARTUP_NAME in preloaded, "Original startup source must already be loaded")
    source, size = preloaded[STARTUP_NAME]
    host.require(size == 5956 and word(source + 744) == SCRIPT_KEY and word(source + 752) == source + 760,
                 "Preloaded original startup source extent or rebasing differs")
    symbol = call("lookup", (SCRIPT_KEY,))
    host.require(symbol == source + 740, "Original load_permanent_assets symbol ownership differs")
    cache = word(0x6dcfac)
    host.require(cache >= 0x10000 and all(word(address) == 0 for address in (0x6dcf84, 0x6dcf8c, 0x6dcf90)),
                 "Animation startup requires an idle original VM/cache")
    decoded_size = word(source + 764)
    host.require(decoded_size == 1409, "Original startup script decoded extent differs")
    body = call("ScriptCacheLoad", (SCRIPT_KEY,), this=cache)
    host.require(body >= 0x10000, "Original startup cache did not return a body")
    try:
        source_offset = source_parameters(read(body, decoded_size))
    finally:
        call("ScriptCacheReleaseReference", (SCRIPT_KEY,), this=cache)
    manager = 0x842b18
    host.require(word(0x767410) == 0 and call("NamedHeapLookup", (SCRATCH_KEY, 0), this=manager) == 0,
                 "Original animation system is already initialized")
    for address, key in ((0x767438, "data/anims/standardkeyQ.bin"), (0x767c38, "data/anims/standardkeyT.bin")):
        expected = inputs[key]
        host.require(len(expected) == 2048 and not any(read(address, 2048)), "Original compression table is already initialized")
        write(address, expected)
    params = allocate(bytes(112))
    write(params, parameter_storage(params))
    host.require(call("struct_ctor", this=params) == params and read(params, 8) == bytes(8), "Original parameter constructor differs")
    write(params + 4, struct.pack("<I", params + 16))
    host.require(call("init", (params, 0)) & 0xff, "Original animation system initialization failed")
    anim_cache = word(0x767410)
    scratch_heap = call("NamedHeapLookup", (SCRATCH_KEY, 0), this=manager)
    host.require(anim_cache >= 0x10000 and word(anim_cache + 0xdc) == 0x400 and word(anim_cache + 0xe0) == 4 and scratch_heap >= 0x10000,
                 "Original animation cache configuration or scratch heap differs")
    context = word(0x6dd3f4)
    host.require(context >= 0x10000 and call("get_resource", (SKE_KEY, 0), this=context) == 0,
                 "Original human skeleton resource must initially be absent")
    source_ske = inputs["thps7_human.ske"]
    payload = allocate(source_ske)
    info = call("load_resource", (SKE_KEY, 7, 0, payload, len(source_ske), 0, 0, 0, 0), this=context)
    resource = call("get_resource", (SKE_KEY, 0), this=context)
    start, end = word(0x84376c), word(0x843770)
    host.require(start <= resource <= end - 32 and start <= info <= end - 16 and resource % 4 == info % 4 == 0,
                 "Native skeleton resource or info escapes original arena")
    values = struct.unpack("<8I", read(resource, 32))
    info_values = struct.unpack("<4I", read(info, 16))
    host.require(0x68e000 <= values[0] < 0x6bc000 and values[1] == SKE_KEY and values[3] == 0 and
                 values[6] & 0xffffff == len(source_ske) and info_values[0] == 0x694280 and
                 info_values[1] == payload and info_values[3] == SKE_KEY and word(payload + 4) == 52 and
                 call("get_data", (SKE_KEY, 0), this=context) == info,
                 "Original skeleton registration, data lookup or borrowed ownership differs")
    for address, key in ((0x767438, "data/anims/standardkeyQ.bin"), (0x767c38, "data/anims/standardkeyT.bin")):
        host.require(read(address, 2048) == inputs[key], "Native initialization modified original compression tables")
    log("skater_animation_pass", native_initializer=True, source_filename=STARTUP_NAME,
        source_call_cached_offset=source_offset, original_source_arguments=list(FIELDS),
        native_cache=anim_cache, native_scratch_heap=scratch_heap, skeleton_resource=resource,
        skeleton_info=info, skeleton_bones=52, borrowed_payload=True, resources_retained_until_child_exit=True,
        startup_script_executed=False, original_compression_tables=True, gameplay_ready=False)
    return dict(cache=anim_cache, scratch_heap=scratch_heap, resource=resource, info=info, payload=payload)
