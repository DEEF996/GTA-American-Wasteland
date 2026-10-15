"""Witness a bounded stock ceiling correction, without running physics again."""
import hashlib
import math
import struct

import probe_loader as host
import probe_scene
import probe_vm_idle

METRES=.0254
MAX_DROP=2.
# Stock float32 plane projection left 3.051758e-5 / 6.103516e-5 inches/s
# in captured wall releases. Preserve that velocity; only the witness gate has
# a 0.0001 inches/s numerical tolerance (2.54 micrometres/s).
MAX_CEILING_VERTICAL_SPEED=1.e-4
CANARY=b'CEILING-QUERY!!!'
PINS=(
    ('OriginalCeilingCorrection',0x5d8650,844,'2328ff5a372c85d284e0a5213c86d0697faa101fc4c911493bc52a30ae80102f'),
    ('OriginalSkaterFloat',0x58ef60,62,'bef8726a5cbceded11fd3411f2aa598063ff7a93adc7d463a93e5b2a6530652a'),
    ('OriginalWallrideCeilingCorrection',0x5d89a0,696,'0ca66872a0ea03d93774affbcf89aa894640ab89e8759a09f05f37fa91efb5d4'),
    ('OriginalWallrideUpdate',0x5e7300,5292,'4809b9d9a5297033a726b027637307747ae2888a294eef2fca2a6b1f6e2b221f'),
    probe_scene.PINS['feeler'],probe_scene.PINS['query'],
)
SLOPE_PINS=(
    ('OriginalAirUpdate',0x5df9f0,5388,'eebe1013bef827d956e3f1e57cab4f8580bc9f2a9f8e54fdbfdf873c8b08f16c'),
    ('OriginalAirGravity',0x5c8f60,280,'2d26390dd833b2163c01da6547a9a013782461317636fb19fc9c5cc2f88ff12f'),
    ('OriginalNormalProjection',0x408d70,97,'f949f17e7bc81c8f4fbcde94a1c88a5b62da98784fb7e7a471c9824e449b6a36'),
    ('OriginalPlaneProjection',0x408ac0,132,'1ce59aeae329dba17bf6d969336dc08b98aa56d015d2da4805a9aed597d91027'),
)


def slope_candidate(previous,current,matrix,*,bail_board=None):
    if previous is None or current['frame']!=previous['frame']+1:
        return False
    for sample in (previous,current):
        if any(sample.get(k)!=v for k,v in (('mode',0),('state',1),('walking_gate',0))):
            return False
        if not all(math.isfinite(v) for v in sample['position']+sample['velocity']):
            return False
    if previous.get('bailing') is True and current.get('bailing') is True:
        # AirUpdate still runs its original head correction during an existing
        # bail. Only the same owned detached board proves this separate context;
        # a bail starting or ending within this step has not been witnessed.
        if not bail_board or any(sample.get('board_visible') is not False or
                sample.get('detached_board_id')!=bail_board for sample in (previous,current)):
            return False
    elif (previous.get('bailing') is not False or current.get('bailing') is not False or
            current.get('board_visible') is not True):
        return False
    return (len(matrix)==16 and
        all(math.isfinite(v) for v in matrix) and matrix[5]>=0 and
        abs(math.hypot(*matrix[4:7])-1)<.001 and
        math.dist(previous['position'],current['position'])*METRES>1.02 and
        0<(previous['position'][1]-current['position'][1])*METRES<=MAX_DROP)


def ballistic(previous,gravity):
    """Only a witness of pinned AirUpdate's integration, never a physics update."""
    position=[previous['position'][i]+previous['velocity'][i]/60 for i in range(3)]
    position[1]+=gravity/(2*60*60)
    velocity=list(previous['velocity']);velocity[1]+=gravity/60
    return position,velocity


