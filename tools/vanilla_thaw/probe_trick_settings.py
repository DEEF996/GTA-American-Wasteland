"""Original profile binding edits and readback between completed native frames.

The catalogue and bindings come from the running stock QB/profile APIs. Only
owned argument buffers are written by Python; native setters own profile edits.
A failed native call is terminal for this isolated child, not a retry/rollback
opportunity. Successful probes restore both edits through the same native APIs.
The live adapter uses prepare(); run() retains the bounded roundtrip probe.
"""
import hashlib
import json
from pathlib import Path
import re
import struct

import probe_handoff
import probe_loader as host
import probe_profile
import probe_vm_idle
from import_qb import qbkey

MAPPING, SPECIALS, CATALOGUE = 0xd544aa2d, 0xddbee809, 0x5004a967
UNASSIGNED, FLIP = 0xf60c9090, 0xba15fc7f
ORDINARY_SLOT = qbkey('Air_SquareL')
PINS = {
    'GetMapping': ('ProfileGetMapping', 0x5975f0, 27, '93abcbd84c00eac5dbd390cabef6869f3a1f734b97d4f9417ee8838cf2940e8f'),
    'ReadSpecial': ('ProfileReadSpecial', 0x5983b0, 224, '7e2c1ccdcdcb93b5f1014cb840ed80dc234d5a5e15570fe31d85ceca0f887c4f'),
    'SetSpecial': ('ProfileSetSpecial', 0x598490, 244, '72bf7d10022c0dc4e5642ac2227c87ba46e86ff6f6d1ab795c3402506f145afd'),
    'SpecialEmpty': ('SpecialEmpty', 0x598980, 14, '2b9bccd2ff7541ae5e7aa743fae54c4ad9e8ce2a0ed8e7a63d43e9970e777a67'),
    'SpecialRecordCtor': ('SpecialRecordCtor', 0x598970, 7, '1b1fed12d15d057c77b31dbf3d59d5f0d0c14d01e77e58f020c7f60c81bb62c2'),
    'Rebuild': ('UpdateTrickMappings', 0x5ac970, 137, 'ca2d5ecc4c8049f58f35bb0d5e0ebd7d5a87245dc0b4027f7f100f3ed31c9617'),
    'ComponentRebuild': ('TrickRebuild_dispatch_entry', 0x473630, 6, '5217a546b537ed5b79fd3aceafc5c2a013b8fe4a0cbdcf8ad051702334a11b4d'),
    'GetStructure': ('SettingsGetStructure', 0x4148c0, 69, '9a5d725ddfc5405b650f679db56c2659797e35f6d4e72b25f32e8e381b634c23'),
    'GetArray': ('SettingsGetArray_dispatch_entry', 0x415170, 6, '652da70c9f93ed89daecda2e346a6ddd2946db78a41535aab5c1c06539eeeba2'),
    'AddInteger': ('SettingsAddInteger', 0x415d30, 114, '676c92ac3d9aa18d59dea75b7b8af629c18e0835b2c7a2ae8f6b8bf1295af3c8'),
    'PlayersIndexed': ('SettingsPlayersIndexed_region', 0x4a0490, 80, 'de2b00459e0c812db952d081b5d6f3a4c80126aac70dba4b3d6f349de4dcd6f8'),
    'ProfileIndexed': ('SettingsProfileIndexed', 0x589890, 11, '01bea150152ed9de6b5b5bc0133c335e5df336d434febc2841c22492004c62ec'),
    'IsNetwork': ('SettingsIsNetwork', 0x4a4b40, 58, '668d003f7c509ab30321e58772e0d4c8faa9d45e3b0c411f0e65bd8fa653666f'),
    'GetChecksum': host.VM_PINS['StructGetChecksum'],
    'Lookup': host.QB_PINS['lookup'],
}
LABEL_PINS = {
    'GetText': ('SettingsOriginalGetText', 0x414580, 89, '61870b9c87471fd59a4282fe20e64193c5e9bebfe712567dabc8b8df9c455eb3'),
    'DisplayTextCaller': ('SettingsOriginalDisplayText', 0x5acb10, 302, 'b2b7b588d712de3d65b31ab821f3ce29494e990e2742d85a738849fc4e431114'),
}
DEBUG_NAME_BYTES = 13060640
DEBUG_NAME_SHA = '846e58ec9203e36c49e3a3a673984cc4839644cc5f2e739d1c6893235455a407'
PINS.update({key: probe_handoff.PINS[key] for key in
             ('StructCtor', 'StructDtor', 'AddChecksum', 'ContainsFlag')})
