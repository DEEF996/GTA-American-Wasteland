"""Witness the original bail body collision; never supply a physics result.

One owned native wrapper retains two fixed records per frame. Normal frames
perform no host reads or writes for this witness. Only a large, existing Ground
bail reads the completed record and checks the original correction/history.
"""
import ctypes as C
import hashlib
import math
import struct

import probe_loader as host
from probe_ground_trace import GroundTrace, copy_stub, decode_record, relative_call, RECORD
from probe_frame_clock import PINS as CLOCK_PINS

CALL=0x5e9024
TARGET=0x5d2e90
SLOT=512
PINS=(
    (0x5e9022,12,'e0df100b319c7c5b58d97e027f860f2616b54d7af9d7724c8f4578fa37c9ae5b'),
    (TARGET,1539,'1b0c1070afec81d06670afb65ed3e230a5fb8beee2a8f758bfd65fa9d36b6752'),
    (0x5c3380,130,'cb7fde6d3a0fb46f396b255bf343866ba1618155d7bb6e7690f8c82023d8b14e'),
    CLOCK_PINS['begin_frame'][1:],
)
METRES=.0254
ORDINARY=.02+60/60
MAX_PUSH=2.


def begin_stub(actor,core,data):
    """Select/clear the owned slot using the original begin-frame counter.

    Repeated calls in the same frame retain their counts, so they cannot pass
    the exactly-once predicate. All registers/flags are restored before Core.
    """
    pack=lambda x:struct.pack('<I',x)
    code=bytearray(b'\x9c\x60')
    skips=[]
    def skip(op):
        code.extend(op+b'\x0f\x85');skips.append(len(code));code.extend(bytes(4))
    skip(b'\x81\x7c\x24\x24'+pack(core))
    skip(b'\xa1'+pack(core+12)+b'\x3d'+pack(actor))
    # OriginalClock_begin_frame increments this uint64 before owner updates.
    code+=b'\xa1'+pack(0x8459d0)+b'\x8b\x15'+pack(0x8459d4)
    code+=b'\x89\xc7\x83\xe7\x01\xc1\xe7\x09\x81\xc7'+pack(data+64)
    code+=b'\x89\x3d'+pack(data)+b'\x3b\x07\x0f\x85';clear_low=len(code);code+=bytes(4)
    code+=b'\x3b\x57\x04\x0f\x84';same=len(code);code+=bytes(4)
    clear=len(code)
    code+=b'\x89\xfe\x31\xc0\xb9'+pack(SLOT//4)+b'\xfc\xf3\xab'
    code+=b'\xa1'+pack(0x8459d0)+b'\x89\x06\x89\x56\x04'
    code+=b'\xa1'+pack(core+0x9c)+b'\x89\x46\x08'
    code+=b'\xa1'+pack(actor+0x18)+b'\x89\x46\x0c'
    end=len(code)
    struct.pack_into('<i',code,clear_low,clear-clear_low-4)
    for offset in skips+[same]:struct.pack_into('<i',code,offset,end-offset-4)
    code+=b'\x61\x9d'
    return bytes(code)


def wrapper(address,actor,core,state,data):
    before=b'\x51'+begin_stub(actor,core,data)+copy_stub(actor,core,state,data,0)
    return (before+relative_call(address+len(before),TARGET)+copy_stub(actor,core,state,data,1)+
            b'\x8d\x64\x24\x04\xc3')


class BodyWitness:
    _patch=GroundTrace._patch

    def __init__(self,api,process,system,context,log):
        self.api,self.process,self.system,self.context,self.log=api,process,system,context,log
        self.actor=context['actor'];self.core=context['components'][0x5bd63b29]
        self.state=context['components'][0x43686585]
        system.worker.check_call_access()
        for address,count,digest in PINS:
            host.require(hashlib.sha256(system.read(address,count)).hexdigest()==digest,
                         'Original body correction function/call differs')
        self._identity()
        self.actor_id=system.word(self.actor+0x18)
        self.bail_board=(self.actor_id+0x884ef81b)&0xffffffff
        self.data=system.allocate(bytes(4096));self.code=system.allocate(bytes(4096))
        self.glue=wrapper(self.code,self.actor,self.core,self.state,self.data)
        host.require(len(self.glue)<=4096,'Body witness glue exceeds its owned page')
        system.write(self.code,self.glue)
        previous=C.c_ulong()
        host.success(api.VirtualProtectEx(process,self.code,4096,0x20,C.byref(previous)),
                     'Seal owned body witness code')
        host.require(previous.value==4,'Owned body witness was not writable')
        host.success(api.FlushInstructionCache(process,self.code,len(self.glue)),'Flush owned body witness')
        self.patch=(CALL,relative_call(CALL,TARGET),relative_call(CALL,self.code))
        try:self._patch(*self.patch)
        except BaseException:
            system.worker.command_failed=True
            raise
        log('body_witness_initialized',ring_frames=2,original_call=hex(TARGET),
            original_calls_once=True,host_per_frame_reads=0,host_per_frame_writes=0)

    def _identity(self):
        system=self.system;lo,hi=system.word(0x84376c),system.word(0x843770)
        for ptr,size,kind,vtable in ((self.core,0x19ac,0x5bd63b29,0x6a0d58),
                                    (self.state,0x140,0x43686585,0x6a1200)):
            host.require(ptr%4==0 and lo<=ptr and ptr+size<=hi and
                struct.unpack('<4I',system.read(ptr,16))[:2]==(vtable,kind) and
                system.word(ptr+12)==self.actor,'Body witness component ownership changed')
        host.require(self.actor%4==0 and lo<=self.actor and self.actor+0xd8<=hi and
            system.word(self.actor)==0x6989e8 and system.word(self.core+0x192c)==self.state,
            'Body witness actor/state ownership changed')
        if hasattr(self,'actor_id'):
            host.require(system.word(self.actor+0x18)==self.actor_id,'Body witness actor identity changed')

    def verify_patch(self):
        self.system.worker.check_call_access()
        address,_,replacement=self.patch
        host.require(self.system.read(address,5)==replacement and
                     self.system.read(self.code,len(self.glue))==self.glue,'Body witness original call/glue changed')
        for start,count,digest in PINS:
            code=self.system.read(start,count)
            if start<=address and address+5<=start+count:
                offset=address-start
                code=code[:offset]+self.patch[1]+code[offset+5:]
            host.require(hashlib.sha256(code).hexdigest()==digest,'Body witness original code changed')

    def record(self,frame):
        """Read only while the retained worker is idle, after that exact frame."""
        self.verify_patch();self._identity()
        host.require(type(frame)is int and 0<frame<2**64,'Body witness frame invalid')
        raw=self.system.read(self.data+64+(frame%2)*SLOT,SLOT)
        if struct.unpack_from('<Q',raw)[0]!=frame:return None
        dt,actor_id=struct.unpack_from('<fI',raw,8)
        host.require(actor_id==self.actor_id,'Body witness record actor differs')
        return dict(frame=frame,dt=dt,actor_id=actor_id,
            before=decode_record(raw[64:64+RECORD],self.actor,self.core,self.state),
            after=decode_record(raw[64+RECORD:64+2*RECORD],self.actor,self.core,self.state))

    def completed(self,sample):
        self.system.worker.check_call_access()
        host.require(struct.unpack('<Q',self.system.read(0x8459d0,8))[0]==sample['frame'],
                     'Body witness completed source frame changed')
        record=self.record(sample['frame'])
        motion=self.system.read(self.actor+0x68,112)
        host.require(motion[:12]==struct.pack('<3f',*sample['position']) and
            motion[16:28]==struct.pack('<3f',*sample['previous_position']) and
            motion[96:108]==struct.pack('<3f',*sample['velocity']),
            'Body witness completed actor sample differs')
        state=self.system.read(self.state+0x18,8);bail=self.system.read(self.state+0x78,1)
        host.require(struct.unpack('<2I',state)==(0,0) and bail==b'\x01',
                     'Body witness completed actor is not Ground bail')
        return record,dict(core_previous=struct.unpack('<3f',self.system.read(self.core+0xa8,12)),
            matrix=struct.unpack_from('<16f',motion,32),
            dt=struct.unpack('<f',self.system.read(self.core+0x9c,4))[0])


def candidate(previous,current,bail_board):
    if previous is None or not bail_board or current['frame']!=previous['frame']+1:return False
    for sample in (previous,current):
        if (any(sample.get(k)!=v for k,v in (('mode',0),('state',0),('walking_gate',0))) or
            sample.get('bailing') is not True or sample.get('board_visible') is not False or
            sample.get('detached_board_id')!=bail_board):return False
        if not all(math.isfinite(v) for v in sample['position']+sample['velocity']):return False
    return ORDINARY<math.dist(previous['position'],current['position'])*METRES<=ORDINARY+MAX_PUSH


def tolerance(*arrays):
    largest=max(abs(v) for values in arrays for v in values)
    # Four original float32 ULPs plus the original .001in floor publication.
    return 4*2.**(math.frexp(largest)[1]-24)+.001


def proves(previous,current,matrix,record,completed,bail_board):
    if not candidate(previous,current,bail_board) or record is None or record['frame']!=current['frame']:return False
    before,after=record['before'],record['after']
    if before is None or after is None:return False
    for phase in (before,after):
        if phase['calls']!=1 or phase['state']!=0 or phase['mode']!=0 or phase['bailing'] is not True:return False
    arrays=(before['position'],after['position'],after['velocity'],current['position'],
            current['previous_position'],current['velocity'],completed['core_previous'])
    if (any(len(v)!=16 for v in (matrix,before['matrix'],after['matrix'],completed['matrix'])) or
        any(len(v)!=3 for v in arrays) or not all(math.isfinite(v) for v in (record['dt'],completed['dt'])) or
        not all(math.isfinite(v) for a in arrays+(matrix,before['matrix'],after['matrix'],completed['matrix']) for v in a) or
        abs(record['dt']-1/60)>1.e-8 or abs(completed['dt']-1/60)>1.e-8):return False
    if (math.dist(previous['position'],before['position'])*METRES>ORDINARY or
        not 0<math.dist(before['position'],after['position'])*METRES<=MAX_PUSH):return False
    error=tolerance(*arrays)
    if (math.dist(current['previous_position'],after['position'])>error or
        math.dist(completed['core_previous'],after['position'])>error or
        math.dist(current['velocity'],after['velocity'])>tolerance(current['velocity'],after['velocity']) or
        any(math.dist(a,b)>1.e-5 for a,b in ((matrix,after['matrix']),
            (before['matrix'],after['matrix']),(matrix,completed['matrix'])))):
        return False
    # The observed authored hidden-board bail shift occurs after physics. The
    # exact original body correction must still explain the whole large jump.
    return (math.dist(current['position'],after['position'])<=error or
        math.dist(current['position'],[after['position'][i]-2.5*matrix[i] for i in range(3)])<=error)


class BodyCorrection:
    def __init__(self,witness):
        self.witness=witness
        self.previous=None

    def update(self,result):
        current=result['sample'];proved=False
        if candidate(self.previous,current,self.witness.bail_board):
            record,completed=self.witness.completed(current)
            proved=proves(self.previous,current,result['actor_matrix'],record,completed,self.witness.bail_board)
            self.witness.log('original_body_correction',frame=current['frame'],proved=proved,
                previous_position=self.previous['position'],position=current['position'],
                body=record,completed=completed)
        self.previous=current
        return proved
