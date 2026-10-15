"""Read-only evidence for original waiting owner contexts between native updates.

The stock Update return 5 at 0x4130E9 does not clear 0x6DCF84. A nonzero pointer is
admissible only when its registered owner, wait state, original source and live
cache body agree. This module never executes, resumes, loads or clears a script.
The first live GTA scene capture verified this ownership chain after SetState.
Without preloaded source provenance, a nonzero context remains inadmissible.
"""
import hashlib
import struct

import probe_loader as host
from import_qb import qbkey

PINS = (
    host.VM_PINS['ScriptUpdate'],
    ('OriginalScriptReturnRestoreEntry', 0x412130, 6, 'bcaf8e83616b9b2f6a4e72a14a83f216e889ee91748f004d4001046541bc1ff8'),
    ('OriginalScriptCacheDeletingDtor', 0x413fe0, 33, 'd2931ffdc4589e819e16cbc10bd04735e9c4f852c5e9927842a458b29c8110f9'),
    ('OriginalScriptCacheVtable', 0x68edac, 12, '54cef13984db718b19d18dbff5b2dbdc19ada51abb88f71ae5ba3dd91b1a040c'),
)


def validate_waiting(value):
    host.require(value['spawned_busy'] == value['owner_busy'] == value['event_depth'] == value['event_busy'] == 0,
                 'Native owner/script/event dispatch is active')
    if value['current_script'] == 0:
        return False
    host.require(value['registered'] and value['owner'] == value['actor'] and value['checksum'] != 0 and
                 1 <= value['wait_state'] <= 14 and value['flags_b4'] == value['flags_bc'] == value['flags_d4'] == 0,
                 'Current script is not a registered, restored waiting context of this actor')
    host.require(value['cache_class_verified'] and value['cache_checksum'] == value['checksum'] and
                 value['cache_reference_count'] > 0 and value['source_kind'] == 0x70400 and
                 value['source_checksum'] == value['checksum'] and value['source_owner'] != 0 and
                 value['source_owned'] and value['source_header_adjacent'] and value['cache_end_token'] == 0x24,
                 'Waiting context does not own verified original source/cache publication')
    length, stored = value['source_bytes'], value['stored_bytes']
    host.require(1 <= stored <= length <= 1024 * 1024 and length <= stored * 18 and
                 value['cache_body'] <= value['program_counter'] < value['cache_body'] + length,
                 'Waiting context PC is outside its original cached source')
    return True