PINS.update({key: probe_profile.PINS[key] for key in
             ('Current', 'GetStat', 'GlobalArray', 'GlobalStructure', 'ArrayPointer')})


def read_labels(resource_manifest):
    """Only the pinned private stock checksum dictionary; no fallback guesses."""
    path = Path(resource_manifest)
    manifest = json.loads(path.read_text(encoding='utf-8-sig'))
    host.require(path.is_absolute() and manifest['format'] == 'gta-thaw-private-stock-resources-v1' and
                 Path(manifest['directory']).resolve() == path.resolve().parent,
                 'Settings labels require the private verified resource cache')
    matches = [row for row in manifest['files'] if row['path'].replace('\\', '/') == 'data/pak/dbg.pak.wpc']
    host.require(len(matches) == 1 and matches[0]['bytes'] == DEBUG_NAME_BYTES and
                 matches[0]['sha256'] == DEBUG_NAME_SHA, 'Pinned stock checksum dictionary is absent')
    source = path.parent / 'data/pak/dbg.pak.wpc'
    host.require(source.resolve().is_relative_to(path.resolve().parent), 'Stock label input escapes its cache')
    raw = source.read_bytes()
    host.require(len(raw) == DEBUG_NAME_BYTES and hashlib.sha256(raw).hexdigest() == DEBUG_NAME_SHA,
                 'Original stock checksum dictionary differs')
    return {int(key, 16): label.decode('latin1') for key, label in
            re.findall(rb'0x([0-9a-fA-F]{8}) ([^\r\n\0]+)', raw)}


def source_label(dictionary, key):
    label = dictionary.get(key)
    host.require(label is not None and 0 < len(label) <= 256 and qbkey(label) == key,
                 f'Original label is absent or disagrees with its checksum: {key:08x}')
    return label


def mapping_keys(read, pointer, resolve):
    """Read CStruct links; resolve unnamed includes with the original API.

    GetChecksum414A50 itself proves CStruct+4, CComponent type(byte1>>1),
    key+4, value+8 and next+12. Values are subsequently read by GetChecksum,
    preserving original include/override semantics rather than emulating them.
    """
    keys, unresolved, visiting = set(), set(), set()
    budget = 512

    def visit(address):
        nonlocal budget
        host.require(address and address % 4 == 0 and address not in visiting and
                     len(visiting) < 32, 'Mapping structure is null, cyclic or too deep')
        visiting.add(address)
        node = struct.unpack('<2I', read(address, 8))[1]
        seen = set()
        while node:
            host.require(node % 4 == 0 and node not in seen and budget > 0,
                         'Mapping component chain is cyclic or exceeds its bound')
            seen.add(node)
            budget -= 1
            flags, key, value, node = struct.unpack('<4I', read(node, 16))
            host.require((flags >> 8 & 255) >> 1 == 13,
                         'Mapping contains a non-checksum component')
            if key:
                keys.add(key)
            else:
                included = resolve(value)
                if included:
                    visit(included)
                else:
                    unresolved.add(value)
        visiting.remove(address)

    visit(pointer)
    host.require(0 < len(keys) <= 256, 'Original normal mapping is empty or excessive')
    return sorted(keys), sorted(unresolved)


def special_record(raw):
    host.require(len(raw) == 12 and raw[8] in (0, 1), 'Invalid native special record')
    # Original ctor598970 initializes only byte8; bytes9..11 are padding.
    trick, combo = struct.unpack_from('<2I', raw)
    return trick, combo, raw[8]


