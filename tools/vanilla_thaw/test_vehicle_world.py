import hashlib
import struct
import unittest
from collections import OrderedDict
from unittest.mock import Mock, patch

import probe_vehicle_world as vehicles
from probe_vehicle_nodes import compile_nodes
from assets_export import qbkey
from carentan_qb import node_fields


def snapshot(rows, *, tick=1000, truncated=False, generation=(1,2,3,0), shapes=None):
    return dict(vehicles=rows,tick_ms=tick,sequence=tick,source_identity=generation,
                ready=True,truncated=truncated,shapes=shapes or {})


def row(identity,key='one',position=(0.,0.,0.)):
    return dict(id=identity,shape_key=key,position=position,matrix=vehicles.IDENTITY,
                origin_velocity=(0.,0.,0.),vehicle_class=0,health=1000,driver_present=True)


def shape():
    return dict(col=struct.pack('<2I',10,1)+b'validated fixture',report={},rail_report=None,hooks=[])


def oversized_shape():
    payload=struct.pack('<2I',10,2048)+b'verified compiler payload'
    return dict(col=payload,report=dict(format='gta-thaw-vehicle-local-collision-v1',
        col_sha256=hashlib.sha256(payload).hexdigest(),stats=dict(col_bytes=len(payload),col_objects=2048)),
        rail_report=None,hooks=[])


class VehicleDiagnosticTests(unittest.TestCase):
    def fixture(self):
        w=vehicles.VehicleWorld.__new__(vehicles.VehicleWorld)
        actor,component,core,owner,contact=0x10000,0x11000,0x12000,0x15000,0x16000
        memory=bytearray(0x10000);reads=[]
        def write(address,fmt,*values):struct.pack_into(fmt,memory,address-0x10000,*values)
        def read(address,size):
            reads.append((address,size))
            if address==0x500000:return b'pinned'
            assert 0x10000<=address<=0x20000-size
            return bytes(memory[address-0x10000:address-0x10000+size])
        w.read=read;w.word=lambda address:struct.unpack('<I',read(address,4))[0]
        w.idle=Mock();w.call=Mock();w.write=Mock()
        w.references=Mock(arena=(0x10000,0x20000))
        w.context=dict(actor=actor,components={0x408d2aa9:component,0x5bd63b29:core})
        write(component,'<4I',0x6904a8,0x408d2aa9,0,actor)
        write(core+0x1930,'<I',component);write(component+0x18,'<I',contact)
        write(contact,'<I',owner);write(owner,'<I',0x68f7b8)
        write(owner+0x68,'<4f',1.,2.,3.,1.);write(owner+0x78,'<4f',4.,5.,6.,1.)
        write(owner+0x88,'<16f',*vehicles.IDENTITY);write(owner+0xc8,'<4f',7.,8.,9.,1.)
        write(owner+0xdc,'<16f',*vehicles.IDENTITY)
        w.vehicles={(1,2):dict(owner=owner,collider=0x17000,external_id=2,
            parked=False,retired=False,shape='fixture',last_seen=123)}
        pins=(('fixture',0x500000,6,hashlib.sha256(b'pinned').hexdigest()),)
        return w,write,reads,pins

    def test_rare_capture_reads_only_owned_objects_and_keeps_both_positions(self):
        w,write,reads,pins=self.fixture()
        with patch.object(vehicles,'MOTION_DIAGNOSTIC_PINS',pins):result=w.diagnostic_snapshot()
        self.assertTrue(result['contact_owned']);self.assertEqual(result['contact_vehicle_id'],2)
        self.assertEqual(len(bytes.fromhex(result['motion_contact_hex'])),0xb0)
        self.assertEqual(result['vehicles'][0]['position'],[1.,2.,3.,1.])
        self.assertEqual(result['vehicles'][0]['previous_position'],[4.,5.,6.,1.])
        w.call.assert_not_called();w.write.assert_not_called()

    def test_unknown_contact_owner_is_not_dereferenced(self):
        w,write,reads,pins=self.fixture();write(0x16000,'<I',0xdeadbeef)
        with patch.object(vehicles,'MOTION_DIAGNOSTIC_PINS',pins):result=w.diagnostic_snapshot()
        self.assertFalse(result['contact_owned']);self.assertIsNone(result['contact_vehicle_id'])
        self.assertFalse(any(address==0xdeadbeef for address,_ in reads))
        self.assertEqual(result['contact_owner'],0xdeadbeef)

    def test_foreign_contact_allocation_and_component_owner_reject_before_read(self):
        for field,value,expected in ((0x11018,0xdeadbeef,'owned arena'),
                                     (0x1100c,0x15000,'ownership'),
                                     (0x11018,0x1ff80,'owned arena')):
            w,write,reads,pins=self.fixture();write(field,'<I',value)
            with patch.object(vehicles,'MOTION_DIAGNOSTIC_PINS',pins):
                with self.assertRaisesRegex(RuntimeError,expected):w.diagnostic_snapshot()
            self.assertNotIn((value,0xb0),reads)
            w.call.assert_not_called();w.write.assert_not_called()

    def test_changed_original_layout_rejects_before_owned_contact_read(self):
        w,write,reads,pins=self.fixture()
        with patch.object(vehicles,'MOTION_DIAGNOSTIC_PINS',(('bad',0x500000,6,'0'*64),)):
            with self.assertRaisesRegex(RuntimeError,'layout'):w.diagnostic_snapshot()
        self.assertEqual(reads,[(0x500000,6)])