def proves_slope(previous,current,matrix,height,gravity,hit,point,normal,blocked,*,bail_board=None):
    if not slope_candidate(previous,current,matrix,bail_board=bail_board) or not hit or blocked:
        return False
    if (len(point)!=4 or len(normal)!=4 or
        not all(math.isfinite(v) for v in (*point,*normal,height,gravity)) or
        not 0<height*METRES<=MAX_DROP or not -10000<=gravity<0 or
        point[3]!=1 or normal[3]!=0 or normal[1]>-.1 or
        abs(math.hypot(*normal[:3])-1)>.0001 or math.hypot(normal[0],normal[2])<.0001):
        return False
    start,incoming=ballistic(previous,gravity)
    end=[start[i]+matrix[4+i]*height for i in range(3)]
    # Independent native head ray must hit its finite segment, not merely a
    # nearby plane. The second original ray must leave the correction clear.
    along=sum((point[i]-start[i])*matrix[4+i] for i in range(3))
    if not 0<=along<=height or math.dist(point[:3],[start[i]+matrix[4+i]*along for i in range(3)])>.05:
        return False
    amount=sum((point[i]-end[i])*normal[i] for i in range(3))
    correction=[amount*normal[i] for i in range(3)]
    projected=[start[i]+correction[i] for i in range(3)]
    dot=sum(incoming[i]*normal[i] for i in range(3))
    velocity=[incoming[i]-dot*normal[i] for i in range(3)]
    return (0<math.hypot(*correction)*METRES<=MAX_DROP and
        math.dist(projected,current['position'])<=.05 and
        math.dist(velocity,current['velocity'])<=.05)


def wall_snap(previous,current,previous_matrix):
    if (previous['state']!=2 or previous_matrix is None or len(previous_matrix)!=16 or
            not all(math.isfinite(v) for v in previous_matrix)):
        return None
    normal=previous_matrix[4:7]
    if abs(normal[1])>.001 or abs(math.hypot(*normal)-1)>.001:
        return None
    residual=[current['position'][i]-previous['position'][i]-previous['velocity'][i]/60 for i in (0,2)]
    # Stock WallrideUpdate pushes 18 inches after ceiling correction, and another
    # 18 when the now upright support query misses. Neither is velocity integration.
    for distance in (18.,36.):
        if all(abs(residual[j]-normal[i]*distance)<=.05 and
               abs(current['velocity'][i]-previous['velocity'][i])<=5 for j,i in enumerate((0,2))):
            return distance
    return None


def candidate(previous,current,matrix,previous_matrix=None):
    if previous is None or current['frame']!=previous['frame']+1:
        return False
    for sample in (previous,current):
        if any(sample.get(key)!=value for key,value in
               (('mode',0),('walking_gate',0),('bailing',False))):
            return False
        if not all(math.isfinite(v) for v in sample['position']+sample['velocity']):
            return False
    if (current.get('board_visible') is not True or len(matrix)!=16 or not all(math.isfinite(v) for v in matrix) or
            math.dist(matrix[4:7],(0.,1.,0.))>.001 or
            current['state']!=1 or abs(current['velocity'][1])>MAX_CEILING_VERTICAL_SPEED):
        return False
    before,after=previous['position'],current['position']
    drop=(before[1]-after[1])*METRES
    return (math.dist(before,after)*METRES>1.02 and 0<drop<=MAX_DROP and
            ((previous['state']==1 and previous['velocity'][1]>0 and
              all(abs(current['velocity'][i]-previous['velocity'][i])<=.05 and
                  abs(after[i]-before[i]-previous['velocity'][i]/60)<=.05 for i in (0,2))) or
             wall_snap(previous,current,previous_matrix) is not None))


def query_position(previous,current,previous_matrix=None):
    position=list(current['position'])
    if previous['state']==2:
        distance=wall_snap(previous,current,previous_matrix)
        host.require(distance is not None,'Wall ceiling witness has no original wall-release displacement')
        for i in (0,2): position[i]-=(distance-18)*previous_matrix[4+i]
    return position


def proves_ceiling(previous,current,matrix,height,hit,point,normal,previous_matrix=None):
    if not candidate(previous,current,matrix,previous_matrix) or not hit:
        return False
    if (len(point)!=4 or len(normal)!=4 or
            not all(math.isfinite(v) for v in (*point,*normal,height)) or
            not 0<height*METRES<=MAX_DROP or point[3]!=1 or normal[3]!=0 or
            math.dist(normal[:3],(0.,-1.,0.))>.0001):
        return False
    # The actual unchanged head-height getter and original contact must agree
    # with the solved root; an arbitrary Air teleport cannot borrow this flag.
    target=query_position(previous,current,previous_matrix)
    target[1]+=height
    return math.dist(point[:3],target)<=.05


