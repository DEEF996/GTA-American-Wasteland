"""Original registry publication and one bounded stock VM native dispatch.

No callback replacements or source edits. Registration tables are read from the
whole pinned child image. This is a development proof, not a playable backend.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import sys

import probe_loader as host

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assets_export import qbkey

TABLES = [(0x6d2aa8, 1383, 8, "1329cc020047ae5e01adac6e784bfc777b69584d9068308d820fb573be67fa51"),
          (0x6d55e0, 67, 4, "d81b9944d2a2d774db6343f63e9e6b0c5a982111baa24402d7e788356f21e3bd")]
BINDINGS = [(4, 0x487fb0), (37, 0x47c750)]


# Exact image regions/entry pins; original full EXE remains mandatory.
PINS = {
    "GlobalFunctionCount": ("GlobalFunctionCount", 0x59fcc0, 6, "104fe3fae3d3539d574c589748143cbc7e2bc3c54831bfe38be28a6f936eeff8"),
    "RegisterGlobalFunctions": ("RegisterGlobalFunctions", 0x40d210, 6, "39e98184457324d9a431e2cf147b722ca6f7c7e2924fbb43d1611659ccf90f6b"),
    "MemberFunctionCount": ("MemberFunctionCount", 0x59fcd0, 6, "38d27ae04aebd51874888ab4eaf8728691daf1da1a34be84a5ba3ee57ae026e5"),
    "RegisterMemberFunctions": ("RegisterMemberFunctions", 0x40d260, 6, "2ca060c2dcc84c245d12f6f0bae604797710b304e47300feba761658c3edd233"),
    "IsTrue": ("IsTrue", 0x487fb0, 37, "c8d03545d5b8645e85ef508723e91535337a4fd9217d626bd3bdb77bb4c6f304"),
    "StructGetInteger": ("StructGetInteger", 0x414e10, 69, "d5622d4d944a5b48769ba005e3c3e6184f6ce0bcc553b7139467f898b5ae4ae1"),
    "NativePrintf": ("NativePrintf", 0x47c750, 3, "e18aacce29affcdddaa5f641ac1ec12552e0fe9d066e6408941b1792818b8619"),
    "ScriptCacheLoad": ("ScriptCacheLoad", 0x413c90, 6, "63abad928924425d6bb0379ab145ea18dc5e64c32e5f137ad832dbe065287ef5"),
    "ScriptCacheReleaseReference": ("ScriptCacheReleaseReference", 0x413af0, 6, "ed8f2bb7c9bb4a552946fdeb002a1f05f456854e74f40acd243d534d4b78699b"),
}


def verify_bindings(body):
    host.require(len(body) == 58, "Cached igc_printf extent differs")
    for offset, function in BINDINGS:
        host.require(body[offset] == 0x43 and struct.unpack_from("<I", body, offset + 1)[0] == function, "Original cache did not bind the selected native callback")


def run(api, process, record, deadline, log, source, filename):
    def read(address, size):
        return host.remote_read(api, process, address, size)

    def write(address, data):
        host.remote_write(api, process, address, data)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    def call(name, arguments=(), this=None):
        pin = PINS.get(name) or host.VM_PINS.get(name) or host.QB_PINS[name]
        return host.native_call(api, process, pin, list(arguments), record, deadline, log, this)

    def idle():
        return all(word(address) == 0 for address in (0x6dcf84, 0x6dcf8c, 0x6dcf90))

    host.require(idle(), "Original script registry must be idle")
    host.require(all(word(address) == 0 for address in (0x6dd104, 0x6dd10c, 0x6dd110)), "Original fixed symbol pools must remain inactive")
    host.require(word(0x6dd3f4) != 0 and word(0x6dcfac) != 0, "Original VM bootstrap is absent")
    buckets = word(0x6dd0fc)
    host.require(buckets >= 0x10000 and not any(read(buckets, 0x4000)), "Original QB buckets must be empty before publication")
    expected = {}
    table_contents = {}
    for address, count, stride, digest in TABLES:
        data = read(address, count * stride)
        host.require(hashlib.sha256(data).hexdigest() == digest, "Original native registration table differs")
        table_contents[address] = data
        for index in range(count):
            name_ptr = struct.unpack_from("<I", data, index * stride)[0]
            host.require(0x400000 <= name_ptr < 0x1c4cf00, "Original native name is outside the image")
            text = read(name_ptr, 256).split(b"\0", 1)[0]
            host.require(0 < len(text) < 256, "Original native name is unterminated or empty")
            checksum = qbkey(text.decode("ascii"))  # Metadata check, not game dispatch.
            callback = struct.unpack_from("<I", data, index * stride + 4)[0] if stride == 8 else 0
            host.require(checksum not in expected, "Original registration tables have a checksum collision")
            expected[checksum] = (8 if stride == 8 else 9, callback)
        log("native_table_verified", address=address, count=count, bytes=len(data), sha256=digest)
    host.require(len(expected) == 1450 and expected[qbkey("IsTrue")] == (8, 0x487fb0) and expected[qbkey("Printf")] == (8, 0x47c750), "Original selected callbacks differ")
    callback_before = {}
    for name in ("IsTrue", "StructGetInteger", "NativePrintf"):
        _, address, size, digest = PINS[name]
        data = read(address, size)
        host.require(hashlib.sha256(data).hexdigest() == digest, f"Original {name} body differs")
        callback_before[name] = data
    host.require(call("GlobalFunctionCount") == 1383, "Original CFunction count differs")
    call("RegisterGlobalFunctions", (0x6d2aa8, 1383))
    host.require(call("MemberFunctionCount") == 67, "Original member-name count differs")
    call("RegisterMemberFunctions", (0x6d55e0, 67))
    baseline = read(buckets, 0x4000)
    arena_start, arena_end = word(0x84376c), word(0x843770)
    published = {}
    seen_checksums = set()
    for pointer in struct.unpack("<4096I", baseline):
        while pointer:
            host.require(len(published) < 1450 and pointer not in published and pointer % 4 == 0 and arena_start <= pointer and pointer + 20 <= arena_end, "Original registry has an invalid, duplicate or cyclic symbol")
            data = read(pointer, 20)
            flags, checksum, owner, value, following = struct.unpack("<5I", data)
            host.require(checksum in expected and checksum not in seen_checksums, "Original registry contains a missing/duplicate name")
            kind, callback = expected[checksum]
            host.require((flags >> 16 & 0xff) == kind and (flags >> 8 & 7) == 1 and owner == 0 and value == callback, "Native symbol type, ownership or callback differs")
            published[pointer] = data
            seen_checksums.add(checksum)
            pointer = following
    host.require(len(published) == 1450 and seen_checksums == set(expected), "Original registrars did not publish both complete tables")
    log("native_registry_published", global_functions=1383, member_names=67, symbols_verified=len(published), host_symbol_table_writes=False, replacement_callbacks=0)
    name_offset = (len(source) + 15) & ~15
    encoded = filename.encode("ascii") + b"\0"
    owned = host.success(api.VirtualAllocEx(process, None, name_offset + len(encoded), 0x3000, 4), "Allocate owned igc source")
    write(owned, source)
    write(owned + name_offset, encoded)
    source_crc = call("crc", (owned + name_offset,))
    host.require(source_crc == qbkey(filename), "Original igc filename checksum differs")
    host.require(call("lookup", (0xe1a9f20a,)) == 0 and call("lookup", (0xde1929ec,)) == 0, "Selected original igc symbols already exist")
    call("load", (owned + name_offset, owned, 0))
    symbol, debug = call("lookup", (0xe1a9f20a,)), call("lookup", (0xde1929ec,))
    host.require(symbol == owned + 464 and debug == owned + 28, "Original igc source addresses differ")
    host.require(struct.unpack("<4I", read(symbol, 16)) == (0x70400, 0xe1a9f20a, source_crc, owned + 484), "Original igc_printf symbol differs")
    host.require(struct.unpack("<4I", read(debug, 16)) == (0x10400, 0xde1929ec, source_crc, 0), "Original igc_debug must remain its unmodified integer zero")
    host.require(struct.unpack("<3I", read(owned + 484, 12)) == (0x0d56bcf7, 58, 58), "Original igc stored header differs")
    host.require(hashlib.sha256(read(owned + 496, 58)).hexdigest() == "ec8c00face53d2cbaf4ff347b692f401dbffa8a00676247959a01c8573ab704e", "Original igc source body changed before caching")
    cache = word(0x6dcfac)
    cached_base = call("ScriptCacheLoad", (0xe1a9f20a,), this=cache)
    host.require(cached_base >= 0x10000 and cached_base % 4 == 0, "Original cache provided no aligned script body")
    cached = read(cached_base, 58)
    verify_bindings(cached)
    log("native_cache_bound", script_crc=0xe1a9f20a, cached_body=cached_base, cached_sha256=hashlib.sha256(cached).hexdigest(), native_token=0x43, IsTrue=0x487fb0, Printf=0x47c750, host_token_writes=False)
    call("HeapContextPush", (word(0x842b18 + 0x128),), this=0x842b18)
    script = call("ScriptAllocate", (0xfc, 1))
    host.require(script >= 0x10000 and call("ScriptCtor", this=script) == script, "Original CScript construction failed")
    host.require(word(0x6dcf8c) == script and word(0x6dcf90) == 1, "Original CScript registry differs")
    call("ScriptSetSource", (0xe1a9f20a, 0, 0, 0), this=script)
    call("HeapContextPop", this=0x842b18)
    pc = word(script + 0x10)
    host.require(cached_base <= pc < cached_base + 58 and word(script + 0xd0) == 0xe1a9f20a, "Original CScript did not bind the verified cached source")
    result = call("ScriptUpdate", this=script)
    host.require(result == 0 and word(script + 0x10) == 0 and word(0x6dcf84) == 0 and word(debug + 12) == 0, "Original native-dispatch script did not finish with its unchanged false input")
    log("native_script_executed", script="igc_printf", updates=1, result=result, IsTrue_input=0, igc_debug_after=word(debug + 12), original_native_callback_bound=True, member_methods_executed=False)
    call("ScriptDtor", this=script)
    call("ScriptFree", (script,))
    host.require(idle(), "Original script destruction did not restore idle registry")
    call("ScriptCacheReleaseReference", (0xe1a9f20a,), this=cache)
    call("unload", (source_crc,))
    host.require(call("lookup", (0xe1a9f20a,)) == 0 and call("lookup", (0xde1929ec,)) == 0, "Original source unload left igc symbols")
    host.require(read(buckets, 0x4000) == baseline and all(read(pointer, 20) == data for pointer, data in published.items()), "Original source unload did not restore the registry-only graph")
    for address, data in table_contents.items():
        host.require(read(address, len(data)) == data, "Original registration source table changed")
    for name, before in callback_before.items():
        _, address, size, _ = PINS[name]
        after = read(address, size)
        log("native_callback_after", function=name, sha256=hashlib.sha256(after).hexdigest(), changed_bytes=sum(a != b for a, b in zip(before, after)))
    log("native_dispatch_pass", original_global_functions=1383, original_member_names=67, original_script_updates=1, registry_symbols_restored=1450, registry_retained_until_child_exit=True, backend_ready=False, scope="original tables published and one IsTrue dispatch through original VM; no member-method execution or skater")


def self_test():
    selections = [host.qb_fixture(), host.qb_fixture(script=True), host.qb_fixture(native=True)]
    host.require([(row[0], row[1]) for row in selections] == [(214, 48), (89, 25908), (175, 15124)], "Scalar, VM and native fixtures are not independent")
    for arguments in ({"native": {}}, {"script": {}, "native": False}, {"script": True, "native": True}):
        try:
            host.qb_fixture(**arguments)
        except RuntimeError:
            continue
        raise RuntimeError("Fixture selector accepted a manifest as a mode or ambiguous selection")
    try:
        host.probe_qb(None, None, None, None, None, bytes(15124), "scripts/game/igc/igc_player.qb.wpc")
    except RuntimeError as error:
        host.require("48-byte fixture" in str(error), "Scalar guard did not run before remote access")
    else:
        raise RuntimeError("Scalar probe accepted an oversized native fixture")
    fixture = bytearray(58)
    for offset, callback in BINDINGS:
        fixture[offset] = 0x43
        struct.pack_into("<I", fixture, offset + 1, callback)
    verify_bindings(fixture)
    failures = [fixture[:-1], fixture + b"\0"]
    for offset, _ in BINDINGS:
        for byte in (offset, offset + 1):
            invalid = bytearray(fixture)
            invalid[byte] ^= 1
            failures.append(invalid)
    for invalid in failures:
        try:
            verify_bindings(invalid)
        except RuntimeError:
            continue
        raise RuntimeError("Native binding validator accepted a malformed test buffer")
    host.require(qbkey("IsTrue") == 0x030c0940 and qbkey("igc_printf") == 0xe1a9f20a, "Metadata checksum utility differs")
    host.require(qbkey("scripts/game/igc/igc_player.qb.wpc") == qbkey(r"scripts\game\igc\igc_player.qb.wpc") == 0xf3d6fd11, "Original filename slash normalization differs")
    print("PASS: independent fixture selection, scalar bounds, valid bindings and six malformed controls; no child created")


if __name__ == "__main__":
    host.require(sys.argv[1:] == ["--self-test"], "Use probe_loader.py --native for the bounded native test")
    self_test()
