"""Install one matched original COL/rail region and its stock effect service.

Prepare inputs through probe_gta_rails.prepare_inputs before starting the child.
The bounded RailStream uses precomputed regional graphs and original resource
load/parser/unload calls. Active or otherwise referenced rail groups stay alive.
"""
import hashlib
import ctypes
import struct
import time
from pathlib import Path

import probe_loader as host
import probe_gta_rails
import probe_particles
from motion_contact import classify

CORE_RAIL_PIN = (0x5cee00, 881, '53df220f585d47053eec4a46f4f328ac7d8f001be2dadc6cf77e509cf2a563c5')
UPDATE_BUDGET_SECONDS = .008
MAX_UPDATE_STAGES = 12
STREAM_PINS = {
    'NodeArrayMemoryLoad': ('NodeArrayMemoryLoad', 0x41aee0, 312, '1dcc0bced54e425a5d7641aa1656f65751faf0ccbda1adec5b7e74f3a1ab0dba'),
    'ResourceMemoryCopyDispatch': ('ResourceMemoryCopyDispatch', 0x419d80, 62, '404f256211fe7047a43dba90c2dbefdf7acfb4d2b8efe26c251411a8d3e45d63'),
    'ResourceMemoryParse': ('ResourceMemoryParse', 0x419f90, 70, 'd6e39a797b0dae54228578502a1fb16d6650b491d7a13c4b1d41aa8c63857d02'),
    'ResourceMemoryCopy': ('ResourceMemoryCopy', 0x41a090, 182, '1a2d5c3fac90713703172536b5d31ea5ed8837b794607286618a767d244c1be3'),
    'NodeArrayMemoryDescriptor': ('NodeArrayMemoryDescriptor', 0x41bae0, 38, 'db1f24415ab135d0c5cca59e33c42aad49a17b7a4b89c656503847d7b751184d'),
    'NodeArrayManUnload': ('NodeArrayManUnload', 0x59b520, 38, '03627c92b79da16f777c065858c1d469d0370dbd2635fea0f00daba2c6a6e0b3'),
    'RailOrphanCleanup': ('RailOrphanCleanup', 0x58b030, 236, '54b544457402a7e81a14843de35c04413318f5e93bff6f76afba12a3cabb7ce9'),
    'NodeManagerRemove': ('NodeManagerRemove', 0x588810, 118, 'ddf8c4f440ca753c9858e1048e907efb26be10f5ae7f5dfdf2d2618a3f6e7a3c'),
    'RailManagerSetActive': ('RailManagerSetActive', 0x58b930, 198, '83892e0e581034ff6619a9f28d075967073c153ec698402264676094b6a4d8b7'),
}


def memory_resource_input(resource):
    """Original 0x94-byte NodeArray descriptor, followed by immutable QB input."""
    name = resource['native_filename']
    source, report = resource['source'], resource['report']
    host.require(isinstance(name, str) and name.isascii() and
                 name == 'gta_thaw/streamed-rails/'+report['basename']+'.qb' and
                 all(part not in ('', '.', '..') for part in name.split('/')) and
                 ':' not in name and '\\' not in name and '\0' not in name,
                 'Streaming rail filename is not a bounded private resource path')
    filename = name.encode('ascii')+b'.Wpc\0'
    host.require(len(filename) <= 120 and isinstance(source, bytes) and
                 0 < len(source) <= 4*1024*1024 and len(source) == report['qb_bytes'] and
                 hashlib.sha256(source).hexdigest() == resource['key'] == report['qb_sha256'],
                 'Streaming rail memory content or filename differs')
    # 41BAE0 copies exactly 0x94 bytes. The manager fills the four ownership
    # fields at +80..8C; +90 remains the same null argument as the disk loader.
    return filename.ljust(0x94, b'\0')+source


