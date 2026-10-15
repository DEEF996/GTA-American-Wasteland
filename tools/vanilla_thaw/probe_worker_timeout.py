"""Failure-only snapshots of the two owned x86 runtime threads.

WOW64_CONTEXT follows the local Windows SDK winnt.h definition. No context is
written, no native function is invoked, and every successful suspend is paired
with a resume before returning, including when a read fails.
"""
import ctypes as C
from ctypes import wintypes as W
import struct
import time


class FloatingSave(C.Structure):
    _fields_=[(name,C.c_uint32) for name in ('ControlWord','StatusWord','TagWord',
        'ErrorOffset','ErrorSelector','DataOffset','DataSelector')]+[
        ('RegisterArea',C.c_ubyte*80),('Cr0NpxState',C.c_uint32)]


class Context(C.Structure):
    _pack_=4
    _fields_=[(name,C.c_uint32) for name in ('ContextFlags','Dr0','Dr1','Dr2','Dr3','Dr6','Dr7')]+[
        ('FloatSave',FloatingSave)]+[(name,C.c_uint32) for name in
        ('SegGs','SegFs','SegEs','SegDs','Edi','Esi','Ebx','Edx','Ecx','Eax',
         'Ebp','Eip','SegCs','EFlags','Esp','SegSs')]+[('ExtendedRegisters',C.c_ubyte*512)]


assert C.sizeof(Context)==716 and Context.Eip.offset==184 and Context.Esp.offset==196


def capture_thread(api, process, thread, tid, read):
    pid=api.GetProcessId(process)
    if not pid or api.GetProcessIdOfThread(thread)!=pid or api.GetThreadId(thread)!=tid:
        raise ValueError('Timeout thread does not belong to the owned runtime')
    result=dict(pid=pid,tid=tid)
    times=[W.FILETIME() for _ in range(4)]
    if api.GetThreadTimes(thread,*(C.byref(value) for value in times)):
        result.update({key:value.dwHighDateTime<<32|value.dwLowDateTime for key,value in
                       zip(('created_100ns','exited_100ns','kernel_100ns','user_100ns'),times)})
    previous=api.SuspendThread(thread)
    if previous==0xffffffff:raise C.WinError(C.get_last_error())
    try:
        context=Context();context.ContextFlags=0x10007
        if not api.Wow64GetThreadContext(thread,C.byref(context)):
            raise C.WinError(C.get_last_error())
        result['registers']={name.lower():getattr(context,name) for name in
            ('Eip','Esp','Ebp','Eax','Ebx','Ecx','Edx','Esi','Edi','EFlags')}
        if 0x10000<=context.Esp<=0xffffffff-256:
            result['stack_words']=list(struct.unpack('<64I',read(context.Esp,256)))
        if 0x10000<=context.Eip<=0xffffffff-16:
            result['instruction_hex']=read(context.Eip,16).hex()
    finally:
        resumed=api.ResumeThread(thread)
        result['suspend_balanced']=resumed==previous+1
        if resumed!=previous+1:
            raise RuntimeError('Could not restore owned timeout thread suspend count')
    return result


def collect(api, process, worker):
    import probe_loader as host
    import probe_resources
    if worker.process!=process:raise ValueError('Timeout worker handle differs')
    declarations={
        'GetProcessId':([W.HANDLE],W.DWORD),
        'GetProcessIdOfThread':([W.HANDLE],W.DWORD),
        'GetThreadId':([W.HANDLE],W.DWORD),
        'GetThreadTimes':([W.HANDLE,*([C.POINTER(W.FILETIME)]*4)],W.BOOL),
        'Wow64GetThreadContext':([W.HANDLE,C.POINTER(Context)],W.BOOL),
        'DuplicateHandle':([W.HANDLE,W.HANDLE,W.HANDLE,C.POINTER(W.HANDLE),W.DWORD,W.BOOL,W.DWORD],W.BOOL),
    }
    for name,(arguments,result) in declarations.items():
        function=getattr(api,name);function.argtypes=arguments;function.restype=result
    started=time.monotonic()
    read=lambda address,size:host.remote_read(api,process,address,size)
    result={}
    for name,thread,tid in [('command',worker.thread,worker.tid)]:
        try:result[name]=capture_thread(api,process,thread,tid,read)
        except (OSError,ValueError,RuntimeError) as error:result[name]={'error':str(error)}
    slots=probe_resources.read_metadata()['globals']
    result['file_service']={name:struct.unpack('<I',read(slots[name],4))[0] for name in
        ('file_active_count','file_pending_count','file_worker_handle')}
    if result['file_service']['file_worker_handle'] and time.monotonic()-started<.08:
        thread=W.HANDLE()
        # Reuse the original owned handle; a fresh OpenThread can be denied.
        if api.DuplicateHandle(process,result['file_service']['file_worker_handle'],
                               W.HANDLE(-1),C.byref(thread),0,False,2):
            try:
                tid=api.GetThreadId(thread)
                try:result['file_thread']=capture_thread(api,process,thread,tid,read)
                except (OSError,ValueError,RuntimeError) as error:result['file_thread']={'error':str(error)}
            finally:api.CloseHandle(thread)
        else:result['file_thread']={'error':'DuplicateHandle failed','winerror':C.get_last_error()}
    result['elapsed_ms']=(time.monotonic()-started)*1000
    return result