class VehiclePolicyTests(unittest.TestCase):
    def world(self):
        w=vehicles.VehicleWorld.__new__(vehicles.VehicleWorld)
        w.shapes={};w.vehicles={};w.aliases={};w.node_shapes={};w.serial=0
        w.generation=None;w.poisoned=False;w.references=None;w.context=None
        w.cache_dirty=False
        w.skitch_input_latched=False;w._consume_skitch_buttons=Mock()
        w.shape_rejections=OrderedDict()
        w.idle=Mock();w.log=Mock();w.call=Mock();w.transform=Mock();w.params=4;w.off_params=8
        def load(col,verified=None):
            key=hashlib.sha256(col).hexdigest();w.shapes[key]=dict(last_used=w.serial)
            return key
        def create(identity,key,position,matrix,velocity):
            v=dict(shape=key,owner=1000+w.serial,parked=False,retired=False,skitch_allowed=True)
            w.vehicles[identity]=v;w.update(identity,position,matrix,velocity);return v
        def update(identity,position,matrix,velocity):
            w.vehicles[identity]['position']=position;w.cache_dirty=True
        def active(identity,enabled):w.vehicles[identity]['parked']=not enabled
        def remove(identity,**kwargs):del w.vehicles[identity]
        w.load_shape=Mock(side_effect=load);w.create=Mock(side_effect=create)
        w.update=Mock(side_effect=update);w.set_active=Mock(side_effect=active)
        w.vehicle_extents=Mock();w.remove=Mock(side_effect=remove)
        w.rebuild_caches=Mock(side_effect=lambda:setattr(w,'cache_dirty',False))
        return w

    def test_one_owner_prewarms_per_tick_without_activation(self):
        w=self.world();s=snapshot([row(1),row(2),row(3)],shapes={'one':shape()})
        a=w.sync(s,active=False);b=w.sync(s,active=False);c=w.sync(s,active=False)
        self.assertEqual([a['pending'],b['pending'],c['pending']],[2,1,0])
        self.assertEqual(c['active'],0)
        self.assertEqual(w.load_shape.call_count,1)
        self.assertEqual(w.sync(s)['active'],3)

    def full_shape_cache(self, references=()):
        w=self.world()
        w.shapes={f'cached{i:02}':dict(last_used=i,col=0x10000+i*0x1000,bytes=256)
                  for i in range(24)}
        raw=struct.pack('<'+'I'*len(references),*references)
        w.references=Mock()
        w.references.blocks.return_value=[('actor',raw)]
        w.release_shape=Mock(side_effect=lambda key:w.shapes.pop(key))
        return w

    def test_shape_eviction_stops_at_first_unreferenced_lru(self):
        w=self.full_shape_cache((0x10004,))
        with patch.object(vehicles,'reference_words',wraps=vehicles.reference_words) as scan:
            result=w.sync(snapshot([row(1)],shapes={'one':shape()}))
        self.assertEqual(result['created'],1)
        w.release_shape.assert_called_once_with('cached01')
        self.assertEqual(scan.call_count,2)
        self.assertEqual([call.args[1] for call in scan.call_args_list],
                         [[(0x10000,0x10100)],[(0x11000,0x11100)]])

    def test_shape_eviction_preserves_lru_key_tie_order(self):
        w=self.full_shape_cache()
        w.shapes['cached01']['last_used']=0
        w.shapes=dict(reversed(list(w.shapes.items())))
        with patch.object(vehicles,'reference_words',wraps=vehicles.reference_words) as scan:
            w.sync(snapshot([row(1)],shapes={'one':shape()}))
        w.release_shape.assert_called_once_with('cached00')
        self.assertEqual(scan.call_count,1)

    def test_shape_eviction_never_checks_owned_shape(self):
        w=self.full_shape_cache()
        w.generation=(1,2,3,0)
        w.vehicles[(0,100)]=dict(owner=1000,external_id=100,shape='cached00',input_shape='kept',
            last_seen=1000,retired=False,parked=False,skitch_allowed=True)
        w.aliases['kept']='cached00'
        with patch.object(vehicles,'reference_words',wraps=vehicles.reference_words) as scan:
            w.sync(snapshot([row(100,'kept'),row(1)],shapes={'one':shape()}))
        w.release_shape.assert_called_once_with('cached01')
        self.assertEqual(scan.call_count,1)
        self.assertEqual(scan.call_args.args[1],[(0x11000,0x11100)])

    def test_shape_eviction_waits_when_every_unused_shape_is_referenced(self):
        w=self.full_shape_cache(tuple(0x10000+i*0x1000+4 for i in range(24)))
        result=w.sync(snapshot([row(1)],shapes={'one':shape()}))
        self.assertEqual((result['pending'],result['created']),(1,0))
        w.release_shape.assert_not_called();w.load_shape.assert_not_called();w.create.assert_not_called()
        self.assertFalse(w.poisoned)

    def test_shape_release_still_rechecks_fresh_references_before_native_delete(self):
        w=self.full_shape_cache()
        # A previous selection's snapshot is never accepted by release_shape.
        w.references.blocks.return_value=[('actor',struct.pack('<I',0x10004))]
        with self.assertRaisesRegex(RuntimeError,'COL still has original references'):
            vehicles.VehicleWorld.release_shape(w,'cached00')
        w.references.blocks.assert_called_once_with()
        w.call.assert_not_called()
        self.assertIn('cached00',w.shapes)

    def test_pending_new_shape_keeps_live_owner_and_updates_motion(self):
        w=self.world();w.sync(snapshot([row(1)],shapes={'one':shape()}))
        original=next(iter(w.vehicles.values()))
        w.sync(snapshot([row(1,'replacement',(3.,0.,0.))],tick=1100))
        self.assertFalse(original['retired']);self.assertEqual(original['position'],(3.,0.,0.))
        self.assertEqual(w.create.call_count,1)

    def test_truncated_omission_waits_for_expiry(self):
        w=self.world();w.sync(snapshot([row(1)],shapes={'one':shape()}))
        original=next(iter(w.vehicles.values()))
        w.sync(snapshot([],tick=1200,truncated=True));self.assertFalse(original['retired'])
        w.sync(snapshot([],tick=1251,truncated=True));self.assertTrue(original['retired'])
        self.assertTrue(original['parked'])

    def test_expired_snapshot_retires_even_if_truncated(self):
        w=self.world();w.sync(snapshot([row(1)],shapes={'one':shape()}))
        original=next(iter(w.vehicles.values()))
        w.sync(dict(snapshot([],tick=1000,truncated=True),ready=False))
        self.assertTrue(original['retired']);self.assertTrue(original['parked'])

    def test_all_moved_owners_refresh_caches_once(self):
        w=self.world();s=snapshot([row(1),row(2)],shapes={'one':shape()})
        w.sync(s);w.sync(s);w.rebuild_caches.reset_mock()
        w.sync(snapshot([row(1,position=(4.,0.,0.)),row(2,position=(8.,0.,0.))]))
        w.rebuild_caches.assert_called_once_with()
        self.assertFalse(w.cache_dirty)

    def test_registry_link_excluded_but_adjacent_gameplay_pointer_retains(self):
        w=self.world();w.references=Mock()
        w.vehicles[1]=dict(owner=0x1000)
        w.vehicle_extents.return_value=[(0x2000,0x2020)]
        raw=bytearray(32);struct.pack_into('<I',raw,0x14,0x2000)
        blocks=[('owner_00003000_component_3506ce64',raw)]
        self.assertEqual(w.actor_references(1,blocks),[])
        struct.pack_into('<I',raw,0x18,0x2004)
        self.assertEqual(w.actor_references(1,blocks),[
            dict(component=blocks[0][0],offset=0x18,pointer=0x2004)])

    def test_complete_omission_and_generation_replacement_retire(self):
        w=self.world();s=snapshot([row(1)],shapes={'one':shape()});w.sync(s)
        old=next(iter(w.vehicles.values()))
        s['source_identity']=(1,2,4,0);w.sync(s)
        self.assertTrue(old['retired']);self.assertEqual(w.create.call_count,2)
        w.sync(snapshot([],generation=(1,2,4,0)))
        self.assertTrue(all(v['retired'] for v in w.vehicles.values()))

    def test_driver_exit_refreshes_skitch_eligibility(self):
        w=self.world();s=snapshot([row(1)],shapes={'one':shape()});w.sync(s)
        v=next(iter(w.vehicles.values()));v['components']={'Skitch':500}
        s['vehicles'][0]['driver_present']=False;w.sync(s)
        self.assertFalse(v['skitch_allowed'])
        w.call.assert_called_with('SkitchEnable',(8,0),500)

    def test_skitch_entry_requires_chord_and_rear_of_real_hooks(self):
        w=self.world();w.context={'actor':0x9000}
        w.observe_skitch=Mock(return_value=None)
        w.read=Mock(return_value=struct.pack('<3f',0.,0.,-90.))
        w.node_shapes['car']={'hook_positions':((-10.,15.,-40.),(10.,15.,-40.))}
        v=dict(owner=0x1000,shape='car',parked=False,retired=False,skitch_allowed=True,
               components={'Skitch':500},published_position=struct.pack('<4f',0,0,0,1),
               published_matrix=struct.pack('<16f',*vehicles.IDENTITY))
        w.vehicles[1]=v
        w.set_skitch_input(False)
        w.call.assert_called_once_with('SkitchEnable',(8,0),500)
        w.call.reset_mock();w.set_skitch_input(False);w.call.assert_not_called()
        w._consume_skitch_buttons.assert_not_called()
        w.set_skitch_input(True)
        w._consume_skitch_buttons.assert_called_once_with()
        w.call.assert_called_once_with('SkitchEnable',(4,0),500)
        w.read.return_value=struct.pack('<3f',0.,0.,-10.)
        w.call.reset_mock();w.set_skitch_input(True)
        w.call.assert_called_once_with('SkitchEnable',(8,0),500)
        w._consume_skitch_buttons.assert_called_once_with()
        # Rotating the carrier rotates its rear admission plane as well.
        v['published_matrix']=struct.pack('<16f',0,0,-1,0,0,1,0,0,1,0,0,0,0,0,0,1)
        w.read.return_value=struct.pack('<3f',-90.,0.,0.)
        w.call.reset_mock();w.set_skitch_input(True)
        w.call.assert_called_once_with('SkitchEnable',(4,0),500)

    def test_existing_skitch_keeps_only_owned_car_enabled_without_chord(self):
        w=self.world();w.context={'actor':0x9000}
        w.observe_skitch=Mock(return_value={'vehicle_owner':0x1000})
        w.read=Mock()
        for index in (1,2):
            w.vehicles[index]=dict(owner=index*0x1000,parked=False,retired=False,skitch_allowed=True,
                                   components={'Skitch':index*500})
        w.set_skitch_input(False)
        self.assertEqual(w.call.call_args_list,[(('SkitchEnable',(4,0),500),),
                                               (('SkitchEnable',(8,0),1000),)])
        w.read.assert_not_called()
        w.vehicles[1]['skitch_allowed']=False;w.call.reset_mock();w.set_skitch_input(False)
        w.call.assert_called_once_with('SkitchEnable',(8,0),500)
        with self.assertRaisesRegex(RuntimeError,'boolean'):w.set_skitch_input(1)

    def test_native_failure_poisoned_and_propagated(self):
        w=self.world();w.create.side_effect=RuntimeError('native ABI failure')
        with self.assertRaisesRegex(RuntimeError,'native ABI'):
            w.sync(snapshot([row(1)],shapes={'one':shape()}))
        self.assertTrue(w.poisoned)

    def test_over_budget_shape_rejected_once_without_poisoning_other_cars(self):
        w=self.world();bad=row(2,'oversized');bad['model']=500
        s=snapshot([bad,row(1)],shapes={'oversized':oversized_shape(),'one':shape()})
        result=w.sync(s);w.sync(s)
        self.assertEqual(result['active'],1);self.assertEqual(result['rejected_shapes'],1)
        self.assertFalse(w.poisoned);self.assertEqual(w.create.call_count,1)
        rejected=[call for call in w.log.call_args_list if call.args[0]=='vehicle_shape_rejected']
        self.assertEqual(len(rejected),1)
        self.assertEqual(rejected[0].kwargs['objects'],2048)
        self.assertEqual(rejected[0].kwargs['model'],500)

    def test_over_budget_replacement_preserves_supported_car_and_motion(self):
        w=self.world();w.sync(snapshot([row(1)],shapes={'one':shape()}))
        original=next(iter(w.vehicles.values()))
        w.sync(snapshot([row(1,'oversized',(4.,0.,0.))],shapes={'oversized':oversized_shape()}))
        self.assertFalse(original['retired']);self.assertFalse(original['parked'])
        self.assertEqual(original['position'],(4.,0.,0.));self.assertEqual(w.create.call_count,1)

    def test_over_budget_does_not_mask_changed_hash(self):
        w=self.world();payload=oversized_shape();payload['report']['col_sha256']='0'*64
        with self.assertRaisesRegex(RuntimeError,'content differs'):
            w.sync(snapshot([row(1,'oversized')],shapes={'oversized':payload}))
        self.assertTrue(w.poisoned)

    def test_native_capacity_defers_without_poisoning(self):
        w=self.world();w.load_shape.side_effect=vehicles.VehicleShapeCapacity('full')
        result=w.sync(snapshot([row(1)],shapes={'one':shape()}))
        self.assertFalse(w.poisoned);self.assertEqual(result['pending'],1)
        self.assertEqual(w.create.call_count,0)

    def test_cache_full_of_referenced_shapes_defers_without_eviction(self):
        w=self.world()
        for i in range(24):
            w.shapes[str(i)]=dict(last_used=i,col=0x1000+i*64,bytes=64)
        w.references=Mock();w.references.blocks.return_value=[('core',struct.pack('<24I',*(0x1000+i*64 for i in range(24))))]
        w.release_shape=Mock()
        result=w.sync(snapshot([row(1)],shapes={'one':shape()}))
        self.assertFalse(w.poisoned);self.assertEqual(result['pending'],1)
        w.release_shape.assert_not_called();w.load_shape.assert_not_called()

    def test_cache_evicts_only_unreferenced_shape_before_create(self):
        w=self.world()
        for i in range(24):w.shapes[str(i)]=dict(last_used=i,col=0x1000+i*64,bytes=64)
        w.references=Mock();w.references.blocks.return_value=[('core',struct.pack('<23I',*(0x1000+i*64 for i in range(23))))]
        w.release_shape=Mock(side_effect=lambda key:w.shapes.pop(key))
        result=w.sync(snapshot([row(1)],shapes={'one':shape()}))
        self.assertFalse(w.poisoned);self.assertEqual(result['created'],1)
        w.release_shape.assert_called_once_with('23')
        self.assertEqual(len(w.shapes),24)

    def test_reflection_and_skew_rejected(self):
        for index,value in ((0,-1.),(4,.1),(12,2.)):
            matrix=list(vehicles.IDENTITY);matrix[index]=value
            with self.assertRaises(RuntimeError):vehicles.validate_transform((0,0,0),matrix,(0,0,0))
        vehicles.validate_transform((0,0,0),vehicles.IDENTITY,(0,0,0))

    def test_unchanged_transform_skips_native_calls_but_keeps_velocity_and_previous_position(self):
        w=self.world();w.shapes={'shape':dict(last_used=0)}
        w.vehicles[1]=dict(owner=0x1000,collider=0x2000,shape='shape',group=False)
        w.word=Mock(side_effect=lambda address:0x68f7b8 if address==0x1000 else 0x68f6e8)
        w.read=Mock(return_value=struct.pack('<4f',0.,0.,0.,1.));w.write=Mock()
        update=lambda p,v=(0.,0.,0.):vehicles.VehicleWorld.update(w,1,p,vehicles.IDENTITY,v)
        update((1.,0.,0.));w.call.reset_mock();w.write.reset_mock();w.cache_dirty=False
        w.transform.assert_called_once_with('Mesh',0x1000,0x2000)
        w.transform.reset_mock()
        update((1.,0.,0.),(3.,0.,0.))
        w.call.assert_not_called();self.assertFalse(w.cache_dirty)
        self.assertEqual(w.write.call_args_list,[( (0x1078,struct.pack('<4f',1.,0.,0.,1.)),),
                                                ( (0x10c8,struct.pack('<4f',3.,0.,0.,1.)),)])
        w.write.reset_mock();update((1.,0.,0.),(3.,0.,0.))
        w.write.assert_not_called();w.call.assert_not_called()
        update((2.,0.,0.),(3.,0.,0.))
        w.call.assert_called_once_with('MeshSetPosition',(0x1068,),0x2000)
        self.assertTrue(w.cache_dirty)
        w.transform.assert_not_called()

    def test_transform_batch_only_for_both_dirty_fields_after_all_owner_writes(self):
        for group in (False,True):
            w=self.world();w.shapes={'shape':dict(last_used=0)}
            w.vehicles[1]=dict(owner=0x1000,collider=0x2000,shape='shape',group=group)
            w.word=Mock(side_effect=lambda address:0x68f7b8 if address==0x1000 else (0x68f480 if group else 0x68f6e8))
            w.read=Mock(return_value=struct.pack('<4f',0.,0.,0.,1.))
            order=[];w.write=Mock(side_effect=lambda address,data:order.append(address))
            w.transform=Mock(side_effect=lambda *args:order.append('batch'))
            update=lambda p,m:vehicles.VehicleWorld.update(w,1,p,m,(1.,0.,0.))
            update((1.,0.,0.),vehicles.IDENTITY)
            prefix='Group' if group else 'Mesh'
            w.transform.assert_called_once_with(prefix,0x1000,0x2000)
            self.assertEqual(order,[0x1078,0x1068,0x1088,0x10dc,0x10c8,'batch'])
            w.call.assert_not_called();w.transform.reset_mock();order.clear()
            matrix=(0.,0.,-1.,0.,0.,1.,0.,0.,1.,0.,0.,0.,0.,0.,0.,1.)
            update((1.,0.,0.),matrix)
            w.call.assert_called_once_with(prefix+'SetOrientation',(0x1088,),0x2000)
            w.transform.assert_not_called()
            w.call.reset_mock();w.transform.side_effect=RuntimeError('native failure')
            published=dict(w.vehicles[1]);w.cache_dirty=False
            with self.assertRaisesRegex(RuntimeError,'native failure'):
                update((2.,0.,0.),vehicles.IDENTITY)
            self.assertEqual(w.vehicles[1]['published_position'],published['published_position'])
            self.assertEqual(w.vehicles[1]['published_matrix'],published['published_matrix'])
            self.assertFalse(w.cache_dirty)

    def test_hook_metadata_matches_stock_class_and_creation(self):
        data=compile_nodes('fixture',[],[(-1.,2.,3.),(1.,2.,3.)])
        self.assertEqual(struct.unpack_from('<I',data,52)[0],2)
        for index in range(2):
            fields=node_fields(data,struct.unpack_from('<I',data,60+4*index)[0])
            self.assertEqual(fields[qbkey('Class')],(0x1b00,qbkey('ObjectHook')))
            self.assertEqual(fields[0],(0x1b00,qbkey('CreatedAtStart')))
            self.assertNotIn(qbkey('TriggerScript'),fields)
        for hooks in ([],[(1,2,3)],[(1,2,float('nan')),(2,3,4)]):
            with self.assertRaises(ValueError):compile_nodes('fixture',[],hooks)


