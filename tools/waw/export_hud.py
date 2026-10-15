"""Export exact THAW PC HUD IMG pixels and FNT records to a new staging folder.

No game changes. IMG layout reference: NeversoftMultitool ThawImgFile.cs.
IMG scanlines and native UVs are retained; PNG rows follow the original loader flip.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from assets_models import dxt_rgba, png_bytes
from build_board import iwi_rgba, require


def decode_img(data):
    require(len(data)>=28 and data[:8]==bytes.fromhex('0dd0adab02001400'),'Unsupported THAW IMG')
    width,height=struct.unpack_from('<2H',data,12)
    mipcount,depth,compression,palette_depth=data[20:24]
    require(mipcount==1,'Expected single-mip HUD asset')
    compression={2:1,3:5}.get(compression,compression)
    cursor=24
    if compression:
        size,=struct.unpack_from('<I',data,cursor)
        require(cursor+4+size==len(data),'Unexpected compressed IMG length')
        rgba=dxt_rgba(data[cursor+4:],width,height,compression)
    else:
        palette=None
        if depth in (4,8):
            count,=struct.unpack_from('<I',data,cursor);cursor+=4
            require(palette_depth==32 and 0<count<=256,'Unsupported IMG palette')
            palette=[data[cursor+4*i:cursor+4*i+4] for i in range(count)]
            cursor+=count*4
        stride,lines=struct.unpack_from('<2H',data,cursor);cursor+=4
        require(lines==height and cursor+stride*lines==len(data),'Unexpected raw IMG length')
        pixels=data[cursor:]
        if palette is not None:
            require(depth==8 and stride==width,'Unsupported indexed HUD stride')
            require(max(pixels)<len(palette),'Invalid palette index')
            rgba=b''.join(palette[i] for i in pixels)
        else:
            require(depth==32 and stride==width*4,'Unsupported raw IMG encoding')
            rgba=bytes(c for i in range(0,len(pixels),4) for c in (pixels[i+2],pixels[i+1],pixels[i],pixels[i+3]))
    return width,height,rgba


def parse_font(data):
    height,baseline,start=struct.unpack_from('<3I',data)
    image,=struct.unpack_from('<I',data,0x178)
    require(380<=start<image and (image-start)%24==0,'Unexpected THAW PC font layout')
    glyphs=[]
    for offset in range(start,image,24):
        u0,v0,u1,v1,w,h,base,extra0,extra1=struct.unpack_from('<4f3h2B',data,offset)
        require(all(0<=v<=1 for v in (u0,v0,u1,v1)) and 0<=w<=2048 and 0<=h<=2048,'Invalid font record')
        glyphs.append({'uv':[u0,v0,u1,v1],'width':w,'height':h,'baseline':base,'extra_bytes':[extra0,extra1]})
    lookup=list(data[12:268])
    require(max(lookup)<len(glyphs),'Invalid font character map')
    return {'format':'thaw-pc-font-v1','height':height,'baseline':baseline,'lookup':lookup,
            'glyphs':glyphs,'source_metrics_offset':start,'source_atlas_offset':image,
            'spacing_header_raw':list(struct.unpack_from('<2h',data,0x134)),
            'native_metrics_semantics_verified':False},decode_img(data[image:])


def pak_member(path,key):
    pak=path.read_bytes();pab=path.with_name(path.name.replace('.pak.','.pab.'))
    backing=pab.read_bytes() if pab.exists() else pak
    at=0
    while at+32<=len(pak):
        fields=struct.unpack_from('<8I',pak,at)
        if fields[0] in (0xb524565f,0x2cb3ef3b):break
        if fields[3]==key:
            start=at+fields[1]-(len(pak) if pab.exists() else 0)
            require(0<=start<=len(backing)-fields[2],'Invalid PAK payload')
            return backing[start:start+fields[2]]
        at+=192 if fields[7]&32 else 32
    raise ValueError(f'Missing PAK member {key:08x}')


def export(root,output):
    require(not output.exists(),'Output folder must be new')
    output.mkdir(parents=True)
    assets=[]; textures=[]; fonts={}
    def save(name,source,payload,decoded,font=None):
        width,height,rgba=decoded
        png=output/(name+'.png');iwi=output/(name+'.iwi')
        png.write_bytes(png_bytes(width,height,rgba));iwi.write_bytes(iwi_rgba(width,height,rgba))
        row={'name':name,'source':str(source),'source_sha256':hashlib.sha256(payload).hexdigest(),
             'texture_index':len(textures),
             'width':width,'height':height,'rgba_sha256':hashlib.sha256(rgba).hexdigest(),
             'png':str(png.resolve()),'iwi':str(iwi.resolve()),'iwi_sha256':hashlib.sha256(iwi.read_bytes()).hexdigest()}
        if font:
            metrics=output/(name+'.json');metrics.write_text(json.dumps(font,indent=2),encoding='utf-8')
            row.update(metrics=str(metrics.resolve()),glyph_count=len(font['glyphs']))
            fonts[name.removeprefix('thaw_hud_')]={**font,'texture_index':len(textures)}
        assets.append(row)
        bgra=bytes(c for i in range(0,len(rgba),4) for c in (rgba[i+2],rgba[i+1],rgba[i],rgba[i+3]))
        textures.append((width,height,bgra))
    sprites=['balancemeter','balancemeter_2','balancemeter_bg','balancemeter_bg_2','balancearrow_glow','score_solo','score_small','special','specialbar']
    for name in sprites:
        source=root/'images/panelsprites'/(name+'.img.wpc');payload=source.read_bytes()
        save('thaw_hud_'+name,source,payload,decode_img(payload))
    for name in ['newtrickfont','newtimerfont']:
        source=root/'fonts'/(name+'.fnt.wpc');payload=source.read_bytes()
        font,decoded=parse_font(payload);save('thaw_hud_'+name,source,payload,decoded,font)
    source=root/'worlds/Global/global.pak.wpc'
    payload=pak_member(source,zlib.crc32(b'testtitle')^0xffffffff)
    font,decoded=parse_font(payload);save('thaw_hud_testtitle',str(source)+'#testtitle',payload,decoded,font)
    source=root/'mod/rethawed_sprites.pak.wpc'
    # Original live the_run_timer uses five base sprites and two rings of eight pieces.
    timer_keys=[0x73226971,0xb3fdc249,0xb865d849,0x3ffdc486,0xdc091d4d]
    for key in [0x905f8544,0x30fe0b08,0xd8f7c72f,0x2d7761ef,0xd806d65e,0x595bbe34]+timer_keys:
        payload=pak_member(source,key)
        save(f'thaw_hud_theme_{key:08x}',str(source)+f'#{key:08x}',payload,decode_img(payload))
    from PIL import Image  # Already installed; reTHAWed loads these source PNG sprites.
    for prefix in ('rt_watch_timer_','rt_watch_timer_fg_'):
        for index in range(1,9):
            name=prefix+str(index);key=zlib.crc32(name.encode('ascii'))^0xffffffff
            source=root/'images/panelsprites/RT'/(name+'.png');payload=source.read_bytes()
            require(payload[:8]==b'\x89PNG\r\n\x1a\n','Invalid original timer PNG')
            with Image.open(io.BytesIO(payload)) as image:
                width,height=image.size
                require(0<width<=4096 and 0<height<=4096,'Invalid timer sprite dimensions')
                # Original rthw PNG loader RVA0x53952 copies source rows into reversed destination rows.
                decoded=width,height,image.convert('RGBA').transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()
            save(f'thaw_hud_theme_{key:08x}',source,payload,decoded)
    require(len(textures)<=64,'Native HUD texture limit')
    binary=b'THAWTEX1'+struct.pack('<2I',1,len(textures))
    for w,h,bgra in textures:
        binary+=struct.pack('<3I',w,h,len(bgra))+bgra
    bundle=output/'hud_textures.bin';bundle.write_bytes(binary)
    # Validate boundaries and exact decoded pixels through the reader contract.
    at=16
    for w,h,bgra in textures:
        require(struct.unpack_from('<3I',binary,at)==(w,h,len(bgra)),'Bundle header mismatch')
        at+=12;require(binary[at:at+len(bgra)]==bgra,'Bundle pixel mismatch');at+=len(bgra)
    require(at==len(binary),'Bundle trailing bytes')
    hud={'format':'thaw-hud-assets-v1','virtual_resolution':[640,448],
         'textures':[{k:a[k] for k in ['name','texture_index','width','height','source','source_sha256','rgba_sha256']} for a in assets],
         'fonts':fonts,'bundle':str(bundle.resolve()),'bundle_sha256':hashlib.sha256(binary).hexdigest(),
         'scanline_policy':'IMG rows unchanged; PNG rows reversed by original rthw loader; native UVs unchanged'}
    (output/'hud_assets.json').write_text(json.dumps(hud,indent=2),encoding='utf-8')
    manifest={'format':'thaw-hud-assets-v1','scanline_policy':'IMG rows unchanged; PNG rows reversed by original rthw loader; native UVs unchanged',
              'assets':assets,'installed':False,'native_donor_built':False}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'manifest':str(output/'manifest.json'),'assets':len(assets),'fonts':len(fonts),'bundle_bytes':len(binary),'bundle_sha256':hud['bundle_sha256']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args();export(args.root,args.output)
