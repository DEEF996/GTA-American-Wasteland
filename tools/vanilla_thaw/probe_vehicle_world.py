"""Original movable collision bindings for bounded, owned stock-host proofs.

This module is not connected to the live adapter until collision, contact and
teardown proofs pass. Shapes stay in their original private COL loader scene;
only native movable objects enter the original moving-object list.
"""
import hashlib
import math
from pathlib import Path
import json
import struct
import ctypes
import time
from collections import OrderedDict

import probe_loader as host
import probe_scene
import probe_gta_rails
import probe_world_rails
import probe_scene_stream
import probe_vm_idle
from probe_scene_references import SceneReferences, reference_words
from assets_export import parse_col
from probe_vehicle_nodes import compile_nodes
from probe_frame_input import BUTTONS, INPUT_KEY, button_state
from probe_vehicle_transform import apply as apply_transform

METADATA_SHA = '5b30b9f87f3031ee33bdfddba01d0bd42943b8016ee70230ff6649ee04cb194d'
IDENTITY = (1.,0.,0.,0., 0.,1.,0.,0., 0.,0.,1.,0., 0.,0.,0.,1.)
MAX_SHAPE_OBJECTS, MAX_SHAPE_BYTES = 1024, 4*1024*1024
MOTION_DIAGNOSTIC_PINS=(
    ('MotionContactFactory',0x45bd50,30,'ef5e7a2cd048920779a339c9ce919674b8705f307cfdd49e5c8917127492dafc'),
    ('MotionContactCtor',0x5ad9b0,159,'f1a1c4db4db63c856846d9e807c79c66eccb0149f1ea440056329a69603c3e11'),
    ('MotionContactAllocation',0x5c292d,20,'5729682403f029058202059333ac46510e613ddbd9a92ff7141a7086f4e98da4'),
)
SKITCH_INPUT_PINS = (
    ('SkitchButtonUpdate',0x593810,145,'20df2fe3d20da9ba1aff6ac4eee5f5873d3152092dcfb0aa3a864a854beaaa06'),
    ('SkitchClearPress',0x593940,5,'1f4ce9035447f37d443397d5f84bfbae7182064d15824ad8fdd06552c364b202'),
    ('SkitchClearRelease',0x593950,8,'06e06b9002f70c15a151d9dd60ed0d8fe00151ecf1b3a3672fc55d28a4f04225'),
    ('SkitchTenseReleaseEvidence',0x5c9210,126,'19a62c480488eea978f86d7e110ce9457b010d30aa1b0c996122689b889b68bf'),
    ('SkitchTenseSetEvidence',0x5c93f0,69,'ed4e5eb834e69e54975ac4f5dd04bc5641528ff7f98760805af1412948bfe569'),
    ('SkitchInputClock',0x436230,48,'7c5eb364ca5e3d008446aa89b5b4e9e09f141d50787e9be69c6828da6db488fa'),
    ('SkitchTrickButton',0x46f640,184,'8988bea71c6fef7b14e8ff3409aa055b05ac06ec2dbb2dfa69c891a6c2a9b31e'),
    ('SkitchClearButtonEvents',0x46f2b0,6,'40c89896b58841b092c50f406177f36a06c33e15759b568ac895e017d1379e79'),
    ('SkitchClearButtonCaller',0x472bd0,139,'ad0000a1f4fcb000f98e06b7bc5ce933cefa61acf8f09824c2eb5e6cfb669796'),
    ('SkitchClearButtonDescriptor',0x6c0168,8,'557afa8a273f67fa93b5a5a42948e567d528fcab96881f2d8ae78d6ae4c9d00c'),
    ('SkitchClearButtonRegistration',0x688935,10,'02fdf633403bf473f03b165384f1132c8747ffee31168648996b79860debaf81'),
    ('SkitchInputEvents',0x44ff60,793,'e0e2ebf2595a46ddcba48931259bc32db3c56b378c01c4cd12a73c079cbb7222'),
    ('SkitchInputEdge',0x44feb0,175,'6429d460697e652b6054142c31c4c947a65d45f1e512aa61c8d44ac8ba85ab8c'),
    ('SkitchTrickButtonMapping',0x6bdd68,152,'a543ba2cc7f26b1e5585274baa7a32f06591e6e4403522681e18f8956ebb46fc'),
)


class VehicleShapeTooLarge(ValueError):
    """Verified geometry exceeds this adapter's tested per-shape budget."""
    def __init__(self, count, size):
        self.count,self.size=count,size
        super().__init__(f'Vehicle COL has {count} objects/{size} bytes; limits are '
                         f'{MAX_SHAPE_OBJECTS} objects/{MAX_SHAPE_BYTES} bytes')


class VehicleShapeCapacity(ValueError):
    """Referenced native shapes currently occupy every bounded cache slot."""


def validate_shape_payload(col,verified=None):
    key=hashlib.sha256(col).hexdigest()
    if verified is None:
        count=parse_col(col)['stats']['objects']
    else:
        host.require(verified['format']=='gta-thaw-vehicle-local-collision-v1' and
                     verified['col_sha256']==key and verified['stats']['col_bytes']==len(col),
                     'Prepared vehicle COL content differs')
        version,count=struct.unpack_from('<2I',col)
        host.require(version==10 and count==verified['stats']['col_objects'], 'Prepared vehicle COL header differs')
    host.require(count>0,'Vehicle collision shape is empty')
    if count>MAX_SHAPE_OBJECTS or len(col)>MAX_SHAPE_BYTES:
        raise VehicleShapeTooLarge(count,len(col))
    return key,count


