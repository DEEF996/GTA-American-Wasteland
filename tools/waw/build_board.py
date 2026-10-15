"""Build an original THAW weighted board as an isolated T4 XModel donor.

Writes only --output. No game process access or installation. OAT performs the
native XModel conversion. Input glTF uses unchanged THAW Y-up model coordinates;
OAT maps them to CoD (x,-z,y). See its GltfLoader.cpp and IwiWriter6.cpp.
"""
import argparse
import base64
import collections
import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import zlib


def require(condition, message):
    if not condition:
        raise ValueError(message)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(',', ':'), allow_nan=False), encoding='utf-8')


def read_export_png(path):
    """Read our exporter's exact filter-zero RGBA PNGs, retaining source pixels."""
    data = path.read_bytes()
    require(data[:8] == b'\x89PNG\r\n\x1a\n', 'Expected PNG')
    at, chunks, dimensions = 8, [], None
    while at < len(data):
        size, = struct.unpack_from('>I', data, at)
        kind, payload = data[at+4:at+8], data[at+8:at+8+size]
        require(at+12+size <= len(data), 'Truncated PNG')
        require(zlib.crc32(kind+payload) == struct.unpack_from('>I', data, at+8+size)[0], 'PNG CRC mismatch')
        if kind == b'IHDR':
            w,h,depth,color,compression,filtering,interlace = struct.unpack('>2I5B', payload)
            require((depth,color,compression,filtering,interlace) == (8,6,0,0,0), 'Expected exported RGBA8 PNG')
            dimensions = w,h
        elif kind == b'IDAT':
            chunks.append(payload)
        at += size+12
    require(dimensions is not None, 'Missing PNG dimensions')
    w,h = dimensions
    raw = zlib.decompress(b''.join(chunks))
    require(len(raw) == h*(w*4+1), 'Invalid PNG scanline length')
    require(all(raw[y*(w*4+1)] == 0 for y in range(h)), 'Expected export filter zero')
    return w,h,b''.join(raw[y*(w*4+1)+1:(y+1)*(w*4+1)] for y in range(h))


def iwi_rgba(w,h,rgba):
    require(len(rgba) == w*h*4 and 0 < w <= 4096 and 0 < h <= 4096, 'Invalid image')
    bgra = bytes(c for i in range(0,len(rgba),4) for c in (rgba[i+2],rgba[i+1],rgba[i],rgba[i+3]))
    # IwiWriter6: format BITMAP_RGBA=1, NOMIPMAPS=2; one mip, 28-byte header.
    return b'IWi\x06'+struct.pack('<BB3H4I',1,2,w,h,1,28+len(bgra),0,0,0)+bgra