def height_stub(actor,record,output):
    host.require(all(type(v) is int and 0x10000<=v<0xfffffff0 and v%4==0
                     for v in (actor,record,output)), 'Invalid ceiling getter glue address')
    pack=lambda v:struct.pack('<I',v)
    return (b'\xc7\x05'+pack(record)+pack(2)+b'\x68'+pack(actor)+b'\x6a\x01'+
        b'\x68'+pack(0x542cf0c7)+b'\xb8'+pack(0x58ef60)+b'\xff\xd0\x83\xc4\x0c'+
        b'\xd9\x1d'+pack(output)+b'\xc7\x05'+pack(record)+pack(6)+host.STUB)


def gravity_stub(core,record,output):
    host.require(all(type(v) is int and 0x10000<=v<0xfffffff0 and v%4==0
                     for v in (core,record,output)), 'Invalid gravity getter glue address')
    pack=lambda v:struct.pack('<I',v)
    return (b'\xc7\x05'+pack(record)+pack(2)+b'\xb9'+pack(core)+b'\xb8'+pack(0x5c8f60)+
        b'\xff\xd0\xd9\x1d'+pack(output)+b'\xc7\x05'+pack(record)+pack(6)+host.STUB)


class AirCorrection:
    def __init__(self,api,process,record,deadline,log,context,system,*,vm_sources=None,extra_diagnostics=None,ground_trace=None):
        self.ground_trace=ground_trace
        self.api,self.process,self.record,self.deadline=api,process,record,deadline
        self.log,self.context,self.system,self.vm_sources=log,context,system,vm_sources
        # Constructor runs after this session's original handoff. driver.last
        # can still belong to the prior session and is not a valid entry seed.
        from probe_frames import motion_state
        actor,components=context['actor'],context['components']
        state=struct.unpack('<77I',system.read(components[0x43686585],0x134))
        walking=struct.unpack('<7I',system.read(components[0x726e85aa],28))
        self.previous=dict(frame=struct.unpack('<Q',system.read(0x8459d0,8))[0],
            position=list(struct.unpack('<3f',system.read(actor+0x68,12))),
            velocity=list(struct.unpack('<3f',system.read(actor+0xc8,12))),**motion_state(state,walking))
        self._snapshot(self.previous)
        self.bail_board=(system.word(actor+0x18)+0x884ef81b)&0xffffffff
        self.previous_matrix=list(struct.unpack('<16f',system.read(actor+0x88,64)))
        self.storage=context.get('gta_ceiling_witness_storage')
        self.last_sloped=False
        self.diagnostics=0
        self.bail_diagnostics=0
        self.extra_diagnostics=extra_diagnostics
        self.previous_visual_matrix=None
        self.previous_palette=None

    def _snapshot(self,sample):
        system,actor=self.system,self.context['actor']
        core=self.context['components'][0x5bd63b29]
        state=self.context['components'][0x43686585]
        host.require(system.word(actor)==0x6989e8 and system.word(core+4)==0x5bd63b29 and
            system.word(core+12)==actor and system.word(state+4)==0x43686585 and
            system.word(state+12)==actor, 'Ceiling witness actor/component identity changed')
        bail_board=getattr(self,'bail_board',None)
        host.require(bail_board is None or (system.word(actor+0x18)+0x884ef81b)&0xffffffff==bail_board,
                     'Ceiling witness actor board identity changed')
        frame=system.read(0x8459d0,8)
        host.require(struct.unpack('<Q',frame)[0]==sample['frame'], 'Ceiling witness source frame changed')
        motion=system.read(actor+0x68,112)
        host.require(motion[:12]==struct.pack('<3f',*sample['position']) and
            motion[96:108]==struct.pack('<3f',*sample['velocity']), 'Ceiling witness source sample differs')
        return frame,motion,system.read(state,0x134),system.read(core,0x19ac)

    def _query(self,previous,result,*,sloped=False):
        system,worker=self.system,self.system.worker
        current=result['sample']
        bail_board=getattr(self,'bail_board',None)
        worker.check_call_access()
        vm=probe_vm_idle.require_idle(system,self.context,self.vm_sources)
        before=self._snapshot(current)
        host.require(system.word(0x8405c0)==0,'Ceiling witness requires no active collision cache')
        for name,address,size,digest in PINS+(SLOPE_PINS if sloped else ()):
            trace=getattr(self,'ground_trace',None)
            code=trace.original_bytes(address,size) if trace is not None else system.read(address,size)
            host.require(hashlib.sha256(code).hexdigest()==digest,
                         'Ceiling witness original code differs: '+name)
        if self.storage is None:
            self.storage=system.allocate(CANARY+bytes(240)+CANARY)
            self.context['gta_ceiling_witness_storage']=self.storage
        host.require(system.read(self.storage,len(CANARY))==CANARY and
            system.read(self.storage+len(CANARY)+240,len(CANARY))==CANARY,
            'Ceiling witness storage ownership changed')
        output=self.storage+16
        feeler=self.storage+32
        system.write(output,bytes(16))
        system.write(self.record+36,bytes(36))
        code=height_stub(self.context['actor'],self.record,output)
        address=worker.prepare_command(code)
        waited,returned=worker.execute(address,self.deadline)
        status=struct.unpack('<18I',system.read(self.record,72))
        host.require(waited==0 and returned==0 and status[0]==6 and system.read(address,len(code))==code,
                     'Original ceiling head-height getter failed')
        height=struct.unpack('<f',system.read(output,4))[0]
        host.require(math.isfinite(height) and 0<height*METRES<=MAX_DROP,'Original head height exceeds ceiling witness bound')
        gravity=None
        if sloped:
            core=self.context['components'][0x5bd63b29]
            dt=struct.unpack('<f',system.read(core+0x9c,4))[0]
            host.require(abs(dt-1/60)<1.e-8,'Sloped ceiling requires an original 1/60 source step')
            flag=system.read(0x83e8c8,1)
            code=gravity_stub(core,self.record,output)
            address=worker.prepare_command(code)
            waited,returned=worker.execute(address,self.deadline)
            status=struct.unpack('<18I',system.read(self.record,72))
            host.require(waited==0 and returned==0 and status[0]==6 and
                system.read(address,len(code))==code and system.read(0x83e8c8,1)==flag,
                'Original gravity witness changed native state or failed')
            gravity=struct.unpack('<f',system.read(output,4))[0]
            host.require(math.isfinite(gravity) and -10000<=gravity<0,'Original gravity exceeds slope witness bound')
        host.require(host.native_call(self.api,self.process,probe_scene.PINS['feeler'],[],
            self.record,self.deadline,self.log,feeler)==feeler,'Original ceiling feeler constructor differs')
        if sloped:
            start,_=ballistic(previous,gravity)
            end=[start[i]+result['actor_matrix'][4+i]*height for i in range(3)]
        else:
            expected=query_position(previous,current,self.previous_matrix)
            start=(expected[0],previous['position'][1],expected[2])
            end=(start[0],start[1]+height,start[2])
        system.write(feeler,struct.pack('<8f',*start,1.,*end,1.))
        hit=bool(host.native_call(self.api,self.process,probe_scene.PINS['query'],[0,0,0],
            self.record,self.deadline,self.log,feeler)&255)
        data=system.read(feeler,0xc0)
        normal,point=struct.unpack_from('<4f',data,0x24),struct.unpack_from('<4f',data,0x34)
        host.require(struct.unpack_from('<2H',data,0x4c)==(0x10,0), 'Ceiling feeler input masks changed')
        blocked=sloped
        if sloped and proves_slope(previous,current,result['actor_matrix'],height,gravity,hit,point,normal,False,bail_board=bail_board):
            amount=sum((point[i]-end[i])*normal[i] for i in range(3))
            target=[start[i]+amount*normal[i] for i in range(3)]
            system.write(feeler,struct.pack('<8f',*start,1.,*target,1.))
            blocked=bool(host.native_call(self.api,self.process,probe_scene.PINS['query'],[0,0,0],
                self.record,self.deadline,self.log,feeler)&255)
            host.require(struct.unpack('<2H',system.read(feeler+0x4c,4))==(0x10,0),
                'Slope clearance feeler input masks changed')
        host.require(self._snapshot(current)==before and system.word(0x8405c0)==0 and
            probe_vm_idle.require_idle(system,self.context,self.vm_sources)==vm,
            'Ceiling query changed the completed native actor/source context')
        host.require(system.read(self.storage,len(CANARY))==CANARY and
            system.read(self.storage+len(CANARY)+240,len(CANARY))==CANARY,
            'Ceiling query exceeded its owned storage')
        proved=(proves_slope(previous,current,result['actor_matrix'],height,gravity,hit,point,normal,blocked,bail_board=bail_board)
            if sloped else proves_ceiling(previous,current,result['actor_matrix'],height,hit,point,normal,self.previous_matrix))
        self.log('original_ceiling_correction',frame=current['frame'],proved=proved,
            previous_position=previous['position'],position=current['position'],height=height,
            hit=hit,point=point,normal=normal,actor_unchanged=True,previous_state=previous['state'],
            wall_release_inches=wall_snap(previous,current,self.previous_matrix),
            sloped=sloped,gravity=gravity,correction_blocked=blocked,start=start,end=end,
            previous_bailing=previous.get('bailing'),bailing=current.get('bailing'))
        return proved

    def update(self,result):
        current=result['sample']
        # The caller retains this result with a pending completed source sample.
        proved=False
        self.last_sloped=False
        if candidate(self.previous,current,result['actor_matrix'],self.previous_matrix):
            proved=self._query(self.previous,result)
        if not proved and slope_candidate(self.previous,current,result['actor_matrix'],bail_board=getattr(self,'bail_board',None)):
            proved=self._query(self.previous,result,sloped=True)
            self.last_sloped=proved
        ground_trace=getattr(self,'ground_trace',None)
        large_displacement=(not proved and self.previous is not None and
                            math.dist(self.previous['position'],current['position'])*METRES>1.0201)
        # Reserve opt-in capacity for bail corrections, including the first
        # airborne bail frame, independently of other large discontinuities.
        bail_diagnostic=bool(large_displacement and ground_trace is not None and
            current.get('mode')==0 and current.get('bailing') is True and self.previous.get('mode')==0)
        diagnostic_counter='bail_diagnostics' if bail_diagnostic else 'diagnostics'
        if large_displacement and getattr(self,diagnostic_counter,0)<(4 if bail_diagnostic else 8):
            # Retain rare unproved discontinuities before an automatic session
            # recovery can replace the ordinary frame ring (including bails).
            snapshot=self._snapshot(current)
            extra={}
            diagnostic=getattr(self,'extra_diagnostics',None)
            if diagnostic is not None:
                try:extra=diagnostic()
                except (OSError,ValueError,RuntimeError) as error:
                    extra={'capture_error':str(error)}
            if ground_trace is not None:
                try:extra['ground_trace']=ground_trace.snapshot()
                except (OSError,ValueError,RuntimeError) as error:
                    extra['ground_trace_error']=str(error)
            self.log('source_large_displacement',previous=self.previous,sample=current,
                previous_matrix=self.previous_matrix,actor_matrix=result['actor_matrix'],
                previous_visual_matrix=getattr(self,'previous_visual_matrix',None),
                visual_matrix=result.get('visual_matrix'),
                previous_palette_hex=(getattr(self,'previous_palette',None) or b'').hex(),
                palette_hex=result.get('palette',b'').hex(),extra=extra,
                state_hex=snapshot[2].hex(),core_hex=snapshot[3].hex())
            setattr(self,diagnostic_counter,getattr(self,diagnostic_counter,0)+1)
        self.previous=dict(current)
        self.previous_matrix=list(result['actor_matrix'])
        self.previous_visual_matrix=result.get('visual_matrix')
        self.previous_palette=result.get('palette')
        return proved
