"""Opt-in, temporary diagnostics around unchanged original physics calls.

No acceptance policy uses this trace. Each call still runs exactly once. The
owned host retains two frames of integer-only pre/post copies; normal gameplay
does not install it unless GTA_THAW_GROUND_TRACE=1 is explicitly set.
"""
import ctypes as C
import hashlib
import math
import os
import struct

import probe_loader as host

PINS=(
    (0x5e89af,48,'ac10a22dba762ba4843e40505c9a03a67cbc6a8b5953c6ee38beee1212956849'),
    (0x5e9022,12,'e0df100b319c7c5b58d97e027f860f2616b54d7af9d7724c8f4578fa37c9ae5b'),
    (0x5d2e90,1539,'1b0c1070afec81d06670afb65ed3e230a5fb8beee2a8f758bfd65fa9d36b6752'),
    (0x5e4370,5168,'6d445020828242c823b6fd172d99156c7a7d48943e9f500aeca11547ef62ea6b'),
    (0x5df9f0,5388,'eebe1013bef827d956e3f1e57cab4f8580bc9f2a9f8e54fdbfdf873c8b08f16c'),
    (0x5d8650,844,'2328ff5a372c85d284e0a5213c86d0697faa101fc4c911493bc52a30ae80102f'),
)
CALLS=((0x5e89b1,0x5e4370,0),(0x5e0e1f,0x5d8650,2),(0x5e89ba,0x5df9f0,4))
STAGES=('ground_before','ground_after','ceiling_before','ceiling_after',
        'air_before','air_after','components_after','spawned_scripts_after')
NATIVE_STAGES=6
SLOT=1024
RECORD=160


def relative_call(address,target):
    return b'\xe8'+struct.pack('<I',(target-address-5)&0xffffffff)


def copy_stub(actor,core,state,header,index):
    """Preserve EFLAGS and every integer register; never touch x87/SSE state."""
    pack=lambda x:struct.pack('<I',x)
    out=bytearray(b'\x9c\x60') # PUSHFD, PUSHAD; wrapper's retained input ECX at ESP+36.
    out+=b'\x81\x7c\x24\x24'+pack(core)+b'\x0f\x85';skip1=len(out);out+=bytes(4)
    out+=b'\xa1'+pack(core+12)+b'\x3d'+pack(actor)+b'\x0f\x85';skip2=len(out);out+=bytes(4)
    out+=b'\x8b\x3d'+pack(header)+b'\x85\xff\x0f\x84';skip3=len(out);out+=bytes(4)
    out+=b'\x81\xc7'+pack(64+index*RECORD) # EDI points to a record in selected owned slot.
    out+=b'\xff\x07\xc7\x47\x04'+pack(actor)+b'\xc7\x47\x08'+pack(core)
    out+=b'\xc7\x47\x0c'+pack(state)+b'\x83\xc7\x10\xfc'
    # Fixed owned addresses only, including preserved vector W tags.
    for source,count in ((actor+0x68,28),(core+0xa8,4),(state+0x18,2),(state+0x78,1)):
        out+=b'\xbe'+pack(source)+b'\xb9'+pack(count)+b'\xf3\xa5'
    out+=b'\xc7\x07'+pack(0x47545243)
    end=len(out)
    for offset in (skip1,skip2,skip3):struct.pack_into('<i',out,offset,end-offset-4)
    out+=b'\x61\x9d'
    return bytes(out)


def wrapper(address,target,actor,core,state,header,index):
    before=b'\x51'+copy_stub(actor,core,state,header,index) # Keep input ECX across original volatile registers.
    after=copy_stub(actor,core,state,header,index+1)
    return before+relative_call(address+len(before),target)+after+b'\x8d\x64\x24\x04\xc3'


def decode_record(raw,actor,core,state):
    host.require(len(raw)==RECORD,'Ground trace record extent differs')
    count,a,c,s=struct.unpack_from('<4I',raw)
    if not count:return None
    host.require(count<=8 and (a,c,s)==(actor,core,state) and
                 struct.unpack_from('<I',raw,156)[0]==0x47545243,
                 'Ground trace record ownership or completion differs')
    values=struct.unpack_from('<32f',raw,16)
    host.require(all(math.isfinite(x) for x in values),'Ground trace contains nonfinite actor data')
    return dict(calls=count,position=values[:3],previous_position=values[4:7],
                matrix=values[8:24],velocity=values[24:27],core_previous=values[28:31],
                state=struct.unpack_from('<I',raw,144)[0],mode=struct.unpack_from('<I',raw,148)[0],
                bailing=bool(raw[152]))


