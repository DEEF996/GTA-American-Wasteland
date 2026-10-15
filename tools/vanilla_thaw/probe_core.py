"""Publish the complete pinned stock core QB pair through its original loader.

Source bytes remain private external inputs. Static section candidates are
checked against the actual native symbol graph; they do not parse or run scripts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

import import_qb
import probe_loader as host


def sections(source):
    host.require(len(source) >= 28 and len(source) % 4 == 0 and
                 struct.unpack_from("<I", source, 4)[0] == len(source), "Original whole QB header differs")
    if len(source) == 28:
        return []
    host.require(len(source) >= 48, "Original nonempty QB section is truncated")
    original_owner = struct.unpack_from("<I", source, 36)[0]
    result = []
    for offset in range(28, len(source) - 19, 4):
        kind, key, owner, value, next_ = struct.unpack_from("<5I", source, offset)
        if kind & 0xffff != 0x400 or owner != original_owner or next_ or kind >> 16 not in (1, 2, 3, 4, 5, 6, 7, 10, 12, 13, 26, 27, 28):
            continue
        if kind >> 16 in (3, 4, 5, 6, 7, 10, 12) and not offset + 20 <= value < len(source):
            continue
        result.append((offset, kind, key, value))
    host.require(result and result[0][0] == 28, "Original nonempty QB lacks its first source section")
    return result


def read_inputs(qb_manifest, image_manifest):
    host.require(qb_manifest.is_absolute() and image_manifest.is_absolute(), "Core input manifests must be absolute")
    qb = json.loads(qb_manifest.read_text(encoding="utf-8-sig"))
    image = json.loads(image_manifest.read_text(encoding="utf-8-sig"))
    original = Path(image["source"]).resolve()
    directory = qb_manifest.resolve().parent
    host.require(qb.get("format") == import_qb.FORMAT and qb.get("member_count") == 268 and
                 qb.get("sources_revalidated_before_publish") is True and
                 Path(qb["source_directory"]).resolve() == original and not directory.is_relative_to(original),
                 "Full core cache must derive from the selected original source")
    pair = []
    for name in ("data/pak/qb.pak.wpc", "data/pak/qb.pab.wpc"):
        contents = (original / name).read_bytes()
        host.require((len(contents), hashlib.sha256(contents).hexdigest()) == import_qb.PINNED_INPUTS[name], "Selected original core archive changed")
        pair.append(contents)
    expected, _ = import_qb.parse_archive(*pair)
    host.require(len(expected) == len(qb["members"]) == 268, "Complete original core member count differs")
    inputs, keys = [], set()
    for actual, row in zip(qb["members"], expected):
        host.require(all(actual.get(key) == value for key, value in row.items() if key != "payload"), "Core cache member order or archive metadata differs")
        path = (directory / row["cache_file"]).resolve()
        host.require(path.is_relative_to(directory), "Core source escapes private cache")
        source = path.read_bytes()
        host.require(source == row["payload"], "Core cache bytes differ from the pinned original archive")
        candidates = sections(source)
        for _, _, key, _ in candidates:
            host.require(key not in keys, "Complete stock core unexpectedly duplicates a symbol")
            keys.add(key)
        inputs.append({"source": source, "name": row["canonical_name"], "sha256": row["sha256"], "sections": candidates})
    host.require(len(keys) == 11212 and sum(not row["sections"] for row in inputs) == 9, "Pinned original core section census differs")
    return inputs


def reuse(api, process, preloaded, filename, source):
    host.require(filename in preloaded, "Requested source is absent from the complete original core")
    pointer, size = preloaded[filename]
    host.require(size == len(source) and pointer >= 0x10000 and pointer % 16 == 0 and
                 host.remote_read(api, process, pointer, 1) == b"\1", "Already loaded whole original source extent/state differs")
    return pointer


def run(api, process, record, deadline, log, inputs):
    def read(address, size):
        return host.remote_read(api, process, address, size)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    def graph(limit, buffers=()):
        result, visited = {}, set()
        start, end = word(0x84376c), word(0x843770)
        for pointer in struct.unpack("<4096I", read(word(0x6dd0fc), 0x4000)):
            while pointer:
                host.require(pointer % 4 == 0 and (start <= pointer <= end - 20 or
                             any(base <= pointer <= base + size - 20 for base, size in buffers)) and
                             pointer not in visited and len(visited) < limit, "Original core symbol graph escapes owned memory or cycles")
                visited.add(pointer)
                values = struct.unpack("<5I", read(pointer, 20))
                host.require(values[1] not in result, "Original core graph has duplicate checksums")
                result[values[1]] = (pointer, values)
                pointer = values[4]
        host.require(len(result) == limit, "Original core native publication count differs")
        return result

    host.require(len(inputs) == 268 and all(word(a) == 0 for a in (0x6dcf84, 0x6dcf8c, 0x6dcf90)), "Complete core requires an idle original VM")
    baseline = graph(2292)
    selected = {section[2] for row in inputs for section in row["sections"]}
    host.require(len(selected) == 11212 and not selected.intersection(baseline), "Stock sources collide with the original native/component registry")
    preloaded, expected = {}, {}
    for index, row in enumerate(inputs):
        source, filename = row["source"], row["name"]
        name_offset = (len(source) + 15) & ~15
        encoded = filename.encode("ascii") + b"\0"
        owned = host.success(api.VirtualAllocEx(process, None, name_offset + len(encoded), 0x3000, 4), "Allocate owned complete original core source")
        host.remote_write(api, process, owned, source)
        host.remote_write(api, process, owned + name_offset, encoded)
        host.native_call(api, process, host.QB_PINS["load"], [owned + name_offset, owned, 0], record, deadline, log)
        host.require(read(owned, 1) == b"\1", "Original complete core source was not processed")
        preloaded[filename] = (owned, len(source))
        for offset, kind, key, value in row["sections"]:
            expected[key] = (owned + offset, kind, import_qb.qbkey(filename), value + owned if kind >> 16 in (3, 4, 5, 6, 7, 10, 12) else value)
        log("core_source_loaded", order=index, filename=filename, bytes=len(source), sha256=row["sha256"], source_sections=len(row["sections"]))
    actual = graph(13504, preloaded.values())
    for key, (pointer, values) in actual.items():
        if key in baseline:
            before_pointer, before = baseline[key]
            host.require(pointer == before_pointer and values[:4] == before[:4], "Loading stock core replaced original native/component symbol data")
        else:
            want_pointer, kind, owner, value = expected[key]
            host.require(pointer == want_pointer and values[:4] == (kind, key, owner, value), "Original loader publication differs from the complete source record")
    host.require(all(word(a) == 0 for a in (0x6dcf84, 0x6dcf8c, 0x6dcf90)), "Loading original core unexpectedly started scripts")
    log("core_pass", source_files=268, source_sections=11212, empty_sources=9, native_symbols=13504,
        original_registration_symbols_preserved=2292, original_archive_order=True,
        source_buffers_retained_until_child_exit=True, scripts_executed=0, backend_ready=False)
    return preloaded
