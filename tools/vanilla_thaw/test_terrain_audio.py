"""Cache trust boundary and unchanged original native registration arguments."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import terrain_audio as t
import probe_audio_bank
import probe_audio_frame
from test_audio_bank import Memory


def fixture():
    rows, chunks = [], []
    for i in range(31):
        raw=bytes([i+1])*132
        rows.append(dict(checksum=10000+i,offset=i*256,pcm_bytes=4,frequency=22050,
                         flags=8 if i%2 else 0,pitch=100.,volume=80.,dropoff=0.,
                         sha256=hashlib.sha256(raw).hexdigest()))
        chunks.append(raw)
    metadata=dict(native_count=144,sources=[dict(samples=rows)])
    samples,size=t.sample_rows(metadata)
    payload=bytearray(size)
    for row,raw in zip(samples,chunks):payload[row['offset']:row['offset']+len(raw)]=raw
    return metadata,dict(source=bytes(payload),samples=samples,native_count=144,
                         sha256=hashlib.sha256(payload).hexdigest())


class TerrainAudioTests(unittest.TestCase):
    def test_native_wrapper_preserves_all_twelve_sample_register_arguments(self):
        args = list(range(0x1200, 0x120c))
        pack = lambda word: struct.pack('<I', word)
        prefix = b'\xc7\x05' + pack(0x9000) + pack(2)
        pushes = b''.join(b'\x68' + pack(word) for word in reversed(args))
        call = b'\xb8' + pack(0x440a40) + b'\xff\xd0'
        thiscall = t.host.native_call_stub(0x440a40, args, 0x9000, this=0x8000)
        self.assertTrue(thiscall.startswith(prefix + pushes + b'\xb9' + pack(0x8000) + call + b'\xa3'))
        cdecl = t.host.native_call_stub(0x440a40, args, 0x9000)
        self.assertTrue(cdecl.startswith(prefix + pushes + call + b'\x83\xc4\x30\xa3'))
        with self.assertRaisesRegex(RuntimeError, 'argument count'):
            t.host.native_call_stub(0x440a40, args + [0], 0x9000)

    def test_public_pins_have_unique_bounded_samples_without_private_paths(self):
        metadata=t.pins()
        rows,size=t.sample_rows(metadata)
        self.assertEqual(len(rows),31)
        self.assertLess(size,t.MAX_BUNDLE)
        for source in metadata['sources']:
            for key in ('pak','bank','member'):
                self.assertNotIn(':',source[key]); self.assertNotIn('..',source[key])

    def test_cache_pins_not_mutable_manifest_authorize_sample_bytes(self):
        metadata,inputs=fixture()
        with tempfile.TemporaryDirectory() as folder,patch.object(t,'pins',return_value=metadata):
            root=Path(folder)
            manifest=dict(format=t.FORMAT,pins_sha256=t.PINS_SHA,samples=inputs['samples'],
                source=str(root/'original'),bytes=len(inputs['source']),sha256=inputs['sha256'],source_files_modified=False)
            (root/'manifest.json').write_text(json.dumps(manifest))
            (root/'terrain-samples.bin').write_bytes(inputs['source'])
            self.assertEqual(t.read_cache(root,root/'original'),inputs)
            with self.assertRaisesRegex(RuntimeError,'another source'):
                t.read_cache(root,root/'other')
            changed=bytearray(inputs['source']);changed[129]^=1
            (root/'terrain-samples.bin').write_bytes(changed)
            manifest['sha256']=hashlib.sha256(changed).hexdigest()
            (root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(RuntimeError,'pinned original'):
                t.read_cache(root)

    def test_existing_destination_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'original';source.mkdir();target=root/'cache';target.mkdir()
            keep=target/'unrelated';keep.write_bytes(b'keep')
            with self.assertRaisesRegex(RuntimeError,'replace an existing'):
                t.prepare(source,target)
            self.assertEqual(keep.read_bytes(),b'keep')

    def test_registration_keeps_original_metadata_and_prior_bank_under_mutex(self):
        metadata,inputs=fixture()
        memory=Memory();calls=[];events=[]
        memory.put(0x84376c,0x100000,0x200000)
        memory.put(t.probe_audio.MANAGER,0x101000)
        memory.put(t.probe_audio.SAMPLES,113)
        before=bytes(113*32);memory.write(0x6e0f90,before)
        code=b'original whole method test fixture';pin=('sample',0x300000,len(code),hashlib.sha256(code).hexdigest())
        memory.write(pin[1],code)
        def allocate(raw):memory.write(0x400000,raw);return 0x400000
        def lock(module,name,args):events.append(name)
        memory.allocate,memory.call=allocate,lock
        def native(api,process,current,args,record,deadline,log,this):
            self.assertEqual(current,pin);self.assertEqual(this,0x101000)
            self.assertEqual(events,['EnterCriticalSection'])
            index=len(calls);sample=inputs['samples'][index]
            self.assertEqual(args[:8],[0x400000+sample['offset'],4,sample['checksum'],22050,int(bool(sample['flags']&8)),0,0,0])
            desc=0x110000+index*0x50
            memory.put(0x6e0f90+(113+index)*32,args[2],sample['flags']|(3<<16),args[9],args[10],args[8],desc,0,args[0])
            fields=[0]*18
            fields[7]=fields[14]=4;fields[9]=args[0];fields[16]=args[0]+128
            memory.put(desc,*fields)
            memory.put(t.probe_audio.SAMPLES,114+index)
            calls.append(args)
            return 1
        with patch.object(t,'pins',return_value=metadata),patch.dict(probe_audio_bank.PINS,SampleRegister=pin), \
                patch.object(t.host,'native_call',side_effect=native):
            result=t.register(None,None,None,None,lambda *a,**k:None,memory,inputs)
        self.assertEqual(result['samples'],144);self.assertEqual(len(calls),31)
        self.assertEqual(events,['EnterCriticalSection','LeaveCriticalSection'])
        self.assertEqual(memory.read(0x6e0f90,len(before)),before)
        self.assertEqual(memory.terrain_audio_samples,144)


if __name__=='__main__':unittest.main()