class GroundTrace:
    def __init__(self,api,process,system,context,log,*,body_witness=None):
        self.api,self.process,self.system,self.log=api,process,system,log
        self.actor=context['actor'];self.core=context['components'][0x5bd63b29]
        self.state=context['components'][0x43686585]
        self.frame=0;self.stages={};self.patches=[]
        self.body_witness=body_witness
        host.require(body_witness is not None,'Optional ground trace requires the shared body witness')
        system.worker.check_call_access()
        if body_witness is not None:body_witness.verify_patch()
        for address,count,digest in PINS:
            code=system.read(address,count)
            if body_witness is not None:
                call,old,new=body_witness.patch
                if address<=call and call+5<=address+count:
                    offset=call-address
                    host.require(code[offset:offset+5]==new,'Shared body trace call differs')
                    code=code[:offset]+old+code[offset+5:]
            host.require(hashlib.sha256(code).hexdigest()==digest,
                         'Original ground trace call/function differs')
        lo,hi=system.word(0x84376c),system.word(0x843770)
        for ptr,size,kind,vtable in ((self.core,0x19ac,0x5bd63b29,0x6a0d58),
                                    (self.state,0x140,0x43686585,0x6a1200)):
            host.require(ptr%4==0 and lo<=ptr and ptr+size<=hi and
                         struct.unpack('<4I',system.read(ptr,16))[:2]==(vtable,kind) and
                         system.word(ptr+12)==self.actor,'Ground trace component ownership differs')
        host.require(lo<=self.actor and self.actor+0xd8<=hi and
                     system.word(self.core+0x192c)==self.state,'Ground trace actor/state ownership differs')
        self.data=system.allocate(bytes(4096))
        self.code=system.allocate(bytes(4096))
        code=bytearray()
        for call,target,index in CALLS:
            if body_witness is not None and call==body_witness.patch[0]:continue
            address=self.code+len(code)
            emitted=wrapper(address,target,self.actor,self.core,self.state,self.data,index)
            self.patches.append((call,relative_call(call,target),relative_call(call,address)))
            code+=emitted
        host.require(len(code)<=4096,'Ground trace glue exceeds its owned page')
        system.write(self.code,bytes(code))
        previous=C.c_ulong()
        host.success(api.VirtualProtectEx(process,self.code,4096,0x20,C.byref(previous)),
                     'Seal owned ground trace code')
        host.require(previous.value==4,'Owned ground trace was not writable')
        host.success(api.FlushInstructionCache(process,self.code,len(code)),'Flush owned ground trace')
        self.glue=bytes(code)
        try:
            for address,old,new in self.patches:self._patch(address,old,new)
        except BaseException:
            system.worker.command_failed=True
            raise
        log('ground_trace_enabled',format='stock-ground-trace-v1',ring_frames=2,
            calls=[hex(row[0]) for row in CALLS],replacement_physics=False,
            diagnostic_only=True,original_calls_once=True,shared_body_witness=body_witness is not None)

    def _patch(self,address,expected,replacement):
        host.require(self.system.read(address,5)==expected,'Ground trace callsite changed')
        previous=C.c_ulong()
        host.success(self.api.VirtualProtectEx(self.process,address,5,4,C.byref(previous)),
                     'Open owned ground trace callsite')
        try:
            host.require(previous.value==0x20,'Original ground trace callsite protection differs')
            self.system.write(address,replacement)
        finally:
            ignored=C.c_ulong()
            host.success(self.api.VirtualProtectEx(self.process,address,5,previous.value,C.byref(ignored)),
                         'Restore original ground code protection')
        host.success(self.api.FlushInstructionCache(self.process,address,5),'Flush diagnostic callsite')
        host.require(self.system.read(address,5)==replacement,'Ground trace callsite write differs')

    def begin(self,frame):
        self.system.worker.check_call_access()
        host.require(type(frame)is int and 0<frame<2**32 and frame==self.frame+1,'Ground trace frame is not consecutive')
        self.frame=frame
        slot=self.data+64+(frame%2)*SLOT
        self.system.write(slot,struct.pack('<Q',frame)+bytes(SLOT-8))
        self.system.write(self.data,struct.pack('<I',slot))
        self.stages={key:value for key,value in self.stages.items() if key>=frame-1}

    def original_bytes(self,address,size):
        """Normalize only verified owned CALL operands for an original code pin.

        The optional nested ceiling trace sits inside the whole AirUpdate pin.
        Its wrapper must remain exact; no other original instruction is hidden.
        """
        self.system.worker.check_call_access()
        code=self.system.read(address,size)
        for call,old,new in self.patches:
            if address<=call and call+5<=address+size:
                offset=call-address
                host.require(code[offset:offset+5]==new and
                    self.system.read(self.code,len(self.glue))==self.glue,'Optional physics trace code changed')
                code=code[:offset]+old+code[offset+5:]
        return code

    def stage(self,name,frame):
        host.require(name in STAGES[NATIVE_STAGES:] and frame==self.frame,'Ground trace stage frame differs')
        # Diagnostic-only host phase observations, retained in a two-frame ring.
        raw=struct.pack('<4I',1,self.actor,self.core,self.state)
        raw+=self.system.read(self.actor+0x68,112)+self.system.read(self.core+0xa8,16)
        raw+=self.system.read(self.state+0x18,8)+self.system.read(self.state+0x78,4)
        raw+=struct.pack('<I',0x47545243)
        row=decode_record(raw,self.actor,self.core,self.state)
        row['floor_clearance']=struct.unpack('<f',self.system.read(self.state+0x134,4))[0]
        self.stages.setdefault(frame,{})[name]=row

    def snapshot(self):
        self.system.worker.check_call_access()
        host.require(self.system.read(self.code,len(self.glue))==self.glue,'Ground trace glue changed')
        for address,old,new in self.patches:
            host.require(self.system.read(address,5)==new,'Ground trace callsite differs')
        result=[]
        for frame in (self.frame-1,self.frame):
            if frame<1:continue
            raw=self.system.read(self.data+64+(frame%2)*SLOT,SLOT)
            host.require(struct.unpack_from('<Q',raw)[0]==frame,'Ground trace ring frame differs')
            rows={name:decode_record(raw[64+i*RECORD:64+(i+1)*RECORD],self.actor,self.core,self.state)
                  for i,name in enumerate(STAGES[:NATIVE_STAGES])}
            if self.body_witness is not None:
                body=self.body_witness.record(frame)
                rows['body_push_before']=body['before'] if body else None
                rows['body_push_after']=body['after'] if body else None
            rows.update(self.stages.get(frame,{}));result.append(dict(frame=frame,stages=rows))
        return dict(format='stock-ground-trace-v1',frames=result)


def initialize(api,process,system,context,log,*,body_witness=None):
    if os.environ.get('GTA_THAW_GROUND_TRACE')!='1':return None
    return GroundTrace(api,process,system,context,log,body_witness=body_witness)