def make_gltf(model, rig):
    bones = rig['bones']
    require(len(bones) == 52 and all(b['index'] == i for i,b in enumerate(bones)), 'Expected original 52-bone rig')
    binary = bytearray()
    views, accessors = [], []
    def accessor(rows, fmt, component, shape, target=None):
        while len(binary)%4:
            binary.append(0)
        offset = len(binary)
        for row in rows:
            binary.extend(struct.pack('<'+fmt, *row))
        view = {'buffer':0,'byteOffset':offset,'byteLength':len(binary)-offset}
        if target is not None:
            view['target'] = target
        views.append(view)
        result = {'bufferView':len(views)-1,'componentType':component,'count':len(rows),'type':shape}
        if shape == 'VEC3' and fmt == '3f':
            result.update(min=[min(r[i] for r in rows) for i in range(3)],max=[max(r[i] for r in rows) for i in range(3)])
        accessors.append(result)
        return len(accessors)-1
    names = [b['name'] or f"thaw_bone_{b['index']}" for b in bones]
    require(len(set(n.lower() for n in names)) == 52, 'Duplicate bone names')
    nodes = [{'name':names[i], 'matrix':b['local_bind_row_major']} for i,b in enumerate(bones)]
    roots = []
    for i,bone in enumerate(bones):
        parent = bone['parent_index']
        if parent < 0:
            roots.append(i)
        else:
            require(parent < len(nodes), 'Invalid parent')
            nodes[parent].setdefault('children', []).append(i)
    queue, ordered = collections.deque(roots), []
    while queue:
        i = queue.popleft()
        require(i not in ordered, 'Cyclic rig')
        ordered.append(i)
        queue.extend(nodes[i].get('children', []))
    require(len(ordered)==52, 'Disconnected rig')
    native_index = {source:index for index,source in enumerate(ordered)}
    binding = [{'thaw_index':i,'waw_index_expected':native_index[i], 'checksum':b['checksum'],
                'name':names[i], 'parent_thaw_index':b['parent_index']} for i,b in enumerate(bones)]
    ibm = accessor([b['inverse_bind_row_major'] for b in bones],'16f',5126,'MAT4')
    materials = [{'name':f"mc/thaw_board_{m['checksum']:08x}"} for m in model['materials']]
    material_ids = {m['checksum']:i for i,m in enumerate(model['materials'])}
    primitives = []
    for mesh in model['meshes']:
        count = len(mesh['positions'])
        require(len(mesh['bone_indices']) == len(mesh['bone_weights']) == count, 'Board is not skinned')
        require(len(mesh['normals']) == count and mesh['uv_sets'], 'Missing source normals/UVs')
        require(all(0 <= b < 52 for row in mesh['bone_indices'] for b in row), 'Bone outside original rig')
        require(all(abs(sum(row)-1) < 1e-6 for row in mesh['bone_weights']), 'Invalid original weights')
        attributes = {
            'POSITION':accessor(mesh['positions'],'3f',5126,'VEC3',34962),
            'NORMAL':accessor(mesh['normals'],'3f',5126,'VEC3',34962),
            'TEXCOORD_0':accessor(mesh['uv_sets'][0],'2f',5126,'VEC2',34962),
            'JOINTS_0':accessor(mesh['bone_indices'],'4H',5123,'VEC4',34962),
            'WEIGHTS_0':accessor(mesh['bone_weights'],'4f',5126,'VEC4',34962),
        }
        if mesh['colors_bgra']:
            # THAW neutral color is128; preserve its brightness convention.
            colors = [[r/128,g/128,b/128,a/128] for b,g,r,a in mesh['colors_bgra']]
            attributes['COLOR_0'] = accessor(colors,'4f',5126,'VEC4',34962)
        primitives.append({'attributes':attributes,
                           'indices':accessor([[i] for t in mesh['indices'] for i in t],'H',5123,'SCALAR',34963),
                           'material':material_ids[mesh['material_checksum']],'mode':4})
    nodes.append({'name':'thaw_board_geometry','mesh':0,'skin':0})
    gltf = {'asset':{'version':'2.0','generator':'THAW_Bevy original board converter'},'scene':0,
            'scenes':[{'nodes':roots+[52]}],'nodes':nodes,'meshes':[{'name':'thaw_board','primitives':primitives}],
            'skins':[{'name':'THPS7_Human','joints':list(range(52)),'inverseBindMatrices':ibm}],
            'materials':materials,'buffers':[{'uri':'data:application/octet-stream;base64,'+base64.b64encode(binary).decode('ascii'),'byteLength':len(binary)}],
            'bufferViews':views,'accessors':accessors}
    return gltf,bytes(binary),binding