def select_edits(snapshot, catalogue):
    host.require(len(catalogue) == len({r['key'] for r in catalogue}) == 210,
                 'Original configurable catalogue must contain 210 distinct entries')
    choices = {row['key']: row for row in catalogue if row['defined']}
    ordinary = snapshot['normal'][ORDINARY_SLOT]
    current = choices.get(ordinary)
    host.require(current and current['category'] == FLIP and not current['special'],
                 'Original left flip binding is not a configurable ordinary flip')
    replacement = next((r['key'] for r in catalogue if r['defined'] and
                        r['category'] == FLIP and not r['special'] and r['key'] != ordinary), None)
    host.require(replacement is not None, 'No compatible stock ordinary flip exists')
    for index, (trick, combo, cat) in enumerate(snapshot['special']):
        current = choices.get(trick)
        if cat or combo == UNASSIGNED or not current or not current['special']:
            continue
        target = next((r['key'] for r in catalogue if r['defined'] and r['special'] and
                       r['category'] == current['category'] and r['key'] != trick), None)
        if target is not None:
            return replacement, index, (target, combo, 0)
    raise RuntimeError('No occupied stock special has a compatible different special')


def changed_snapshot(before, ordinary, special_index, special):
    return dict(before, normal=before['normal'] | {ORDINARY_SLOT: ordinary},
                special=[special if i == special_index else value
                         for i, value in enumerate(before['special'])])