class ChordConsumption(unittest.TestCase):
    def fixture(self):
        w=vehicles.VehicleWorld.__new__(vehicles.VehicleWorld)
        actor,inp,state,core,trick=0x1000,0x2000,0x3000,0x4000,0x6000
        memory=bytearray(0x9000)
        def put(address,value):struct.pack_into('<I',memory,address,value)
        for address,key in ((inp,0x27d7cd28),(state,0x43686585),(trick,0x270f56e1)):
            put(address+4,key);put(address+12,actor)
        put(actor+0x29c,inp);put(core+0x192c,state);put(trick+0x20,inp)
        memory[state+0x20:state+0x28]=b'\x01\xa5\xb6\xc7'+struct.pack('<I',123)
        memory[state+0x28:state+0x140]=bytes(range(256))+bytes(24)
        put(inp+0x18,0xabcdef01);put(inp+0x1c,0xf5f5f5f5)
        for offset,key in ((0x1c0,0x7323e97c),(0x1a0,0x20689278)):
            memory[inp+offset]=1;memory[inp+offset+0x10]=1
            put(inp+offset+0xc,255);put(inp+offset+0x18,key)
        memory[trick+0xa44+11:trick+0xa44+13]=b'\1\1'
        w.context={'actor':actor,'components':{0x27d7cd28:inp,0x43686585:state,
                    0x5bd63b29:core,0x270f56e1:trick}}
        w.idle=Mock();w.log=Mock();writes=[];calls=[]
        w.read=lambda address,size:bytes(memory[address:address+size])
        w.word=lambda address:9 if address==0x6dd8d0 else struct.unpack_from('<I',memory,address)[0]
        def write(address,data):
            writes.append((address,data));memory[address:address+len(data)]=data
        w.write=write
        def call(name,args=(),this=None):
            calls.append((name,args,this))
            if name=='SkitchInputClock':return 0xfffffffc
            if name=='SkitchButtonUpdate':memory[this]=0;put(this+0xc,0)
            elif name=='SkitchClearPress':memory[this+0x10]=0
            elif name=='SkitchClearRelease':put(this+0x14,0)
            elif name=='SkitchTrickButton':memory[this+0xa44+args[0]]=0
        w.call=call
        return w,memory,writes,calls

    def test_claim_uses_original_edges_and_preserves_unrelated_state_and_mask(self):
        w,memory,writes,calls=self.fixture();before=bytes(memory)
        w._consume_skitch_buttons()
        state=w.context['components'][0x43686585];inp=w.context['components'][0x27d7cd28]
        expected=bytearray(before[state:state+0x140]);expected[0x20]=0
        struct.pack_into('<I',expected,0x24,5)
        self.assertEqual(memory[state:state+0x140],expected)
        self.assertEqual(w.word(inp+0x18),0xabcdef01)
        self.assertEqual(w.word(inp+0x1c),0xf5f5f5f5&~0x500)
        self.assertEqual([(a,len(b))for a,b in writes],[(state+0x20,8),(inp+0x1c,4)])
        consumed=[args for name,args,_ in calls if name=='SkitchClearButtonEvents']
        self.assertEqual(consumed,[(0x7323e97c,0),(0x20689278,0)])
        self.assertEqual([args for name,args,_ in calls if name=='SkitchTrickButton'],[(11,0),(12,0)])

    def test_foreign_trick_input_is_rejected_before_mutation(self):
        w,memory,writes,calls=self.fixture()
        struct.pack_into('<I',memory,w.context['components'][0x270f56e1]+0x20,0x2004)
        with self.assertRaisesRegex(RuntimeError,'ownership'):w._consume_skitch_buttons()
        self.assertEqual(writes,[]);self.assertEqual(calls,[])

    def test_no_charge_does_not_write_state_or_clock(self):
        w,memory,writes,calls=self.fixture();state=w.context['components'][0x43686585]
        memory[state+0x20]=0;before=bytes(memory[state:state+0x140])
        w._consume_skitch_buttons()
        self.assertEqual(bytes(memory[state:state+0x140]),before)
        self.assertFalse(any(name=='SkitchInputClock'for name,_,_ in calls))


if __name__=='__main__':unittest.main()