def snapshot(system, context, preloaded=None):
    """Return diagnostic metadata; `admissible` requires verified source buffers.

    `preloaded` is the existing core mapping canonical_name -> (pointer, bytes).
    It is not used to re-read game files, call cache Load, or create new sources.
    Structurally valid contexts without it stay inadmissible, with a reason.
    """
    read, word = system.read, system.word
    for name, va, size, digest in PINS:
        host.require(hashlib.sha256(read(va, size)).hexdigest() == digest, 'Native VM evidence pin differs: ' + name)
    start, end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and start <= address <= end - count * 4,
                     'Waiting VM object leaves its native arena')
        return struct.unpack(f'<{count}I', read(address, count * 4))

    actor, current = context['actor'], word(0x6dcf84)
    event = words(word(0x6dd6d0), 10)
    result = dict(current_script=current, actor=actor, spawned_busy=word(0x6dcf98) & 0xffff,
                  owner_busy=word(0x6dd4a4) & 0xff, event_depth=event[8], event_busy=event[9],
                  native_calls=0, host_vm_context_modified=False, source_buffers_provided=preloaded is not None)
    if not current:
        try:
            validate_waiting(result)
            host.require(word(0x6dcf84) == 0 and words(word(0x6dd6d0), 10) == event and
                         word(0x6dcf98) & 0xffff == result['spawned_busy'] and
                         word(0x6dd4a4) & 255 == result['owner_busy'],
                         'Original idle context changed during read')
            result.update(admissible=True, waiting_owner_context=False)
        except RuntimeError as error:
            result.update(admissible=False, reason=str(error))
        return result
    try:
        owner = words(actor, 0x35c // 4)
        host.require(owner[0] == 0x6989e8 and owner[3] == word(0x6dd6b0) and owner[0xd8 // 4] & 0xff == 1,
                     'Waiting context actor ownership differs')
        count = word(0x6dcf90)
        host.require(1 <= count <= 256, 'Waiting context script registry exceeds diagnostic bound')
        pointer, previous, seen, ids = word(0x6dcf8c), 0, set(), set()
        while pointer:
            host.require(pointer not in seen and len(seen) < count, 'Waiting context script registry cycles')
            row = words(pointer, 63)
            host.require(row[2] == previous and row[44] not in ids, 'Waiting context script links/IDs differ')
            seen.add(pointer)
            ids.add(row[44])
            previous, pointer = pointer, row[1]
        host.require(len(seen) == count, 'Waiting context script registry count differs')
        script = words(current, 63)
        key = script[0xd0 // 4]
        result.update(registered=current in seen, registry_count=count, native_script_id=script[44],
                      owner=script[0xc4 // 4], checksum=key, program_counter=script[4],
                      wait_state=script[0xa8 // 4] & 15, call_depth=script[0x38 // 4],
                      flags_b4=script[0xb4 // 4] & 255, flags_bc=script[0xbc // 4] & 255,
                      flags_d4=script[0xd4 // 4] & 255)
        cache = words(word(0x6dcfac), 9)
        result.update(cache_class_verified=cache[0] == 0x68edac, cache=word(0x6dcfac), cache_vtable=cache[0])
        table = words(cache[5], 7)
        host.require(table[0] == 8 and 1 <= table[2] <= 4096 and table[5] & 0xffffff == 0,
                     'Original ScriptCache map shape differs')
        words(table[1], 256 * 3)
        # Original deletion clears an inline head's key/value but keeps its
        # collision chain. Stock native proof: stock-cache-sentinel-02.jsonl.
        # The table count includes live entries, not that empty inline head.
        head = table[1] + (key & 255) * 12
        pointer, visited, chain, live_entries = head, set(), [], 0
        while True:
            host.require(pointer and pointer not in visited and len(visited) < table[2] + 1,
                         'Waiting cache chain is absent or cyclic')
            visited.add(pointer)
            entry = words(pointer, 3)
            chain.append((pointer, entry))
            if pointer == head and entry[:2] == (0, 0):
                result['cache_empty_inline_head'] = True
            else:
                host.require(pointer == head or not table[1] <= pointer < table[1] + 256 * 12,
                             'Waiting cache chain aliases another inline bucket')
                host.require(entry[1] != 0, 'Waiting cache chain contains an empty non-head entry')
                host.require(entry[0] & 255 == key & 255, 'Waiting cache entry belongs to another bucket')
                live_entries += 1
                host.require(live_entries <= table[2], 'Waiting cache chain exceeds its live entry count')
                if entry[0] == key:
                    break
            pointer = entry[2]
        descriptor = words(entry[1], 5)
        result.update(cache_descriptor=entry[1], cache_checksum=descriptor[0], cache_body=descriptor[1],
                      cache_reference_count=descriptor[2])
        symbols = word(0x6dd0fc)
        words(symbols, 4096)
        pointer, visited = word(symbols + (key & 4095) * 4), set()
        while True:
            host.require(pointer >= 0x10000 and pointer % 4 == 0 and pointer not in visited and len(visited) < 4096,
                         'Waiting source symbol is absent, unaligned or cyclic')
            visited.add(pointer)
            section = struct.unpack('<5I', read(pointer, 20))
            host.require(section[1] & 4095 == key & 4095, 'Waiting source symbol belongs to another bucket')
            if section[1] == key:
                break
            pointer = section[4]
        header = struct.unpack('<3I', read(section[3], 12))
        length, stored = header[1:]
        result.update(source_symbol=pointer, source_kind=section[0], source_checksum=section[1], source_owner=section[2],
                      source_header=section[3], source_header_adjacent=section[3] == pointer + 20,
                      source_bytes=length, stored_bytes=stored, source_owned=False)
        host.require(1 <= stored <= length <= 1024 * 1024 and length <= stored * 18,
                     'Waiting source header has invalid lengths')
        matches = []
        for name, (base, size) in (preloaded or {}).items():
            if base <= pointer and section[3] + 12 + stored <= base + size and section[2] == qbkey(name):
                matches.append(name)
        if len(matches) == 1:
            base, size = preloaded[matches[0]]
            result.update(source_owned=read(base, 1) == b'\1', source_name=matches[0], source_buffer=base, source_buffer_bytes=size)
        words(descriptor[1], (length + 3) // 4)
        result['cache_end_token'] = read(descriptor[1] + length - 1, 1)[0]
        result['waiting_owner_context'] = validate_waiting(result)
        host.require(word(0x6dcf84) == current and words(current, 63) == script and
                     word(0x6dcfac) == result['cache'] and words(result['cache'], 9) == cache and
                     words(cache[5], 7) == table and all(words(address, 3) == row for address, row in chain) and
                     words(entry[1], 5) == descriptor and words(word(0x6dd6d0), 10) == event and
                     word(0x6dcf98) & 0xffff == result['spawned_busy'] and word(0x6dd4a4) & 255 == result['owner_busy'],
                     'Original waiting context changed during read')
        result['admissible'] = True
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        result.update(admissible=False, reason=str(error))
    return result


def require_idle(system, context, preloaded=None):
    result = snapshot(system, context, preloaded)
    host.require(result['admissible'], result.get('reason', 'Original waiting context is not admissible'))
    return result