def prepare(api, process, record, deadline, log, context, system, *, vm_sources, labels=None):
    system.worker.check_call_access()
    read, word = system.read, system.word
    admitted = probe_vm_idle.require_idle(system, context, vm_sources)
    clock = read(0x8459c8, 16)
    host.require(clock[:8] == clock[8:], 'Settings require a completed original frame')
    pins = PINS | (LABEL_PINS if labels is not None else {})
    for name, address, size, digest in pins.values():
        host.require(hashlib.sha256(read(address, size)).hexdigest() == digest,
                     'Original settings pin differs: ' + name)
    arena = (word(0x84376c), word(0x843770))
    regions = [arena] + [(p, p + size) for p, size in vm_sources.values()]

    def checked(address, size):
        host.require(address % 4 == 0 and any(start <= address and address + size <= end
                     for start, end in regions), 'Settings object leaves original owned memory')
        return read(address, size)

    def call(key, args=(), this=None):
        return host.native_call(api, process, pins[key], list(args), record, deadline, log, this)

    actor, manager = context['actor'], word(0x83e894)
    checked(actor, 0x35c)
    checked(manager, 40)
    profile = call('Current', this=manager)
    checked(profile, 0x78)
    expected_stats = struct.pack('<10f', *([10.] * 10))
    host.require(word(actor) == 0x6989e8 and read(actor + 0xd8, 1) == b'\1' and
                 read(actor + 0x2c8, 40) == expected_stats and word(manager + 0x1c) == 0 and
                 word(manager + 0x14) == profile and call('ProfileIndexed', (0,), manager) == profile and
                 call('PlayersIndexed', (0,), word(0x6ea804)) == actor and
                 call('IsNetwork', this=word(0x83e468)) & 255 == 0,
                 'Settings require the actual local slot-zero 10/10 actor/profile')
    component = context['components'][0x270f56e1]
    checked(component, 24)
    host.require(word(component + 4) == 0x270f56e1 and word(component + 12) == actor,
                 'Original Trick component ownership differs')
    pose = read(actor + 0x28, 0xb0)  # matrices, current/previous position and velocity
    source_name = 'scripts/game/skater/alltricks.qb.wpc'
    host.require(source_name in vm_sources and vm_sources[source_name][1] == 900,
                 'Original stock alltricks source is not retained')
    source, _ = vm_sources[source_name]
    symbol = call('Lookup', (CATALOGUE,))
    host.require(symbol == source + 28 and struct.unpack('<3I', read(symbol, 12)) ==
                 (0xc0400, CATALOGUE, qbkey(source_name)), 'Stock catalogue publication differs')
    scratch = system.allocate(bytes(256))
    output, parameters = scratch + 64, scratch + 128
    canary = b'TRKS'

    def field(key, structure, method='GetChecksum'):
        system.write(scratch, bytes(4))
        present = call(method, (key, scratch, 0), structure) & 255
        host.require(present in (0, 1), 'Original typed getter returned an invalid Boolean')
        return word(scratch) if present else None

    def source_text(address):
        spans = [(end - address) for start, end in regions + [(0x68e000, 0x6bc000)]
                 if start <= address < end]
        host.require(bool(spans), 'Original trick text is outside verified source/arena/image')
        raw = read(address, min(256, max(spans)))
        host.require(b'\0' in raw and raw[0] != 0, 'Original trick text is empty or unterminated')
        return raw.split(b'\0', 1)[0].decode('cp1252')

    def mapping():
        value = call('GetMapping', (MAPPING,), profile)
        checked(value, 8)
        return value

    specials = call('GetMapping', (SPECIALS,), profile)
    checked(specials, 8)
    special_array = field(0, specials, 'GetArray')
    host.require(special_array is not None, 'Original profile specials array is absent')
    special_header = struct.unpack('<3I', checked(special_array, 12))
    host.require(special_header[0] >> 16 & 255 == 10 and special_header[1] == 12,
                 'Original stock special slot count/type differs')

    def snapshot():
        current_mapping = mapping()
        keys, includes = mapping_keys(checked, current_mapping,
                                     lambda key: call('GlobalStructure', (key, 0)))
        normal = {key: field(key, current_mapping) for key in keys}
        host.require(all(value is not None for value in normal.values()),
                     'Original normal mapping has an unreadable checksum')
        special_values = []
        for index in range(special_header[1]):
            system.write(output - 4, canary + bytes(12) + canary)
            host.require(call('ReadSpecial', (output, index), profile) == output and
                         read(output - 4, 4) == read(output + 12, 4) == canary,
                         'Original special getter returned a different buffer or overwrote guards')
            value = special_record(read(output, 12))
            host.require(bool(call('SpecialEmpty', this=output) & 255) == (value[0] == UNASSIGNED),
                         'Original special emptiness differs from its checksum')
            special_values.append(value)
        return dict(normal=normal, unresolved_includes=includes, special=special_values,
                    stats=[call('GetStat', (key,), profile) for key in probe_profile.STATS])

    array = call('GlobalArray', (CATALOGUE, 0))
    header = struct.unpack('<3I', checked(array, 12))
    host.require(header[0] >> 16 & 255 == 13 and header[1] == 210,
                 'Original configurable trick array differs')
    catalogue = []
    for index in range(header[1]):
        key = call('ArrayPointer', (index,), array)
        definition = call('GlobalStructure', (key, 0))
        if not definition:
            variants = call('GlobalArray', (key, 0))
            if variants:
                variant_header = struct.unpack('<3I', checked(variants, 12))
                host.require(variant_header[0] >> 16 & 255 == 10 and 1 <= variant_header[1] <= 32,
                             'Original trick variant array differs')
                definition = call('ArrayPointer', (0,), variants)
        row = dict(key=key, defined=bool(definition))
        if definition:
            checked(definition, 8)
            category = field(0x89fa55ce, definition)
            if category is None:
                category = field(0xa6d2d890, definition)
            params = field(0x7031f10c, definition, 'GetStructure')
            host.require(params is not None, 'Original configurable trick Params are absent')
            checked(params, 8)
            row.update(category=category if category is not None else 0x6881fcb4,
                       special=bool(call('ContainsFlag', (0x1aeeaa40,), params) & 255))
            if labels is not None:
                name = field(0xa1dc81f9, params, 'GetText')
                host.require(name is not None, 'Original configurable trick display Name is absent')
                row['name'] = source_text(name)
        catalogue.append(row)
    before = snapshot()
    host.require(before['stats'] == [10] * 10, 'Profile statistics are not all 10/10')
    menu_catalogue = None
    if labels is not None:
        categories = {0xba15fc7f: 'Flip', 0xb9ae26d2: 'Grab', 0x6881fcb4: 'Grind',
                      0xc4017218: 'Lip', 0xef24413b: 'Manual', 0x80c7c0f5: 'Manual', 0xe4336688: 'Jump'}
        log('trick_settings_catalogue_inspected', catalogue=catalogue)
        available = [row for row in catalogue if row['defined']]
        host.require(available and all(row['category'] in categories for row in available),
                     'Stock UI catalogue contains an unsupported original trick category')
        tricks = [dict(checksum=r['key'], name=r['name'], category=categories[r['category']],
                       special=r['special']) for r in available]
        slots = []
        for key, value in before['normal'].items():
            label = source_label(labels, key)
            lower = label.lower()
            category = ('Lip' if lower.startswith('lip_') else 'Jump' if lower == 'jumpslot' else
                        'Extra' if lower.startswith('extra_square') else
                        ('Flip' if 'square' in lower else 'Grab') if lower.startswith('air_') else 'Other')
            slots.append(dict(checksum=key, name=label, category=category, default_trick=value))
        name = 'scripts/game/menu/trickmenu.qb.wpc'
        host.require(name in vm_sources and vm_sources[name][1] == 6828,
                     'Original stock trick menu source is absent')
        combo_key = 0x544fe697
        combo_symbol = call('Lookup', (combo_key,))
        host.require(combo_symbol == vm_sources[name][0] + 2716 and
                     word(combo_symbol + 8) == qbkey(name), 'Original special combo publication differs')
        combo_array = call('GlobalArray', (combo_key, 0))
        header = struct.unpack('<3I', checked(combo_array, 12))
        host.require(header[0] >> 16 & 255 == 10 and header[1] == 60,
                     'Original special combo array differs')
        combo_categories = {0xb6a6a746: 'Air', 0x0568d6ef: 'Grind', 0xd0765138: 'Lip', 0xd6db8a01: 'Manual'}
        combos = []
        for combo_index in range(header[1]):
            item = call('ArrayPointer', (combo_index,), combo_array)
            checked(item, 8)
            key = field(0xa92a2280, item)
            category = field(0x108af607, item)
            host.require(key is not None and category in combo_categories,
                         'Original special combo fields differ')
            combos.append(dict(checksum=key, name=source_label(labels, key), category=combo_categories[category]))
        host.require(len({row['checksum'] for row in combos}) == 60, 'Original combo keys are duplicated')
        menu_catalogue = dict(unassigned=UNASSIGNED, tricks=tricks, slots=slots, combos=combos,
                              source_specials=[dict(trick=v[0], combo=v[1], is_cat=bool(v[2])) for v in before['special']],
                              unavailable=[dict(checksum=r['key'], name=source_label(labels, r['key']))
                                           for r in catalogue if not r['defined']])
        log('trick_settings_menu_catalogue', catalogue=menu_catalogue,
            source_display_names=True, stock_checksum_dictionary_sha256=DEBUG_NAME_SHA,
            named_options_exported=False, source_script_editor=False)
    host.require(call('StructCtor', this=parameters) == parameters,
                 'Original settings parameter constructor failed')
    call('AddInteger', (0x5b8ab877, 0), parameters)

    def apply_native(target):
        system.worker.check_call_access()
        admitted_now = probe_vm_idle.require_idle(system, context, vm_sources)
        frame_clock, actor_pose = read(0x8459c8, 16), read(actor + 0x28, 0xb0)
        host.require(frame_clock[:8] == frame_clock[8:], 'Settings require a completed original frame')
        current = snapshot()
        changed = False
        for key, value in target['normal'].items():
            if current['normal'][key] != value:
                call('AddChecksum', (key, value), mapping())
                changed = True
        for index, value in enumerate(target['special']):
            if current['special'][index] != value:
                system.write(output - 4, canary + struct.pack('<2IB3x', *value) + canary)
                host.require(call('SetSpecial', (index, output, 0), profile) & 255 == 1 and
                             read(output - 4, 4) == read(output + 12, 4) == canary,
                             'Original special setter failed or changed its input guards')
                changed = True
        if changed:
            host.require(call('Rebuild', (parameters, 0)) & 255 == 1,
                         'Original UpdateTrickMappings rejected the local slot')
        result = snapshot()
        host.require(result == target, 'Original settings readback differs from requested bindings')
        host.require(probe_vm_idle.require_idle(system, context, vm_sources) == admitted_now and
                     read(0x8459c8, 16) == frame_clock and read(actor + 0x28, 0xb0) == actor_pose and
                     read(actor + 0x2c8, 40) == expected_stats,
                     'Settings changed VM admission, clock, pose or 10/10 actor statistics')
        log('trick_settings_applied', changed=changed, stats=result['stats'])
        return result

    return TrickSettings(before, catalogue, menu_catalogue, apply_native,
                         lambda: call('StructDtor', this=parameters))