def build(args):
    model,rig,template = [json.loads(p.read_text(encoding='utf-8')) for p in (args.model,args.rig,args.material_template)]
    require(model['format']=='thaw-model-v1', 'Expected verified original model export')
    require(model.get('validation', {}).get('skin_weight_encoding') ==
            'native signed 11:11:10 /1023,/1023,/511; three weights; fourth zero',
            'Re-export the original model with the native-verified skin weight decoder')
    require(template['_game']=='t4' and template['cameraRegion']=='lit', 'Expected native lit T4 material')
    output = args.output.resolve()
    require(not (output/'board_manifest.json').exists(), 'Build output already contains a manifest')
    raw = output/'raw'
    for directory in ('images','materials/mc','model_export','xmodel'):
        (raw/directory).mkdir(parents=True,exist_ok=True)
    texture_map,texture_inventory = {},[]
    for texture in model['textures']:
        crc = texture['checksum']; name=f'thaw_board_{crc:08x}'
        w,h,rgba=read_export_png(args.model.parent/texture['filename'])
        data=iwi_rgba(w,h,rgba); path=raw/'images'/f'{name}.iwi';path.write_bytes(data)
        require(data[28:]==bytes(c for i in range(0,len(rgba),4) for c in (rgba[i+2],rgba[i+1],rgba[i],rgba[i+3])), 'Pixel readback failed')
        texture_map[crc]=name
        texture_inventory.append({'name':name,'file':str(path),'width':w,'height':h,'sha256':hashlib.sha256(data).hexdigest(),
                                  'source_rgba_sha256':hashlib.sha256(rgba).hexdigest()})
    material_names=[]
    for source in model['materials']:
        require(len(source['passes'])==1, 'Native adapter currently supports one source texture pass')
        source_pass=source['passes'][0]
        material=copy.deepcopy(template)
        # Verified native color-only sibling technique is referenced by common_mp
        # and localized_mp_airfield; no unrelated donor normal/specular maps.
        material['techniqueSet']='mc_l_sm_r0c0'
        material['textures']=[copy.deepcopy(next(t for t in template['textures'] if t.get('name')=='colorMap'))]
        tex=material['textures'][0]
        tex['image']=texture_map[source_pass['texture_checksum']]
        tex['samplerState'].update(clampU=bool(source_pass['addressing']&0xFFFF),clampV=bool(source_pass['addressing']>>16),mipMap='disabled')
        for state in material['stateBits']:
            if source['double_sided']:
                state['cullFace']='none'
            if source['use_alpha_cutoff'] and source['alpha_cutoff']>0 and state['alphaTest']=='disabled':
                state['alphaTest']='gt0'
        material['constants']=[c for c in material['constants'] if c.get('name')=='colorTint']
        name=f"mc/thaw_board_{source['checksum']:08x}";material_names.append(name)
        save_json(raw/'materials'/f'{name}.json',material)
    gltf,binary,binding=make_gltf(model,rig)
    save_json(raw/'model_export/thaw_board.gltf',gltf)
    (raw/'model_export/thaw_board.bin').write_bytes(binary)
    save_json(raw/'xmodel/thaw_board.json',{'$schema':'http://openassettools.dev/schema/xmodel.v1.json',
        '_game':'t4','_type':'xmodel','_version':2,'flags':0,'lods':[{'distance':1000000.0,'file':'model_export/thaw_board.gltf'}],'type':'animated'})
    shutil.copyfile(args.partclassification,raw/'partclassification.csv')
    source=output/'zone_source';source.mkdir(parents=True,exist_ok=True)
    zone=['>game,T4','techniqueset,,mc_l_sm_r0c0']+[f"image,{t['name']}" for t in texture_inventory]+[f'material,{n}' for n in material_names]+['xmodel,thaw_board']
    (source/'thaw_board.zone').write_text('\n'.join(zone)+'\n',encoding='ascii')
    manifest={'model':'thaw_board','bones':binding,'textures':texture_inventory,'materials':material_names,
              'external_references':[['techniqueset','mc_l_sm_r0c0']],
              'source_model':str(args.model.resolve()),'source_model_sha256':hashlib.sha256(args.model.read_bytes()).hexdigest(),
              'material_template':str(args.material_template.resolve()),
              'vertices':sum(len(m['positions']) for m in model['meshes']),
              'triangles':sum(len(m['indices']) for m in model['meshes']),
              'coordinates':'glTF unchanged THAW; OAT converts(x,y,z) to(x,-z,y)',
              'skin_weight_encoding':model['validation']['skin_weight_encoding'],
              'validation':{'source_pixels_preserved':True,'original_weights_preserved':True,'native_readback_pending':True}}
    if args.linker:
        command=[str(args.linker),'--base-folder',str(output),'--output-folder',str(output/'zone_out'),'thaw_board']
        result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace')
        (output/'build.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        require(result.returncode==0,f'Linker failed ({result.returncode}); see {output / "build.log"}')
        fastfile=output/'zone_out/thaw_board.ff';require(fastfile.is_file(),'Linker did not write donor')
        manifest.update(donor=str(fastfile),donor_sha256=hashlib.sha256(fastfile.read_bytes()).hexdigest())
    save_json(output/'board_manifest.json',manifest)
    print(json.dumps({k:v for k,v in manifest.items() if k not in ('bones','textures')},indent=2))


def validate_readback(output):
    """Validate an OAT --model-format GLTF --image-format IWI readback."""
    output=Path(output)
    manifest_path=output/'build/board_manifest.json'
    manifest=json.loads(manifest_path.read_text())
    source=json.loads(Path(manifest['source_model']).read_text())
    readback_path=output/'readback/model_export/thaw_board_lod0.gltf'
    doc=json.loads(readback_path.read_text())
    buffers=[base64.b64decode(b['uri'].split(',',1)[1]) for b in doc['buffers']]
    def accessor(index):
        a=doc['accessors'][index];v=doc['bufferViews'][a['bufferView']]
        fmt,size={5121:('B',1),5123:('H',2),5125:('I',4),5126:('f',4)}[a['componentType']]
        count={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}[a['type']]
        at=v.get('byteOffset',0)+a.get('byteOffset',0)
        stride=v.get('byteStride',count*size)
        return [struct.unpack_from('<'+fmt*count,buffers[v['buffer']],at+i*stride) for i in range(a['count'])]
    names=[doc['nodes'][i]['name'] for i in doc['skins'][0]['joints']]
    expected_names=[b['name'] for b in manifest['bones']]
    require(len(names)==52 and set(names)==set(expected_names),'Native readback bone names changed')
    for bone in manifest['bones']:
        index=names.index(bone['name'])
        require(index==bone['waw_index_expected'],'Unexpected OAT bone ordering')
        bone['waw_index_verified']=index
    def key(position,uv):
        return tuple(round(v,5) for v in position+uv)
    def triangle_key(vertices):
        return min(tuple(vertices[i:]+vertices[:i]) for i in range(3))
    original_triangles=collections.Counter();original_weights={};original_by_position={}
    for mesh in source['meshes']:
        keys=[key(p,uv) for p,uv in zip(mesh['positions'],mesh['uv_sets'][0])]
        for k,uv in zip(keys,mesh['uv_sets'][0]):
            original_by_position.setdefault(k[:3],[]).append((k,uv))
        material=f"mc/thaw_board_{mesh['material_checksum']:08x}"
        for triangle in mesh['indices']:
            original_triangles[(material,triangle_key([keys[i][:3] for i in triangle]))]+=1
        for k,bones,weights in zip(keys,mesh['bone_indices'],mesh['bone_weights']):
            weight_map=collections.defaultdict(float)
            for b,w in zip(bones,weights):
                if w>0:weight_map[expected_names[b]]+=w
            original_weights.setdefault(k,[]).append(dict(weight_map))
    native_triangles=collections.Counter();max_weight_error=0.;max_uv_error=0.;vertex_sets=set()
    for mesh in doc['meshes']:
        for primitive in mesh['primitives']:
            a=primitive['attributes'];positions=accessor(a['POSITION']);uvs=accessor(a['TEXCOORD_0'])
            keys=[]
            for p,uv in zip(positions,uvs):
                candidates=original_by_position.get(tuple(round(v,5) for v in p),[])
                require(candidates,'Native position not found in source')
                k,source_uv=min(candidates,key=lambda c:max(abs(c[1][i]-uv[i]) for i in range(2)))
                error=max(abs(source_uv[i]-uv[i]) for i in range(2))
                require(error<=max(0.002,0.001*max(abs(x) for x in source_uv)),'Native UV quantization exceeded tolerance')
                max_uv_error=max(max_uv_error,error)
                keys.append(k)
            material=doc['materials'][primitive['material']]['name']
            flat=[row[0] for row in accessor(primitive['indices'])]
            for i in range(0,len(flat),3):
                native_triangles[(material,triangle_key([keys[j][:3] for j in flat[i:i+3]]))]+=1
            signature=tuple(a.items())
            if signature in vertex_sets:continue
            vertex_sets.add(signature)
            for k,joints,weights in zip(keys,accessor(a['JOINTS_0']),accessor(a['WEIGHTS_0'])):
                require(k in original_weights,'Native vertex/UV not found in source')
                actual=collections.defaultdict(float)
                for joint,weight in zip(joints,weights):
                    if weight>0:actual[names[joint]]+=weight
                difference=min(max(abs(actual.get(n,0)-candidate.get(n,0)) for n in set(actual)|set(candidate)) for candidate in original_weights[k])
                max_weight_error=max(max_weight_error,difference)
    require(original_triangles==native_triangles,'Native triangle positions, winding or material changed')
    require(max_weight_error<=2e-5,'Native skin weight quantization exceeded tolerance')
    image_reports=[]
    for texture in manifest['textures']:
        original=Path(texture['file']);readback=output/'readback/images'/original.name
        require(original.read_bytes()==readback.read_bytes(),'Native image readback changed pixels/header')
        image_reports.append({'file':str(readback),'sha256':hashlib.sha256(readback.read_bytes()).hexdigest(),'byte_identical':True})
    report={'model':'thaw_board','donor_sha256':manifest['donor_sha256'],
            'native_gltf':str(readback_path),'native_gltf_sha256':hashlib.sha256(readback_path.read_bytes()).hexdigest(),
            'bone_count':52,'all_bone_names_preserved':True,'all_bone_indices_verified':True,
            'original_triangles':sum(original_triangles.values()),'native_triangles':sum(native_triangles.values()),
            'triangle_positions_winding_material_preserved':True,'max_weight_error':max_weight_error,
            'max_uv_quantization_error':max_uv_error,
            'images':image_reports,'image_pixels_require_iwd':True,
            'external_image_probe':str(output/'embedded-image-check.log')}
    save_json(output/'readback_validation.json',report)
    manifest['validation'].update(native_readback_pending=False,native_readback_passed=True,
                                  report=str(output/'readback_validation.json'),native_gltf_sha256=report['native_gltf_sha256'],
                                  all_52_names_preserved=True,triangle_roundtrip_passed=True,
                                  max_weight_error=max_weight_error,max_uv_quantization_error=max_uv_error,image_pixels_require_iwd=True)
    save_json(manifest_path,manifest)
    print(json.dumps(report,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('model','rig','material-template','partclassification','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--linker',type=Path)
    build(parser.parse_args())


if __name__=='__main__':
    main()