def _rail_content(inputs):
    """Check exact bytes and the collision coordinate frame on every first use."""
    rails = inputs.get('rails')
    host.require(isinstance(rails, dict) and {'source', 'report'} <= set(rails),
                 'Streamed world tile has no prepared global rail graph')
    source, report = rails['source'], rails['report']
    if source is None:
        host.require(report is None, 'Empty rail graph has an unexpected report')
        return source, report, inputs['col_sha256']+'-empty'
    host.require(isinstance(source, bytes) and isinstance(report, dict) and
                 2 <= report['node_count'] <= probe_gta_rails.rail_export.MAX_NODES and
                 len(source) == report['qb_bytes'] and
                 hashlib.sha256(source).hexdigest() == report['qb_sha256'] and
                 report['scene_col_sha256'] == inputs['col_sha256'] and
                 report['coordinates'] == inputs['coordinates'], 'Prepared streaming rails differ from collision origin/content')
    return source, report, report['qb_sha256']


def rail_identity(inputs):
    """Full independent QB validation; also the fallback for unsealed inputs."""
    source, report, key = _rail_content(inputs)
    if source is not None:
        expected = [{key: row[key] for key in ('name', 'position_thaw', 'next_index', 'thaw_type', 'thaw_terrain')}
                    for row in report['nodes']]
        host.require(all((row['thaw_type'], row['thaw_terrain']) ==
                         probe_gta_rails.rail_export.terrain_mapping.rail(row['gta_surface_material'])
                         for row in report['nodes']), 'Prepared rail material policy differs')
        host.require(probe_gta_rails.rail_export.read_nodes(source, report['basename']) == expected,
                     'Prepared streaming rail NodeArray differs from its complete report')
    return key


def _rail_seal(inputs):
    # Bind all report data consumed by native staging and observation. The much
    # larger offline candidate-provenance graph is never a native loader input.
    report = inputs['rails']['report']
    metadata = dict(col_sha256=inputs['col_sha256'], coordinates=inputs.get('coordinates'),
        report=None if report is None else {key: report[key] for key in (
            'qb_sha256', 'qb_bytes', 'scene_col_sha256', 'coordinates', 'basename',
            'array_checksum', 'node_count', 'nodes')})
    return hashlib.sha256(probe_gta_rails.rail_export.encoded(metadata)).hexdigest()


def prepare_rail_validation(inputs):
    """Run in the owned compiler process, before any original native staging."""
    key = rail_identity(inputs)
    return dict(format='gta-thaw-rail-validation-v1', key=key, seal=_rail_seal(inputs))


def verified_rail_identity(inputs):
    """Use the worker's full validation only while its exact binding still holds."""
    rails = inputs.get('rails')
    validation = rails.get('validation') if isinstance(rails, dict) else None
    if validation is None:
        return rail_identity(inputs)
    _, _, key = _rail_content(inputs)
    host.require(isinstance(validation, dict) and
                 validation.get('format') == 'gta-thaw-rail-validation-v1' and
                 validation.get('key') == key and validation.get('seal') == _rail_seal(inputs),
                 'Prepared streaming rail validation binding changed')
    return key


def activation_stub(names, active, manager, record):
    """Owned ABI glue batches whole original chain activation calls, not physics."""
    host.require(1 <= len(names) <= 128 and type(active) is bool and
                 len(set(names)) == len(names) and all(type(name) is int and 0 < name <= 0xffffffff for name in names),
                 'Invalid original rail activation batch')
    pack = lambda value: struct.pack('<I', value)
    code = bytearray(b'\xc7\x05'+pack(record)+pack(2))
    for name in names:
        for value in (1, int(active), name):
            code.extend(b'\x68'+pack(value))
        code.extend(b'\xb9'+pack(manager)+b'\xb8'+pack(STREAM_PINS['RailManagerSetActive'][1])+b'\xff\xd0')
    code.extend(b'\xc7\x05'+pack(record)+pack(6)+host.STUB)
    host.require(len(code) <= 4096, 'Original rail activation glue exceeds the owned command page')
    return bytes(code)


def retired_candidates(groups, current_key, blocks):
    """Pure lifetime decision: do not unload names backing any live rail pointer."""
    from probe_scene_references import reference_words
    return [item for item in groups if item['key'] != current_key and
            not reference_words(blocks, [(item['group'], item['group']+item['nodes']*0x60)])]