class TrickSettings:
    def __init__(self, baseline, catalogue, menu_catalogue, apply_native, close_native):
        self.baseline, self.catalogue, self.menu_catalogue = baseline, catalogue, menu_catalogue
        self._apply_native, self._close_native = apply_native, close_native
        self.current = baseline

    def apply(self, normal, special):
        host.require(self.menu_catalogue is not None, 'Named stock catalogue required for user settings')
        target = validate_assignments(self.baseline, self.menu_catalogue, normal, special)
        if target != self.current:
            self.current = self._apply_native(target)
        return self.current

    def close(self):
        if self._close_native is not None:
            close, self._close_native = self._close_native, None
            close()


def validate_assignments(baseline, catalogue, normal, special):
    """Validate the full resulting profile before calling any native setter."""
    host.require(type(normal) is dict and type(special) is dict,
                 'Trick assignments must be objects')
    tricks = {row['checksum']: row for row in catalogue['tricks']}
    slots = {row['checksum']: row for row in catalogue['slots']}
    combos = {row['checksum']: row for row in catalogue['combos']}
    target = dict(baseline, normal=dict(baseline['normal']), special=list(baseline['special']))

    def key(value):
        host.require(type(value) is int or (type(value) is str and value.isascii() and value.isdigit()
                     and str(int(value)) == value), 'Invalid setting key')
        return int(value)

    for raw, value in normal.items():
        slot = key(raw)
        host.require(slot in slots and slot in target['normal'] and type(value) is int,
                     'Unknown normal trick assignment')
        if value == baseline['normal'][slot]:
            continue
        row = tricks.get(value)
        host.require(row is not None and not row['special'] and
                     slots[slot]['category'] in ('Flip', 'Grab', 'Lip', 'Jump', 'Manual', 'Grind') and
                     row['category'] == slots[slot]['category'], 'Incompatible normal trick category')
        target['normal'][slot] = value
    for raw, value in special.items():
        index = key(raw)
        host.require(0 <= index < len(target['special']) and type(value) is dict and
                     set(value) == {'trick', 'combo'} and all(type(v) is int for v in value.values()),
                     'Invalid special trick assignment')
        pair = value['trick'], value['combo'], 0
        if pair == baseline['special'][index]:
            continue
        host.require(not baseline['special'][index][2], 'Custom created trick slot is read-only')
        if pair[:2] != (UNASSIGNED, UNASSIGNED):
            row, combo = tricks.get(pair[0]), combos.get(pair[1])
            category = ('Air' if row and row['category'] in ('Flip', 'Grab', 'Jump') else
                        row['category'] if row else None)
            host.require(row is not None and row['special'] and combo is not None and
                         combo['category'] == category, 'Incompatible special trick or combo')
        target['special'][index] = pair
    used = {}
    baseline_used = {}
    for index, (_, combo, _) in enumerate(baseline['special']):
        if combo != UNASSIGNED:
            baseline_used.setdefault(combo, set()).add(index)
    for index, (_, combo, _) in enumerate(target['special']):
        if combo != UNASSIGNED:
            used.setdefault(combo, set()).add(index)
    host.require(all(len(indices) == 1 or indices == baseline_used.get(combo)
                     for combo, indices in used.items()), 'Special combo is already assigned')
    return target


def run(api, process, record, deadline, log, context, system, *, vm_sources, labels=None):
    controller = prepare(api, process, record, deadline, log, context, system,
                         vm_sources=vm_sources, labels=labels)
    before, catalogue = controller.baseline, controller.catalogue
    ordinary, index, special = select_edits(before, catalogue)
    after = controller._apply_native(changed_snapshot(before, ordinary, index, special))
    restored = controller._apply_native(before)
    controller.close()
    log('trick_settings_pass', normal_bindings=len(before['normal']), special_slots=len(before['special']),
        configured_tricks=len(catalogue), original_profile_mapping_restored=True, stats=[10] * 10,
        original_native_setters=True, whole_native_rebuilds=2, native_frames=0,
        host_profile_writes=False, persistent_settings_written=False, f8_gui=False,
        trick_execution_after_edit_tested=False, backend_ready=False)
    return dict(before=before, changed=after, restored=restored, catalogue=catalogue,
                menu_catalogue=controller.menu_catalogue)