def validate_transform(position, matrix, velocity):
    host.require(len(position) == len(velocity) == 3 and len(matrix) == 16 and
                 all(math.isfinite(v) and abs(v) < 10000000 for v in (*position, *matrix, *velocity)),
                 'Invalid dynamic vehicle transform')
    axes = [matrix[start:start+3] for start in (0,4,8)]
    host.require(all(abs(sum(x*x for x in axis)-1) < .002 for axis in axes) and
                 all(abs(sum(axes[a][i]*axes[b][i] for i in range(3))) < .002
                     for a,b in ((0,1),(0,2),(1,2))) and
                 tuple(matrix[i] for i in (3,7,11,12,13,14,15)) == (0.,0.,0.,0.,0.,0.,1.),
                 'Vehicle matrix must be orthonormal with separate position')
    a,b,c=axes
    det=a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
    host.require(abs(det-1) < .002, 'Vehicle matrix reflects the source geometry')


class VehicleWorld:
    def __init__(self, api, process, record, deadline, log, system, *, context=None, vm_sources=None):
        raw = Path(__file__).with_name('vehicle_native_pins.json').read_bytes()
        host.require(hashlib.sha256(raw).hexdigest() == METADATA_SHA, 'Vehicle native metadata differs')
        metadata = json.loads(raw)
        host.require(metadata['stock_exe_sha256'] == host.FILES['THAW.exe'][1], 'Vehicle image differs')
        self.pins = {row[0]: row for row in metadata['functions']}
        self.pins.update({row[0]: row for row in probe_scene.PINS.values()})
        self.pins.update(probe_gta_rails.PINS)
        self.pins.update(probe_world_rails.STREAM_PINS)
        self.pins.update(probe_scene_stream.PINS)
        self.pins['SourceUnload']=host.QB_PINS['unload']
        self.pins.update({row[0]:row for row in SKITCH_INPUT_PINS})
        self.api,self.process,self.record,self.deadline,self.log,self.system = api,process,record,deadline,log,system
        self.read,self.word,self.write,self.allocate = system.read,system.word,system.write,system.allocate
        self.context,self.vm_sources=context,vm_sources
        self.free=api.VirtualFreeEx
        self.free.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32]
        self.free.restype=ctypes.c_int
        for name,address,length,digest in self.pins.values():
            host.require(hashlib.sha256(self.read(address,length)).hexdigest() == digest,
                         f'Vehicle native pin differs: {name}')
        self.shapes,self.vehicles = {},{}
        self.scratch = self.allocate(bytes(256))
        self.feeler = self.allocate(bytes(0xc0))
        host.require(self.call('FeelerConstructor',this=self.feeler) == self.feeler, 'Vehicle proof feeler differs')
        self.params = self.allocate(bytes(8))
        host.require(self.call('NativeCStructCtor', this=self.params) == self.params, 'Vehicle native params differ')
        self.node_shapes = {}
        self.aliases={}
        self.generation=None
        self.poisoned=False
        self.serial=0
        self.cache_dirty=False
        self.skitch_input_latched=False
        self.shape_rejections=OrderedDict()
        self.references=SceneReferences(self.read,self.word,(self.word(0x84376c),self.word(0x843770)),context) if context else None
        self.off_params=self.allocate(bytes(8))
        self.call('NativeCStructCtor',this=self.off_params)
        self.call('NativeAddChecksum',(0,probe_gta_rails.rail_export.qbkey('off')),self.off_params)

    def idle(self):
        selected=self.word(probe_scene_stream.DEFAULT_CACHE)
        mask=self.word(probe_scene_stream.CACHE_MASK)
        host.require(mask&~255==0,'Vehicle collision cache registry differs')
        registered=not selected or any(mask&(1<<slot) and
            self.word(probe_scene_stream.CACHE_SLOTS+slot*4)==selected for slot in range(8))
        host.require(not self.poisoned and registered and self.word(0x6dd4a4)&255==0,
                     'Vehicle operation requires a restored original frame')
        if self.context:
            probe_vm_idle.require_idle(self.system,self.context,self.vm_sources)

    def load_nodes(self, shape, rails, hooks):
        host.require(shape in self.shapes and shape not in self.node_shapes, 'Vehicle nodes already loaded or missing COL')
        scalar=self.call('lookup',(probe_gta_rails.RAIL_FORMAT_KEY,))
        if not scalar or self.word(scalar+12)!=0:
            config = probe_gta_rails.dynamic_rail_config()
            buffer = self.allocate(config+probe_gta_rails.CONFIG_NAME.encode('ascii')+b'\0')
            self.call('ConfigLoad', (buffer+len(config), buffer, 0))
        basename = 'gta_vehicle_'+shape[:16]
        source = compile_nodes(basename, rails, hooks)
        filename = 'models/'+basename+'.qb'
        buffer = self.allocate(source+filename.encode('ascii')+b'\0')
        self.call('ConfigLoad', (buffer+len(source), buffer, 0))
        manager = self.word(0x83e87c)
        if not manager:
            manager = self.call('NodeManagerAcquire', (1,))
        array = probe_gta_rails.rail_export.qbkey(basename+'_NodeArray')
        host.require(sum(self.word(manager+i*32+4) != 0 for i in range(48)) < 48,
                     'Original NodeManager capacity exhausted')
        # Local model arrays are not globally parsed level objects. The zero
        # flag avoids the original global ObjectHook singleton refresh.
        host.require(self.call('NodeManagerRegisterArray', (array, 0, 0, 0), manager)&255,
                     'Original vehicle NodeArray registration failed')
        slot = next(manager+i*32 for i in range(48) if self.word(manager+i*32)==array)
        pointer = self.word(slot+4)
        host.require(self.word(pointer+4)==len(rails)+len(hooks), 'Vehicle native NodeArray extent differs')
        self.node_shapes[shape] = dict(basename=basename, source=buffer, filename=filename,
                                      array=array, pointer=pointer, rails=len(rails), hooks=len(hooks),rail_rows=rails,
                                      hook_positions=tuple(tuple(point) for point in hooks))
        self.log('vehicle_nodes_loaded', shape=shape, **self.node_shapes[shape])

    def attach_components(self, identity, *, skitch=True):
        value=self.vehicles[identity]
        nodes=self.node_shapes[value['shape']]
        owner=value['owner']
        self.call('StructClear', this=self.params)
        filename=self.allocate((nodes['basename']+'.mdl').encode('ascii')+b'\0')
        self.call('NativeAddString', (0x286a8d26, filename), self.params)
        host.success(self.free(self.process,filename,0,0x8000),'Release copied vehicle model name')
        components={}
        for kind, crc in [('NodeArray',0xc472ecc5)]+([('RailComponent',0x1cdfb2a3)] if nodes['rails'] else [])+(
                [('ObjectHook',0x27443708),('Skitch',0x3506ce64)] if nodes['hooks'] and skitch else []):
            component=self.call(kind+'Factory')
            host.require(component and self.word(component+4)==crc, 'Original vehicle component type differs')
            self.call('CompositeAppend', (component,), owner)
            self.call(kind+'Init', (self.params,), component)
            components[kind]=component
        host.require(self.word(components['NodeArray']+0x18)==nodes['pointer'], 'Vehicle component resolved another local array')
        value['components']=components
        linked={row['next_index'] for row in nodes['rail_rows'] if row['next_index'] is not None}
        value['rail_roots']=[row['name'] for i,row in enumerate(nodes['rail_rows']) if i not in linked]
        if 'RailComponent' in components:
            manager=self.word(components['RailComponent']+0x18)
            header=struct.unpack('<5I',self.read(manager,20))
            host.require(header[0]==1 and header[2]==header[3]==nodes['rails'] and header[4]==0,
                         'Original local vehicle rail group differs')
            probe_gta_rails.validate_graph(self.read(header[1],nodes['rails']*0x60),header[1],nodes['rail_rows'])
            value['rails']=dict(rail_manager=manager,group=header[1],nodes=nodes['rails'],
                               nodes_by_name={r['name']:r for r in nodes['rail_rows']})
        self.log('vehicle_components_attached', identity=identity, components=components,
                 hook_manager=self.word(components['ObjectHook']+0x18) if 'ObjectHook' in components else None,
                 rail_manager=self.word(components['RailComponent']+0x18) if 'RailComponent' in components else None)
        return components

    def observe_rail(self,sample):
        if not self.context or probe_world_rails.classify(sample)!='rail':return None
        manager=self.word(self.context['components'][0x5bd63b29]+0x3f4)
        for identity,value in self.vehicles.items():
            if value.get('rails',{}).get('rail_manager')==manager:
                host.require(self.word(value['owner'])==0x68f7b8,'Dynamic rail owner class changed')
                result=probe_world_rails.observe(sample,self.context,self.system,value['rails'])
                return dict(result,dynamic_vehicle=True,vehicle_id=value.get('external_id',identity),vehicle_owner=value['owner'])
        return None

    def observe_skitch(self,sample=None):
        if not self.context:return None
        state=self.context['components'][0x43686585]
        if not self.read(state+0x98,1)[0]:return None
        pointer=self.word(self.context['components'][0x5bd63b29]+0x6fc)
        matches=[(i,v) for i,v in self.vehicles.items() if v['owner']==pointer]
        host.require(len(matches)==1 and self.word(pointer)==0x68f7b8,
                     'Original skitch references an unowned vehicle')
        identity,value=matches[0]
        host.require('Skitch' in value.get('components',{}),'Original skitch owner lacks its genuine component')
        return dict(contact='skitch',dynamic_vehicle=True,vehicle_id=value.get('external_id',identity),
                    vehicle_owner=pointer,vehicle_position=list(struct.unpack('<3f',self.read(pointer+0x68,12))),
                    vehicle_velocity=list(struct.unpack('<3f',self.read(pointer+0xc8,12))))

    def diagnostic_snapshot(self):
        """Capture owned motion data only on a rare rejected displacement."""
        self.idle()
        host.require(self.context is not None and self.references is not None,
                     'Vehicle diagnostic requires the owned skater context')
        arena=self.references.arena
        for name,address,size,digest in MOTION_DIAGNOSTIC_PINS:
            host.require(hashlib.sha256(self.read(address,size)).hexdigest()==digest,
                         'Vehicle diagnostic original layout differs: '+name)
        def block(pointer,size):
            host.require(type(pointer) is int and pointer%4==0 and
                         arena[0]<=pointer<=arena[1]-size,
                         'Vehicle diagnostic leaves the original owned arena')
            return self.read(pointer,size)
        actor=self.context['actor']
        component=self.context['components'][0x408d2aa9]
        raw=block(component,0x74)
        host.require(struct.unpack_from('<2I',raw)==(0x6904a8,0x408d2aa9) and
                     struct.unpack_from('<I',raw,12)[0]==actor and
                     self.word(self.context['components'][0x5bd63b29]+0x1930)==component,
                     'Vehicle diagnostic motion component ownership differs')
        result=dict(motion_component=component,motion_component_hex=raw.hex(),vehicles=[])
        contact=struct.unpack_from('<I',raw,0x18)[0]
        result['motion_contact']=contact
        if contact:
            # The genuine component owns this 0xb0 contact allocation. Its
            # first word is an intrusive reference, not another vtable.
            contents=block(contact,0xb0)
            owner=struct.unpack_from('<I',contents)[0]
            matches=[v for v in self.vehicles.values() if v['owner']==owner]
            host.require(len(matches)<=1,'Vehicle diagnostic has duplicate original owners')
            result.update(motion_contact_hex=contents.hex(),contact_owner=owner,
                          contact_owned=bool(matches),contact_vehicle_id=matches[0].get('external_id') if matches else None)
        host.require(len(self.vehicles)<=24,'Vehicle diagnostic owner count differs')
        for identity,value in self.vehicles.items():
            owner=value['owner']
            data=block(owner,0x124)
            host.require(struct.unpack_from('<I',data)[0]==0x68f7b8,
                         'Vehicle diagnostic owner class differs')
            result['vehicles'].append(dict(identity=identity,vehicle_id=value.get('external_id'),
                owner=owner,collider=value['collider'],parked=value['parked'],retired=value['retired'],
                last_seen=value.get('last_seen'),shape=value['shape'],
                position=list(struct.unpack_from('<4f',data,0x68)),
                previous_position=list(struct.unpack_from('<4f',data,0x78)),
                matrix=list(struct.unpack_from('<16f',data,0x88)),
                velocity=list(struct.unpack_from('<4f',data,0xc8)),
                display_matrix=list(struct.unpack_from('<16f',data,0xdc))))
        return result

    def _set_skitch_enabled(self, value, enabled):
        component=value.get('components',{}).get('Skitch')
        if component and value.get('skitch_enabled') is not enabled:
            self.call('SkitchEnable',(self.params if enabled else self.off_params,0),component)
            value['skitch_enabled']=enabled

    def _consume_skitch_buttons(self):
        """Consume this gesture's X/Triangle charge and edges exactly once.

        CPad, Core TENSE and Trick each retain separate input state. The stock
        TrickButton method synchronizes its held state before ClearEventBuffer
        consumes only that button's history, including the newest circular
        record. No ordinary trick queue or other button history is cleared.
        """
        self.idle()
        actor=self.context['actor']
        component=self.context['components'][INPUT_KEY]
        host.require(self.word(component+4)==INPUT_KEY and self.word(component+12)==actor and
                     self.word(actor+0x29c)==component,'Skitch input component ownership differs')
        state=self.context['components'][0x43686585]
        core=self.context['components'][0x5bd63b29]
        trick=self.context['components'][0x270f56e1]
        host.require(self.word(state+4)==0x43686585 and self.word(state+12)==actor and
                     self.word(core+0x192c)==state,'Skitch TENSE state ownership differs')
        host.require(self.word(trick+4)==0x270f56e1 and self.word(trick+12)==actor and
                     self.word(trick+0x20)==component and
                     struct.unpack('<H',self.read(trick+0x99c,2))[0]<=100 and
                     struct.unpack('<H',self.read(trick+0x99e,2))[0]<100 and
                     all(value in (0,1) for value in self.read(trick+0xa44+11,2)),
                     'Skitch trick input ownership or history differs')
        before=self.read(state,0x140)
        host.require(before[0x20] in (0,1),'Original TENSE latch is invalid')
        for name in ('X','TRIANGLE'):
            _,_,offset,checksum=BUTTONS[name]
            pointer=component+0x20+offset
            button=button_state(self.read(pointer,32),checksum)
            if button['held'] or button['press_edge'] or button['release_edge']:
                self.call('SkitchButtonUpdate',(0,),pointer)
                self.call('SkitchClearPress',this=pointer)
                self.call('SkitchClearRelease',this=pointer)
                after=button_state(self.read(pointer,32),checksum)
                host.require(not any(after[key] for key in ('held','pressure','press_edge','release_edge')),
                             'Original skitch chord button consumption failed')
        if before[0x20]:
            # Core5C9210 emits Ollied from TENSE even when CPad release is clear.
            # This input remap consumes only that charge, using the same stock
            # timestamp expression, without emitting an unwanted jump event.
            tense=bytearray(before[0x20:0x28]);tense[0]=0
            struct.pack_into('<I',tense,4,(self.call('SkitchInputClock')+self.word(0x6dd8d0))&0xffffffff)
            self.write(state+0x20,bytes(tense))
            host.require(self.read(state,0x140)==before[:0x20]+bytes(tense)+before[0x28:],
                         'Skitch chord changed unrelated state or TENSE padding')
            self.log('skitch_chord_charge_consumed',original_button_methods=True,
                     input_latch='TENSE',original_timestamp=True,ollied_event=False)
        # PacketEvents copies +1C into +18 before evaluating raw release_x /
        # release_triangle. Consume only these claimed bits; the next poll
        # owns the normal history copy and all other buttons/events.
        mask=self.word(component+0x1c)
        if mask&0x500:
            self.write(component+0x1c,struct.pack('<I',mask&~0x500))
            host.require(self.word(component+0x1c)==mask&~0x500,
                         'Skitch chord input mask consumption failed')
        for index,checksum in ((11,0x7323e97c),(12,0x20689278)):
            self.call('SkitchTrickButton',(index,0),trick)
            self.call('SkitchClearButtonEvents',(checksum,0),trick)
            host.require(self.read(trick+0xa44+index,1)==b'\0',
                         'Original trick button did not consume the chord edge')

    def set_skitch_input(self, requested):
        """Change original vehicle eligibility, never the skater's W/UP state.

        Stock check_for_skitch checks this component flag before its unchanged
        nearest-hook/range/hold-time solver. An existing grip keeps its owner
        enabled after the chord is released; original S/balance decides exit.
        """
        host.require(type(requested) is bool,'Skitch input must be boolean')
        if not self.context:return
        if requested and not self.skitch_input_latched:self._consume_skitch_buttons()
        self.skitch_input_latched=requested
        attached=self.observe_skitch()
        owner=attached['vehicle_owner'] if attached else None
        position=struct.unpack('<3f',self.read(self.context['actor']+0x68,12)) if requested else None
        if position:
            host.require(all(math.isfinite(x) for x in position),'Skitch actor position is invalid')
        for value in self.vehicles.values():
            enabled=False
            if not value['parked'] and not value['retired'] and value['skitch_allowed']:
                if value['owner']==owner:
                    enabled=True
                elif requested:
                    hooks=self.node_shapes[value['shape']]['hook_positions'] if value['shape'] in self.node_shapes else ()
                    if hooks:
                        center=struct.unpack('<4f',value['published_position'])[:3]
                        forward=struct.unpack('<16f',value['published_matrix'])[8:11]
                        local_z=sum((position[i]-center[i])*forward[i] for i in range(3))
                        # Compensated vehicle basis: forward +Z, rear hooks -Z.
                        enabled=local_z<=max(point[2] for point in hooks)
            self._set_skitch_enabled(value,enabled)

    def closest_hook(self, identity, position):
        value=self.vehicles[identity]
        self.write(self.scratch, struct.pack('<4f', *position, 1.))
        index=self.call('SkitchClosestHook',(self.scratch+16,self.scratch),value['components']['Skitch'])
        return dict(index=index,position=struct.unpack('<3f',self.read(self.scratch+16,12)))

    def call(self, name, args=(), this=None):
        return host.native_call(self.api,self.process,self.pins[name],list(args),
                                self.record,self.deadline,self.log,this)

    def transform(self, prefix, owner, collider):
        apply_transform(self,prefix,owner,collider)

    def load_shape(self, col, *, verified=None):
        key=hashlib.sha256(col).hexdigest()
        if key in self.shapes:return key
        _,count=validate_shape_payload(col,verified)
        if len(self.shapes)>=24:raise VehicleShapeCapacity('Vehicle native shape cache has no free slot')
        scene,buffer=self.allocate(bytes(0xa0)),self.allocate(col)
        host.require(self.call('PCSceneConstructor',(8,),scene) == scene and self.word(scene)==0x69520c,
                     'Vehicle private collision scene differs')
        # Scene stays detached from world lookup. Its loader resolves original
        # COL-v10 pointers used by the native movable geometry constructors.
        host.require(self.call('LoadCOLMemory',(buffer,0),scene)&255 and self.word(scene+0x68)==count,
                     'Vehicle COL loading failed')
        self.shapes[key]=dict(scene=scene,col=buffer,headers=buffer+80,count=count,bytes=len(col),last_used=self.serial)
        self.log('vehicle_shape_loaded',key=key,**self.shapes[key],detached_scene=True)
        return key

    def create(self, identity, shape, position=(0.,0.,0.), matrix=IDENTITY, velocity=(0.,0.,0.)):
        validate_transform(position,matrix,velocity)
        host.require(identity not in self.vehicles and len(self.vehicles)<24 and shape in self.shapes,
                     'Vehicle identity/shape ownership differs')
        source=self.shapes[shape]
        owner=self.call('CompositeCreate',this=self.word(0x6dd6b0))
        host.require(self.word(owner)==0x68f7b8, 'Original vehicle composite was not created')
        collider=self.call('MovingFactory',(4,source['headers'],source['count'],owner))
        expected=0x68f6e8 if source['count']==1 else 0x68f480
        host.require(self.word(collider)==expected, 'Original movable collider class differs')
        result=dict(owner=owner,collider=collider,shape=shape,group=source['count']>1,
                    parked=False,retired=False,skitch_allowed=True)
        self.vehicles[identity]=result
        self.update(identity,position,matrix,velocity)
        self.log('vehicle_native_created',identity=identity,**{key:result[key] for key in
            ('owner','collider','shape','group','parked','retired','skitch_allowed')})
        return result

    def update(self, identity, position, matrix=IDENTITY, velocity=(0.,0.,0.)):
        validate_transform(position,matrix,velocity)
        value=self.vehicles[identity]
        owner,collider=value['owner'],value['collider']
        host.require(self.word(owner)==0x68f7b8 and
                     self.word(collider)==(0x68f480 if value['group'] else 0x68f6e8),
                     'Vehicle native objects changed')
        position_bytes=struct.pack('<4f',*position,1.)
        matrix_bytes=struct.pack('<16f',*matrix)
        velocity_bytes=struct.pack('<4f',*velocity,1.)
        previous=value.get('published_position')
        moved=previous!=position_bytes
        rotated=value.get('published_matrix')!=matrix_bytes
        # External game transforms are data for our owned original object. The
        # genuine methods below update the native collider/inverse/bounds cache.
        if previous is None:previous=self.read(owner+0x68,16)
        # The first stationary tick after movement must still catch up the
        # original previous position; otherwise contacts could replay a delta.
        if value.get('previous_position')!=previous:
            self.write(owner+0x78,previous)
            value['previous_position']=previous
        if moved:self.write(owner+0x68,position_bytes)
        if rotated:
            self.write(owner+0x88,matrix_bytes)
            self.write(owner+0xdc,matrix_bytes)
        if value.get('published_velocity')!=velocity_bytes:self.write(owner+0xc8,velocity_bytes)
        prefix='Group' if value['group'] else 'Mesh'
        if moved and rotated:self.transform(prefix,owner,collider)
        elif moved:self.call(prefix+'SetPosition',(owner+0x68,),collider)
        elif rotated:self.call(prefix+'SetOrientation',(owner+0x88,),collider)
        if moved or rotated:self.cache_dirty=True
        value.update(published_position=position_bytes,published_matrix=matrix_bytes,published_velocity=velocity_bytes)
        self.shapes[value['shape']]['last_used']=self.serial

    def query(self,start,end):
        host.require(all(math.isfinite(x) for x in (*start,*end)) and len(start)==len(end)==3,
                     'Invalid vehicle query ray')
        if self.cache_dirty:self.rebuild_caches()
        self.write(self.feeler,struct.pack('<8f',*start,1.,*end,1.))
        # First argument enables movable queries (5AE070 reads it while the
        # preceding static-query arguments are still on the native stack).
        hit=bool(self.call('FeelerQuery',(1,0,0),self.feeler)&255)
        raw=self.read(self.feeler,0xc0)
        return dict(hit=hit,position=struct.unpack_from('<3f',raw,0x34),
                    normal=struct.unpack_from('<3f',raw,0x24),
                    reference=struct.unpack_from('<I',raw,0x98)[0],
                    owner=struct.unpack_from('<I',raw,0xa4)[0])

    def remove(self, identity, *, _references=None):
        value=self.vehicles[identity]
        # Explicit original unregister, then original deleting destructors.
        # Live integration must release attached gameplay before entering here.
        if not value['parked']:
            self.set_active(identity,False)
        host.require(not (self.actor_references(identity) if _references is None else _references),
                     'Vehicle removal still has original gameplay references')
        self.call(('Group' if value['group'] else 'Mesh')+'Delete',(1,),value['collider'])
        self.call('CompositeDelete',(1,),value['owner'])
        del self.vehicles[identity]
        self.log('vehicle_native_removed',identity=identity,original_destructors=True)

    def set_active(self,identity,active):
        value=self.vehicles[identity]
        if value['parked']==(not active):return
        if active:
            self.call('MovingRegister',(value['collider'],value['owner']))
        else:
            self.call('MovingUnregister',(value['collider'],))
        components=value.get('components',{})
        if 'Skitch' in components:
            self._set_skitch_enabled(value,bool(active and value['skitch_allowed']))
        if 'rails' in value:
            for name in value['rail_roots']:
                self.call('RailManagerSetActive',(name,int(active),1),value['rails']['rail_manager'])
        value['parked']=not active
        self.rebuild_caches()
        if not active:
            # An expired/despawned carrier uses the stock clean skitch exit.
            # Retain the carrier until that original exception finishes.
            if self.context:
                core=self.context['components'][0x5bd63b29]
                state=self.context['components'][0x43686585]
                if self.word(state+0x98)&255 and self.word(core+0x6fc)==value['owner']:
                    self.call('OwnerSelfEvent',(probe_gta_rails.rail_export.qbkey('offmetertop'),0),self.context['actor'])
                    self.log('vehicle_contact_release',identity=identity,original_event='offmetertop',original_handler='SkitchOut')

    def rebuild_caches(self):
        mask=self.word(probe_scene_stream.CACHE_MASK)
        host.require(mask&~255==0,'Vehicle collision cache registry differs')
        for slot in range(8):
            if mask&(1<<slot):
                pointer=self.word(probe_scene_stream.CACHE_SLOTS+slot*4)
                raw=self.read(pointer,0xfcc)
                count,*_=probe_scene_stream.cache_entries(raw)
                bounds=struct.unpack_from('<8f',raw)
                if count or all(bounds[i]<=bounds[i+4] for i in range(3)):
                    self.write(self.scratch+64,raw[:32])
                    self.call('cache_rebuild',(self.scratch+64,),pointer)
        self.cache_dirty=False

    def vehicle_extents(self,identity):
        value=self.vehicles[identity]
        if 'extents' in value:return value['extents']
        owner,collider=value['owner'],value['collider']
        extents=[(owner,owner+0x124),(collider,collider+(0x3c if value['group'] else 0xd4))]
        if value['group']:
            head=self.word(collider+0x10)
            node=self.word(head+0xc)
            visited=set()
            while node!=head:
                host.require(node not in visited and len(visited)<MAX_SHAPE_OBJECTS,'Vehicle compound child chain differs')
                visited.add(node)
                data=struct.unpack('<5I',self.read(node,20))
                child=data[1]
                host.require(data[0]==0x68f740 and self.word(child)==0x68f6e8,'Vehicle compound child identity differs')
                extents.extend(((node,node+20),(child,child+0xd4)))
                node=data[3]
            host.require(len(visited)==self.shapes[value['shape']]['count'],'Vehicle compound child count differs')
        if 'rails' in value:
            r=value['rails'];extents.extend(((r['rail_manager'],r['rail_manager']+0xe8),(r['group'],r['group']+r['nodes']*0x60)))
        for kind,pointer in value.get('components',{}).items():
            extents.append((pointer,pointer+(0x20 if kind=='Skitch' else 0x1c)))
        if 'ObjectHook' in value.get('components',{}):
            manager=self.word(value['components']['ObjectHook']+0x18)
            extents.append((manager,manager+0x4c))
            node=self.word(manager)
            seen=set()
            while node:
                host.require(node not in seen and len(seen)<2,'Vehicle hook chain differs')
                seen.add(node);extents.append((node,node+20));node=self.word(node+16)
        value['extents']=extents
        return extents

    def actor_references(self,identity,blocks=None):
        if not self.references:return []
        value=self.vehicles[identity]
        owner_label=f"owner_{value['owner']:08x}"
        filtered=[]
        for label,raw in self.references.blocks() if blocks is None else blocks:
            if label==owner_label or label.startswith(owner_label+'_component_'):continue
            # CObject intrusive registration links are owned by its destructor,
            # rather than gameplay references that retain an object.
            if label in ('actor','camera_owner') or label.startswith('owner_') and '_component_' not in label:
                raw=raw[:0x38]+bytes(20)+raw[0x4c:]
            elif label in ('core','walk') or '_component_' in label:
                # Pinned ComponentRegister stores the next component of this
                # type at +0x14. CompositeDestructor unregisters that link
                # before deleting the component; it is not borrowed gameplay.
                raw=raw[:0x14]+bytes(4)+raw[0x18:]
            filtered.append((label,raw))
        return reference_words(filtered,self.vehicle_extents(identity))

    def release_shape(self,key):
        host.require(key in self.shapes and not any(v['shape']==key for v in self.vehicles.values()),
                     'Vehicle shape still has native owners')
        value=self.shapes[key]
        if self.references:
            host.require(not reference_words(self.references.blocks(),[(value['col'],value['col']+value['bytes'])]),
                         'Vehicle shape COL still has original references')
        if key in self.node_shapes:
            nodes=self.node_shapes.pop(key)
            host.require(self.call('NodeManagerRemove',(nodes['array'],),self.word(0x83e87c))&255,
                         'Vehicle local NodeArray removal failed')
            self.call('SourceUnload',(probe_gta_rails.rail_export.qbkey(nodes['filename']),))
            host.success(self.free(self.process,nodes['source'],0,0x8000),'Release unregistered vehicle QB buffer')
        self.call('destroy',this=value['scene'])
        host.require(self.word(value['scene']+0x60)==self.word(value['scene']+0x68)==self.word(value['scene']+0x6c)==0,
                     'Original vehicle scene destructor retained geometry')
        for address in (value['scene'],value['col']):
            host.success(self.free(self.process,address,0,0x8000),'Release detached vehicle shape')
        del self.shapes[key]
        self.aliases={alias:sha for alias,sha in self.aliases.items() if sha!=key}
        self.log('vehicle_shape_released',key=key,original_destructors=True)

    def _reclaim(self):
        if not any(v['retired'] for v in self.vehicles.values()):return
        if self.serial%15 and len(self.vehicles)<24:return
        blocks=self.references.blocks() if self.references else []
        for identity,value in list(self.vehicles.items()):
            refs=self.actor_references(identity,blocks) if value['retired'] else None
            if value['retired'] and not refs:self.remove(identity,_references=refs)

    def park(self):
        self.idle()
        self.skitch_input_latched=False
        for identity in self.vehicles:self.set_active(identity,False)
        return self.status(0,0)

    def status(self,pending,created):
        return dict(active=sum(not v['parked'] for v in self.vehicles.values()),
                    retired=sum(v['retired'] for v in self.vehicles.values()),shapes=len(self.shapes),
                    pending=pending,created=created,ready=pending==0)

    def sync(self,prepared,*,allow_create=True,active=True):
        self.idle()
        self.serial+=1
        try:
            incoming=prepared['vehicles'] if prepared.get('ready') else []
            host.require(len(incoming)<=16 and len({r['id'] for r in incoming})==len(incoming),
                         'Vehicle snapshot identities exceed the native bound')
            # A valid but over-budget local mesh is a per-shape limitation,
            # not a failed native frame. Validate before retiring a previously
            # supported shape of the same car, and log the exact model/budget.
            for row in incoming:
                key=row['shape_key']
                payload=prepared['shapes'].get(key)
                if key in self.aliases or key in self.shape_rejections or payload is None:continue
                if (len(payload['col'])<=MAX_SHAPE_BYTES and
                        struct.unpack_from('<I',payload['col'],4)[0]<=MAX_SHAPE_OBJECTS):continue
                try:validate_shape_payload(payload['col'],payload['report'])
                except VehicleShapeTooLarge as error:
                    details=dict(shape_key=key,vehicle_id=row['id'],model=row.get('model'),
                        objects=error.count,bytes=error.size,object_limit=MAX_SHAPE_OBJECTS,
                        byte_limit=MAX_SHAPE_BYTES,reason='native_shape_budget')
                    self.shape_rejections[key]=details
                    if len(self.shape_rejections)>64:self.shape_rejections.popitem(last=False)
                    self.log('vehicle_shape_rejected',**details)
            generation=tuple(prepared['source_identity'])
            by_id={r['id']:r for r in incoming}
            tick=prepared['tick_ms']
            for identity,value in list(self.vehicles.items()):
                row=by_id.get(value.get('external_id'))
                missing=not row and (not prepared.get('ready') or not prepared.get('truncated') or tick-value.get('last_seen',tick)>250)
                replacement=bool(row and row['shape_key']!=value.get('input_shape') and
                    row['shape_key'] in prepared['shapes'] and row['shape_key'] not in self.shape_rejections)
                if value['retired'] or self.generation!=generation or missing or replacement:
                    self.set_active(identity,False);value['retired']=True
            self.generation=generation
            self._reclaim()
            pending=created=0
            rejected=sum(row['shape_key'] in self.shape_rejections for row in incoming)
            for row in incoming:
                found=next((identity for identity,v in self.vehicles.items() if not v['retired'] and v.get('external_id')==row['id']),None)
                if found is not None:
                    value=self.vehicles[found]
                    value['last_seen']=tick
                    allowed=row['vehicle_class'] in (0,1,2,11) and row['health']>0 and row['driver_present']
                    if value['skitch_allowed']!=allowed:
                        value['skitch_allowed']=allowed
                        if 'Skitch' in value.get('components',{}):
                            self._set_skitch_enabled(value,bool(active and allowed))
                    self.update(found,row['position'],row['matrix'],row['origin_velocity'])
                    self.set_active(found,active)
                    continue
                if row['shape_key'] in self.shape_rejections:continue
                if not allow_create or created or len(self.vehicles)>=24 or row['shape_key'] not in prepared['shapes']:
                    pending+=1;continue
                key=self.aliases.get(row['shape_key'])
                if key is None:
                    if len(self.shapes)>=24:
                        blocks=self.references.blocks() if self.references else []
                        candidates=sorted((v['last_used'],k) for k,v in self.shapes.items()
                            if not any(o['shape']==k for o in self.vehicles.values()))
                        # The first safe LRU entry is enough; rescanning every
                        # newer shape cannot change that choice. release_shape
                        # still obtains its own fresh reference census.
                        key=next((k for _,k in candidates if not reference_words(blocks,
                            [(self.shapes[k]['col'],self.shapes[k]['col']+self.shapes[k]['bytes'])])),None)
                        if key is None:pending+=1;continue
                        self.release_shape(key)
                    payload=prepared['shapes'][row['shape_key']]
                    try:key=self.load_shape(payload['col'],verified=payload['report'])
                    except VehicleShapeCapacity:
                        pending+=1
                        self.log('vehicle_shape_deferred',reason='native_cache_capacity',
                            vehicle_id=row['id'],model=row.get('model'),native_shapes=len(self.shapes),
                            object_count=struct.unpack_from('<I',payload['col'],4)[0],bytes=len(payload['col']))
                        continue
                    nodes=payload['rail_report']['nodes'] if payload['rail_report'] else []
                    if nodes or payload['hooks']:self.load_nodes(key,nodes,payload['hooks'])
                    self.aliases[row['shape_key']]=key
                identity=(self.serial,row['id'])
                value=self.create(identity,key,row['position'],row['matrix'],row['origin_velocity'])
                value.update(external_id=row['id'],input_shape=row['shape_key'],last_seen=tick,
                             skitch_allowed=row['vehicle_class'] in (0,1,2,11) and row['health']>0 and row['driver_present'])
                if key in self.node_shapes:self.attach_components(identity)
                self.vehicle_extents(identity)
                self.set_active(identity,False)
                self.set_active(identity,active)
                created+=1
            # The original cache selects moving objects by their current AABB.
            # Refresh once after the transform batch so a car entering an old
            # cache region is found, and existing dirty bounds are evaluated.
            if self.cache_dirty:self.rebuild_caches()
            result=self.status(pending,created)
            result['rejected_shapes']=rejected
            if created or self.serial%60==0:
                self.log('vehicle_sync',**result,sequence=prepared['sequence'],
                    active_ids=[v.get('external_id') for v in self.vehicles.values() if not v['parked']],
                    original_owners=[v['owner'] for v in self.vehicles.values() if not v['parked']])
            return result
        except BaseException:
            self.poisoned=True
            raise