class RailStream:
    """Bounded native preparation per update, with ten original manager slots.

    Prepare each graph in the world compiler. While ``ready`` is false the
    caller keeps its exact accepted GTA movement sample alive and does not
    advance FrameDriver. Original source frames are never used for cleanup.
    """
    def __init__(self, api, process, record, deadline, log, context, system, *,
                 scene_native, directory, preloaded, dynamic_rails=None):
        self.api, self.process, self.record, self.deadline, self.log = api, process, record, deadline, log
        self.context, self.system, self.scene_native, self.preloaded = context, system, scene_native, preloaded
        self.dynamic_rails = dynamic_rails
        self.directory = Path(directory).resolve()
        self.current, self.pending, self.stage, self.groups = None, None, None, []
        self.poisoned, self.last_key, self.retirement_queue = False, None, []
        self.activation_queue = []
        self.prefetched = None
        self._forecast_inputs = self._forecast_key = None
        self._verified_inputs, self._verified_key = None, None
        self.scene_native.idle()
        for name, va, size, digest in [*probe_gta_rails.PINS.values(), *STREAM_PINS.values()]:
            host.require(hashlib.sha256(system.read(va, size)).hexdigest() == digest, 'Streaming rail pin changed: '+name)
        game = system.word(0x83e81c)
        self.rail_manager = system.word(game+0x24)
        self.node_manager = system.word(0x83e87c)
        if not self.node_manager:
            host.require(system.word(0x83e880) == 0, 'Unexpected original NodeManager reference before acquisition')
            self.node_manager = self.call('NodeManagerAcquire', (1,))
        host.require(self.node_manager and self.words(self.rail_manager, 41) == (0,)*41,
                     'Streaming rails require the genuine initially empty original manager')
        self.global_slot = self.words(self.node_manager, 8)
        host.require(self.global_slot[0] == probe_gta_rails.GLOBAL_ARRAY and self.global_slot[1] and
                     system.word(0x83e880) == 1 and system.word(0x83e87c) == self.node_manager and
                     self.words(self.node_manager+32, (0x600-32)//4) == (0,)*((0x600-32)//4),
                     'Streaming rails lost the original Global NodeArray or acquired foreign arrays')
        cwd = system.allocate(bytes(1024))
        count = system.call('kernel32.dll', 'GetCurrentDirectoryW', (512, cwd))
        host.require(0 < count < 512 and Path(system.read(cwd, count*2).decode('utf-16le')).resolve() == self.directory,
                     'Native streaming rail resource working directory differs')
        self.scratch = system.allocate(bytes(4096))
        self.params, self.filename = self.scratch, self.scratch+128
        host.require(self.call('NativeCStructCtor', this=self.params) == self.params,
                     'Streaming rail CStruct constructor failed')
        config = probe_gta_rails.dynamic_rail_config()
        self.config = system.allocate(config+probe_gta_rails.CONFIG_NAME.encode('ascii')+b'\0')
        self.resource_manager = system.word(0x6dd3f4)
        self.words(self.resource_manager, 15)
        host.require(system.word(0x6dd3f8) == 1, 'Original resource manager references differ')
        self.free = api.VirtualFreeEx
        self.free.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
        self.free.restype = ctypes.c_int
        self.stage = 'configure'

    def words(self, address, count):
        start, end = self.scene_native.arena
        host.require(address % 4 == 0 and start <= address <= end-count*4,
                     'Streaming rail native allocation leaves original arena')
        return struct.unpack('<'+str(count)+'I', self.system.read(address, count*4))

    def call(self, name, args=(), this=None):
        pin = STREAM_PINS.get(name, probe_gta_rails.PINS.get(name))
        return host.native_call(self.api, self.process, pin, list(args), self.record,
                                self.deadline, self.log, this)

    def idle(self):
        self.scene_native.idle()
        host.require(probe_gta_rails.probe_vm_idle.snapshot(self.system, self.context, self.preloaded)['admissible'],
                     'Streaming rails require the restored original VM')
        host.require(self.system.word(self.system.word(0x83e81c)+0x24) == self.rail_manager and
                     self.system.word(0x83e87c) == self.node_manager and
                     self.words(self.node_manager, 8) == self.global_slot and
                     self.system.word(0x83f348) == self.system.word(0x83f398) == 0,
                     'Streaming rail manager/parser ownership changed')

    def headers(self):
        words = self.words(self.rail_manager, 41)
        groups = []
        for slot in range(10):
            group, capacity, count, borrowed = words[1+slot*4:5+slot*4]
            if group:
                host.require(capacity == count and 2 <= count <= 8192 and borrowed == 0,
                             'Streaming rails found a foreign or incomplete native group')
                self.words(group, count*24)
                groups.append(dict(slot=slot, group=group, nodes=count))
            else:
                host.require((capacity, count, borrowed) == (0, 0, 0), 'Empty native rail slot contains retained storage')
        host.require(words[0] == len(groups), 'Original RailManager group census differs')
        return groups

    def parameters(self, filename=None, array=None):
        self.call('StructClear', this=self.params)
        if filename is not None:
            data = filename.encode('ascii')+b'\0'
            host.require(len(data) <= 120 and ':' not in filename and '/' in filename and '\0' not in filename,
                         'Streaming rail filename is not a bounded private resource path')
            self.system.write(self.filename, data)
            self.call('NativeAddString', (0, self.filename), self.params)
        else:
            self.call('NativeAddChecksum', (0xd6b86721, array), self.params)

    def status(self, ready):
        return dict(ready=ready, stage=self.stage, nodes=self.current['nodes'] if self.current else 0,
                    retained_groups=len(self.groups), key=self.last_key)

    def _snapshot(self):
        addresses = (0x6dcf84, 0x6dcf8c, 0x6dcf90, 0x6dcf98, 0x6dd4a4, 0x6dd4a8)
        depth = self.system.word(0x842b18+0x10c)
        host.require(depth < 16, 'Original heap stack exceeds its native capacity')
        reference = self.context['components'][0x5bd63b29]+0x3f0
        return dict(actor_before=self.system.read(self.context['actor'], 0x35c),
            rail_reference_address=reference, rail_reference=self.system.read(reference, 8),
            vm_addresses=addresses, vm_before=[self.system.word(va) for va in addresses],
            heap_depth=depth, heap_before=self.system.read(0x842b18+0xc8, (depth+1)*4))

    def _unchanged(self, snapshot):
        host.require(self.system.read(self.context['actor'], 0x35c) == snapshot['actor_before'] and
                     self.system.read(snapshot['rail_reference_address'], 8) == snapshot['rail_reference'] and
                     [self.system.word(va) for va in snapshot['vm_addresses']] == snapshot['vm_before'] and
                     self.system.read(0x842b18+0xc8, (snapshot['heap_depth']+1)*4) == snapshot['heap_before'] and
                     self.system.word(0x842b18+0x10c) == snapshot['heap_depth'],
                     'Original rail resource parser changed the actor, restored VM or heap stack')

    def _resource(self, inputs, key):
        report = inputs['rails']['report']
        foreign_names = {name for row in self.groups for name in row['nodes_by_name']}
        host.require(not foreign_names.intersection(row['name'] for row in report['nodes']),
                     'New streaming rail names collide with a retained native graph')
        path = self.directory/'data/gta_thaw/streamed-rails'/f'{report["basename"]}.qb.wpc'
        return dict(key=key, report=report, path=path, source=inputs['rails']['source'],
                    native_filename=path.relative_to(self.directory/'data').as_posix()[:-4], loaded=False)

    def _load_resource(self, resource):
        """Whole original memory asset loader; original parser owns its copy."""
        contents = memory_resource_input(resource)
        host.require(self.system.word(0x6dd3f4) == self.resource_manager and
                     self.system.word(0x6dd3f8) == 1, 'Original resource manager ownership changed')
        self.words(self.resource_manager, 15)
        transfer = self.system.allocate(contents)
        host.require(transfer % 4 == 0 and 0x10000 <= transfer < 0x100000000-len(contents),
                     'Streaming rail transfer allocation exceeds x86 range')
        # Preserve the sole data publication made by pinned NodeArrayManLoad.
        # This filename is also used by the stock reload command (59CFD0).
        filename = resource['native_filename'].encode('ascii')+b'\0'
        self.system.write(0x83f1b0, filename)
        value = self.call('NodeArrayMemoryLoad', (
            probe_gta_rails.rail_export.qbkey(resource['native_filename']),
            13, 0, transfer+0x94, len(resource['source']), 0, 1, transfer, 0), self.resource_manager)
        host.require(value and self.system.word(0x6dd3f4) == self.resource_manager and
                     self.system.word(0x6dd3f8) == 1 and
                     self.system.read(0x83f1b0, len(filename)) == filename,
                     'Original rail memory loader failed or changed ownership')
        asset, _ = self._resource_owner(resource, capture=True)
        fields = self.words(asset, 13)
        self.words(value, 1)
        host.require(fields[2] == fields[3] == value and fields[4] == 0 and
                     fields[6]&0xffffff == len(resource['source']) and
                     not transfer <= value < transfer+len(contents),
                     'Original rail memory loader did not retain its own copy')
        # Release only after the original call and ownership guards succeeded.
        # A timeout leaves this input owned until the poisoned child is stopped.
        host.success(self.free(self.process, transfer, 0, 0x8000), 'Release copied rail memory input')

    def _preload_call(self, name, resource):
        # Resource load/unload may register a NodeArray, never a native rail group.
        started_profile = time.perf_counter() if host.PROFILE_RESOURCE_CALLS else None
        before, groups = self._snapshot(), self.words(self.rail_manager, 41)
        snapshot_at = time.perf_counter() if started_profile is not None else None
        if name != 'NodeArrayManLoad':
            self.parameters(filename=resource['native_filename'])
        started = time.perf_counter()
        if name == 'NodeArrayManLoad':
            self._load_resource(resource)
            name = 'NodeArrayMemoryLoad'
        else:
            host.require(self.call(name, (self.params, 0)) & 255, 'Original speculative rail resource call failed')
        native_ms = (time.perf_counter()-started)*1000
        native_at = time.perf_counter() if started_profile is not None else None
        self._unchanged(before)
        host.require(self.words(self.rail_manager, 41) == groups,
                     'Speculative rail resource call changed the active RailManager')
        self.idle()
        self.log('world_rail_resource_call', operation=name, key=resource['key'],
                 native_ms=native_ms, nodes=resource['report']['node_count'])
        if started_profile is not None:
            self.log('world_rail_resource_timing', function=name, key=resource['key'],
                nodes=resource['report']['node_count'], snapshot_ms=(snapshot_at-started_profile)*1000,
                parameters_ms=(started-snapshot_at)*1000, native_ms=native_ms,
                guard_ms=(time.perf_counter()-native_at)*1000)

    def _resource_owner(self, resource, *, capture=False):
        """Bind a loaded resource once, then prove its original registration lives.

        NodeManager+E04 is the most recently loaded asset, not the owner of an
        arbitrary retained NodeArray. It is usable only immediately after Load.
        """
        report = resource['report']
        array = self.call('NodeArrayFindNamed', (report['array_checksum'],))
        host.require(array and self.words(array, 3)[:2] == (0xa0100, report['node_count']),
                     'Original streaming NodeArray registration differs')
        asset = self.system.word(self.node_manager+0xe04) if capture else resource.get('asset')
        host.require(type(asset) is int and (capture or resource.get('array') == array),
                     'Original streaming rail resource lost its retained array ownership')
        fields = self.words(asset, 0x34//4)
        host.require(fields[0] == 0x68f060 and fields[0x20//4] ==
                     probe_gta_rails.rail_export.qbkey(resource['native_filename']+'.Wpc') and
                     fields[0x24//4] == report['array_checksum'] and fields[0x28//4] == array and
                     fields[0x2c//4]&255 == 1, 'Original streaming rail resource identity differs')
        if capture:
            resource.update(asset=asset, array=array)
        return asset, array

    def prewarm(self, inputs):
        """One future QB resource phase, without parsing or activating rails.

        Call between complete source frames, only after update(current_inputs)
        reported ready. A different/absent forecast retires only this unused
        resource. update consumes it once the caller has published its exact COL.
        """
        host.require(not self.poisoned, 'Streaming rail manager was poisoned by a failed native operation')
        try:
            if self.stage is not None or self.pending is not None:
                return False
            if inputs is not self._forecast_inputs:
                self._forecast_key = (None if inputs is None else
                    self._verified_key if inputs is self._verified_inputs else verified_rail_identity(inputs))
                self._forecast_inputs = inputs
            key = self._forecast_key
            wanted = (key is not None and key != self.last_key and inputs['rails']['source'] is not None and
                      not any(item['key'] == key for item in self.groups))
            if self.prefetched is not None and (not wanted or self.prefetched['key'] != key):
                self.idle()
                old = self.prefetched
                if old['loaded']:
                    self._resource_owner(old)
                    self._preload_call('NodeArrayManUnload', old)
                self.prefetched = None
                self.log('world_rail_preload_discarded', key=old['key'], parsed=False)
                return False
            if not wanted:
                return False
            if self.prefetched is not None and self.prefetched['loaded']:
                return True
            # update(current_inputs) already checked the live VM this frame.
            # An unchanged/absent forecast has no native work to authorize;
            # repeat the guard here only before an actual resource operation.
            self.idle()
            if len(self.headers()) >= 9:
                return False
            if self.prefetched is None:
                self.prefetched = self._resource(inputs, key)
                return False
            if not self.prefetched['loaded']:
                self._preload_call('NodeArrayManLoad', self.prefetched)
                self.prefetched['loaded'] = True
                self.log('world_rail_resource_preloaded', key=key,
                         nodes=self.prefetched['report']['node_count'], parsed=False, active_rails_unchanged=True)
            return True
        except BaseException:
            self.poisoned = True
            raise

    def activate(self, item, active, names):
        pin = STREAM_PINS['RailManagerSetActive']
        host.require(hashlib.sha256(self.system.read(pin[1], pin[2])).hexdigest() == pin[3],
                     'Original rail activation method changed')
        worker = self.system.worker
        worker.check_call_access()
        code = activation_stub(names, active, self.rail_manager, self.record)
        self.system.write(self.record+36, bytes(36))
        self.system.write(self.record+452, bytes(24))
        address = worker.prepare_command(code)
        waited, returned = worker.execute(address, self.deadline)
        values = struct.unpack('<18I', self.system.read(self.record, 72))
        host.require(waited == 0 and returned == 0 and values[0] == 6,
                     f'Original rail activation failed: status={values[0]} EIP={values[10]:#x}')
        host.require(self.system.read(address, len(code)) == code, 'Owned rail activation ABI glue changed')
        self.log('world_rail_activation', group=item['group'], active=active, chain_heads=len(names),
                 original_method=pin[1], host_node_writes=False)

    def queue_activation(self):
        self.activation_queue = []
        for item in self.groups:
            desired = item is self.current
            if item['active'] == desired:
                continue
            heads = [row['name'] for row in item['report']['nodes'] if row['previous_index'] is None]
            host.require(heads, 'Original rail activation requires a non-cyclic chain head')
            for start in range(0, len(heads), 128):
                self.activation_queue.append((item, desired, heads[start:start+128], start+128 >= len(heads)))
        self.stage = 'activate' if self.activation_queue else None
        return self.status(not self.activation_queue)

    def update(self, inputs, last_sample=None):
        host.require(not self.poisoned, 'Streaming rail manager was poisoned by a failed native operation')
        try:
            started = time.perf_counter()
            # These phases do not advance gameplay. Waiting a render interval
            # between each tiny original call caused visible 100 ms freezes.
            for stages in range(1, MAX_UPDATE_STAGES+1):
                result = self._update(inputs, last_sample)
                elapsed = time.perf_counter()-started
                if result['ready'] or elapsed >= UPDATE_BUDGET_SECONDS:
                    break
            if stages > 1 or not result['ready']:
                self.log('world_rail_work_batch', stages=stages, milliseconds=elapsed*1000,
                         ready=result['ready'], stage=result['stage'])
            return result
        except BaseException:
            self.poisoned = True
            raise

    def _update(self, inputs, last_sample):
        self.idle()
        if self.stage == 'activate':
            item, active, names, final = self.activation_queue.pop(0)
            self.activate(item, active, names)
            if final:
                contents = self.system.read(item['group'], item['nodes']*0x60)
                host.require(all(bool(struct.unpack_from('<I', contents, at+0x40)[0]&4) == active
                                 for at in range(0, len(contents), 0x60)),
                             'Original rail activation did not update every selected chain')
                item['active'] = active
            if not self.activation_queue:
                self.stage = None
            return self.status(self.stage is None)
        if self.stage == 'configure':
            symbol = self.call('lookup', (probe_gta_rails.RAIL_FORMAT_KEY,))
            host.require(symbol >= 0x10000 and struct.unpack('<4I', self.system.read(symbol, 16)) ==
                         (0x10400, probe_gta_rails.RAIL_FORMAT_KEY,
                          probe_gta_rails.rail_export.qbkey('scripts/game/startup.qb.wpc'), 1),
                         'Original stock rail-format startup value/source differs')
            config = probe_gta_rails.dynamic_rail_config()
            self.call('ConfigLoad', (self.config+len(config), self.config, 0))
            host.require(self.call('lookup', (probe_gta_rails.RAIL_FORMAT_KEY,)) == self.config+28 and
                         self.system.word(self.config+40) == 0, 'Original rail NodeArray format was not selected')
            self.stage = None
            return self.status(False)
        if self.stage == 'unload':
            item = self.retirement_queue[0]
            # The source stayed paused since the read-only reference census.
            host.require(bool(retired_candidates([item], None, self.scene_native.references.blocks())),
                         'A native rail group acquired a reference before retirement')
            self._resource_owner(item)
            self.parameters(filename=item['native_filename'])
            host.require(self.call('NodeArrayManUnload', (self.params, 0)) & 255,
                         'Original rail NodeArray unload failed')
            self.stage = 'cleanup'
            return self.status(False)
        if self.stage == 'cleanup':
            item = self.retirement_queue.pop(0)
            self.call('RailOrphanCleanup', this=self.rail_manager)
            host.require(item['group'] not in {row['group'] for row in self.headers()},
                         'Original orphan cleanup did not release the unreferenced rail group')
            self.groups.remove(item)
            self.stage = 'unload' if self.retirement_queue else None
            self.log('world_rail_group_retired', group=item['group'], nodes=item['nodes'], original_cleanup=True)
            return self.status(False)
        if self.stage == 'load':
            self._preload_call('NodeArrayManLoad', self.pending)
            self.stage = 'parse'
            return self.status(False)
        if self.stage == 'parse':
            self._resource_owner(self.pending)
            self.parameters(array=self.pending['report']['array_checksum'])
            host.require(self.call('ParseNodeArray', (self.params, 0)) & 255,
                         'Original streaming rail NodeArray parser failed')
            self.stage = 'verify'
            return self.status(False)
        if self.stage == 'verify':
            report, before = self.pending['report'], self.pending['before_groups']
            added = [row for row in self.headers() if row['group'] not in before]
            host.require(len(added) == 1 and added[0]['nodes'] == report['node_count'],
                         'Original parser did not add the complete selected rail group')
            native = added[0]
            probe_gta_rails.validate_graph(self.system.read(native['group'], native['nodes']*0x60),
                                           native['group'], report['nodes'])
            self._resource_owner(self.pending)
            self._unchanged(self.pending)
            self.current = dict(self.pending, **native, rail_manager=self.rail_manager,
                                active=True, nodes_by_name={row['name']: row for row in report['nodes']})
            self.groups.append(self.current)
            self.last_key, self.pending, self.stage = self.current['key'], None, None
            self.log('world_rails_installed', nodes=self.current['nodes'], group=self.current['group'],
                     rail_manager=self.rail_manager, key=self.last_key, global_cache=True, original_parser=True)
            return self.queue_activation()
        # Preserve exact original graph and its references throughout a grind.
        if last_sample is not None and classify(last_sample) == 'rail':
            self.observe(last_sample)
            return self.status(True)
        if inputs is not self._verified_inputs:
            # The exact retained worker result was fully checked before its
            # resource preload. Keep the same object-identity cache contract as
            # repeated update/prewarm calls; new objects still verify all bytes.
            self._verified_key = (self._forecast_key if inputs is self._forecast_inputs else
                                  verified_rail_identity(inputs))
            self._verified_inputs = inputs
        key = self._verified_key
        if key == self.last_key:
            return self.status(True)
        existing = next((row for row in self.groups if row['key'] == key), None)
        if existing is not None:
            self._resource_owner(existing)
            self.current, self.last_key = existing, key
            return self.queue_activation()
        self.retirement_queue = retired_candidates(self.groups,
            self.current['key'] if self.current else None, self.scene_native.references.blocks())
        if self.retirement_queue:
            self.stage = 'unload'
            return self.status(False)
        if inputs['rails']['source'] is None:
            self.current, self.last_key = None, key
            return self.queue_activation()
        # Leave one native slot available; retaining borrowed graphs is safer
        # than freeing a node still used by original gameplay.
        if len(self.headers()) >= 9:
            self.log('world_rails_waiting_references', groups=len(self.groups), desired_key=key)
            return self.status(True)
        if self.prefetched is not None and self.prefetched['key'] == key:
            self.pending, self.prefetched = self.prefetched, None
        else:
            self.pending = self._resource(inputs, key)
        self.pending.update(self._snapshot(), before_groups={row['group'] for row in self.headers()})
        self.stage = 'parse' if self.pending['loaded'] else 'load'
        return self.status(False)

    def observe(self, sample):
        if classify(sample) != 'rail':
            return dict(contact=classify(sample))
        if self.dynamic_rails is not None:
            # The owner verifies the original manager and its local node bytes.
            result = self.dynamic_rails.observe_rail(sample)
            if result is not None:
                return result
        pointer, _ = struct.unpack('<2I', self.system.read(self.context['components'][0x5bd63b29]+0x3f0, 8))
        group = next((row for row in self.groups if row['group'] <= pointer < row['group']+row['nodes']*0x60), None)
        host.require(group is not None, 'Original grind points to an unowned streaming rail graph')
        return observe(sample, self.context, self.system, group)

    def adjacent_node(self, observation, pointer):
        """Read one linked static node in the same verified native allocation."""
        host.require(not observation.get('dynamic_vehicle'), 'Vehicle rails are not static world nodes')
        group = next((row for row in self.groups if
                      row['group'] <= observation['rail_node'] < row['group']+row['nodes']*0x60), None)
        host.require(group is not None, 'Correction rail group is no longer owned')
        result = _observe_node(self.system, group, pointer, observation['rail_manager'], geometry=True)
        host.require(result['source_chain'] == observation['source_chain'],
                     'Correction continuation belongs to another source chain')
        return result


def install(api, process, record, deadline, log, inputs, scene, context, system, *, preloaded):
    va, size, digest = CORE_RAIL_PIN
    host.require(hashlib.sha256(system.read(va, size)).hexdigest() == digest,
                 'Original Core rail-reference layout evidence differs')
    rails = probe_gta_rails.run(api, process, record, deadline, log, inputs, scene,
                               context, system, preloaded=preloaded)
    particles = probe_particles.run(api, process, record, deadline, log, context,
                                    system, preloaded=preloaded)
    nodes = {row['name']: row for row in inputs['report']['nodes']}
    host.require(len(nodes) == rails['nodes'], 'Prepared rail names are not unique')
    log('world_rails_installed', nodes=rails['nodes'], links=rails['links'],
        rail_manager=rails['rail_manager'], group=rails['group'], particle_manager=particles,
        scene_col_sha256=scene['col_sha256'], streaming_verified=False)
    return dict(rails, nodes_by_name=nodes, particles=particles, col_sha256=scene['col_sha256'])


def observe(sample, context, system, installed):
    """Read-only rail identity after a completed frame, without advancing it."""
    contact = classify(sample)
    if contact != 'rail':
        return dict(contact=contact)
    core = context['components'][0x5bd63b29]
    pointer, manager = struct.unpack('<2I', system.read(core+0x3f0, 8))
    return _observe_node(system, installed, pointer, manager)


def _observe_node(system, installed, pointer, manager, *, geometry=False):
    offset = pointer-installed['group']
    host.require(manager == installed['rail_manager'] and
                 0 <= offset < installed['nodes']*0x60 and offset % 0x60 == 0,
                 'Original OnRail references an unowned rail group')
    row = system.read(pointer, 0x60)
    name = struct.unpack_from('<I', row, 0x44)[0]
    reference = installed['nodes_by_name'].get(name)
    host.require(reference is not None and row[0x20:0x2c] == struct.pack('<3f', *reference['position_thaw']),
                 'Original OnRail node does not match the installed GTA region')
    result = dict(contact='rail', rail_node=pointer, native_node_index=offset//0x60,
                  rail_name=name, source_chain=reference['source_chain'], rail_manager=manager)
    if geometry:
        result.update(position=struct.unpack_from('<3f', row, 0x20), links=struct.unpack_from('<2I', row, 0x50))
    return result
