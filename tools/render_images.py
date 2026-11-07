#!/usr/bin/env python3
"""Render the README images (sections, cutaway, bottom, exploded, printed parts)
straight from hardware/mesh/soul-urn.blend. No Blender needed: the .blend file is
parsed directly. Needs numpy and Pillow.

    python3 tools/render_images.py                 # all images -> hardware/images/
    python3 tools/render_images.py cutaway bottom  # just some
"""
import math, os, re, struct, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLEND=os.path.join(ROOT,'hardware','mesh','soul-urn.blend')
OUTDIR=os.path.join(ROOT,'hardware','images')
ME=None
np.seterr(all="ignore")
class Blend:
    def __init__(s, path):
        d = s.d = read_blend_bytes(path)
        assert d[:7]==b'BLENDER'
        hs = int(d[7:9]); s.blocks=[]; s.byptr={}
        o = hs; s.ids={}; s.owner_map={}; s.blkowner=[]
        while o < len(d):
            code = d[o:o+4]; sdna, = struct.unpack_from('<i',d,o+4)
            ptr, ln, nr = struct.unpack_from('<Qqq',d,o+8)
            o += 32
            b=(code.rstrip(b'\0').decode('latin1'),sdna,ptr,ln,nr,o)
            s.blocks.append(b)
            if code[:4]!=b'DATA': s.cur=len(s.blocks)-1; s.ids={}
            if ptr: s.byptr[ptr]=b; s.ids[ptr]=b
            s.owner_map.setdefault(id(s.ids),s.ids)
            s.blkowner.append(s.ids)
            o += ln
            if code==b'ENDB': break
        s._dna(next(b for b in s.blocks if b[0]=='DNA1'))
    def _dna(s,b):
        d=s.d; base=b[5]; o=b[5]+4
        def al(o): return base+((o-base+3)&~3)
        assert d[o:o+4]==b'NAME'; o+=4; n,=struct.unpack_from('<i',d,o); o+=4
        names=[]
        for _ in range(n):
            e=d.index(b'\0',o); names.append(d[o:e].decode()); o=e+1
        o=al(o); assert d[o:o+4]==b'TYPE',d[o:o+4]; o+=4; n,=struct.unpack_from('<i',d,o); o+=4
        types=[]
        for _ in range(n):
            e=d.index(b'\0',o); types.append(d[o:e].decode()); o=e+1
        o=al(o); assert d[o:o+4]==b'TLEN'; o+=4
        tlen=list(struct.unpack_from('<%dh'%n,d,o)); o+=2*n; o=al(o)
        assert d[o:o+4]==b'STRC'; o+=4; n,=struct.unpack_from('<i',d,o); o+=4
        s.structs=[]; s.sidx={}
        for i in range(n):
            t,c=struct.unpack_from('<hh',d,o); o+=4
            fields=[]; off=0
            for _ in range(c):
                ft,fn=struct.unpack_from('<hh',d,o); o+=4
                name=names[fn]; tn=types[ft]
                arr=1
                for m in re.findall(r'\[(\d+)\]',name): arr*=int(m)
                base=re.sub(r'\[.*','',name)
                isptr=base.startswith('*') or base.startswith('(*')
                size=(8 if isptr else tlen[ft])*arr
                clean=base.lstrip('*').replace('(*','').replace(')()','').replace(')','')
                fields.append((clean,tn,isptr,arr,off,size,name))
                off+=size
            s.structs.append((types[t],fields)); s.sidx[types[t]]=i
        s.types=types; s.tlen=tlen
    def fields(s,sdna): return {f[0]:f for f in s.structs[sdna][1]}
    def sname(s,sdna): return s.structs[sdna][0]
    def get(s,b,field,idx=0):
        f=s.fields(b[1])[field]; o=b[5]+idx*s.tlen[s.types.index(s.sname(b[1]))]+f[4]
        return s.read(o,f)
    def read(s,o,f):
        name,tn,isptr,arr,off,size,raw=f; d=s.d
        if isptr:
            v=struct.unpack_from('<%dQ'%arr,d,o); return v[0] if arr==1 else v
        if tn=='char': 
            r=d[o:o+size]; return r.split(b'\0')[0].decode('utf8','replace')
        fmt={'float':'f','int':'i','short':'h','double':'d','int64_t':'q','uint64_t':'Q','uint':'I','ushort':'H','int8_t':'b','uchar':'B','int32_t':'i','uint32_t':'I','int16_t':'h','uint16_t':'H','uint8_t':'B','bool':'?'}.get(tn)
        if fmt is None: return ('struct',tn,o)
        v=struct.unpack_from('<%d%s'%(arr,fmt),d,o); return v[0] if arr==1 else v
    def sub(s,o,tn,field):
        st=s.structs[s.sidx[tn]][1]; f={x[0]:x for x in st}[field]; return s.read(o+f[4],f)
    def data(s,ptr,ctx=None):
        if ctx is not None:
            i=s.blocks.index(ctx) if not isinstance(ctx,int) else ctx
            r=s.blkowner[i].get(ptr)
            if r: return r
        return s.byptr.get(ptr)

def read_blend_bytes(path):
    raw=open(path,'rb').read()
    if raw[:4]!=b'\x28\xb5\x2f\xfd': return raw
    try:
        from compression import zstd; return zstd.decompress(raw)
    except ImportError: pass
    try:
        import zstandard; return zstandard.ZstdDecompressor().decompressobj().decompress(raw)
    except ImportError: pass
    import subprocess
    return subprocess.run(['zstd','-d','-c',path],check=True,capture_output=True).stdout

MESHES={}
def extract(path):
    global ME
    b=Blend(path)
    def idname(blk): return b.sub(blk[5],'ID','name')[2:]
    def arr(ptr,dt,cols=1):
        blk=b.data(ptr,ME); a=np.frombuffer(b.d,dtype=dt,count=blk[3]//np.dtype(dt).itemsize,offset=blk[5]); return a.reshape(-1,cols) if cols>1 else a

    info=[]
    for blk in b.blocks:
        if blk[0]!='OB' or b.get(blk,'type')!=1: continue
        me=b.data(b.get(blk,'data')); ME=me; f=b.fields(me[1]); o=me[5]+f['attribute_storage'][4]
        ap=b.sub(o,'AttributeStorage','dna_attributes'); an=b.sub(o,'AttributeStorage','dna_attributes_num')
        ab=b.data(ap,me); sz=b.tlen[b.types.index('Attribute')]; at={}
        for i in range(an):
            oo=ab[5]+i*sz; nm=b.data(b.sub(oo,'Attribute','name'),me); name=b.d[nm[5]:nm[5]+nm[3]].split(b'\0')[0].decode()
            st=b.sub(oo,'Attribute','storage_type'); dp=b.sub(oo,'Attribute','data')
            if st==0: at[name]=b.sub(b.data(dp,me)[5],'AttributeArray','data')

        pos=arr(at['position'],'<f4',3).astype(np.float64)
        cv=arr(at['.corner_vert'],'<i4'); po=arr(b.get(me,'poly_offset_indices'),'<i4')[:b.get(me,'totpoly')+1]
        tris=[]
        sizes=np.diff(po)
        for k in np.unique(sizes):
            starts=po[:-1][sizes==k]
            for j in range(1,k-1):
                tris.append(np.stack([cv[starts],cv[starts+j],cv[starts+j+1]],1))
        tris=np.concatenate(tris)
        rm=b.get(blk,'rotmode'); 
        R=euler(b.get(blk,'rot'),rm) if rm>0 else None
        if R is None:
            w,x,y,z=b.get(blk,'quat'); R=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
        S=np.array(b.get(blk,'size')); L=np.array(b.get(blk,'loc'))
        assert not b.get(blk,'parent')
        W=(pos*S)@R.T+L
        nm=idname(blk); MESHES[nm]=(W,tris.astype(np.int32))

def euler(r,mode):
    x,y,z=r
    Rx=np.array([[1,0,0],[0,math.cos(x),-math.sin(x)],[0,math.sin(x),math.cos(x)]])
    Ry=np.array([[math.cos(y),0,math.sin(y)],[0,1,0],[-math.sin(y),0,math.cos(y)]])
    Rz=np.array([[math.cos(z),-math.sin(z),0],[math.sin(z),math.cos(z),0],[0,0,1]])
    assert mode==1,mode  # XYZ
    return Rz@Ry@Rx


def load(name): return MESHES[name]

def look(eye, target, up=(0,0,1)):
    eye=np.array(eye,float); t=np.array(target,float)
    f=t-eye; f/=np.linalg.norm(f); r=np.cross(f,up); r/=np.linalg.norm(r); u=np.cross(r,f)
    return eye, np.stack([r,u,f])

def render(objs, eye, target, size=(1200,1500), ortho=None, fov=30, ss=2,
           clip=None, offsets=None, light=(-0.4,-0.7,0.6), bg=(255,255,255), outline=True, up=(0,0,1), caps=True):
    """objs: list of (name, rgb). clip: (point, normal) - fragments with (p-point).n > 0 are removed."""
    W,H=size[0]*ss,size[1]*ss
    eye,R=look(eye,target,up)
    zbuf=np.full(W*H,np.inf,np.float32); tid=np.full(W*H,-1,np.int64)
    shade_all=[]; obj_all=[]; back_all=[]; base=0
    L=np.array(light,float); L/=np.linalg.norm(L)
    for oi,(name,col) in enumerate(objs):
        v,f=load(name)
        if offsets and name in offsets: v=v+np.array(offsets[name],float)
        c=(v-eye)@R.T  # x right, y up, z forward
        if ortho:
            s=H/ortho; sx=W/2+c[:,0]*s; sy=H/2-c[:,1]*s
        else:
            fpx=(H/2)/np.tan(np.radians(fov)/2); sx=W/2+c[:,0]/c[:,2]*fpx; sy=H/2-c[:,1]/c[:,2]*fpx
        dz=c[:,2]
        planes=[] if clip is None else (clip if isinstance(clip,list) else [clip])
        sds=[(v-np.array(p))@np.array(nn,float) for p,nn in planes]
        if planes:
            rem=np.ones(len(f),bool)
            for sd in sds: rem&=np.all(sd[f]>0,1)
            f=f[~rem]
        a,b_,cc=v[f[:,0]],v[f[:,1]],v[f[:,2]]
        n=np.cross(b_-a,cc-a); nl=np.linalg.norm(n,axis=1); nl[nl==0]=1; n/=nl[:,None]
        # facing: dot(normal, view dir) <0 => front
        cen=(a+b_+cc)/3
        vd=(R[2][None,:] if ortho else cen-eye)
        front=np.einsum('ij,ij->i',n,vd)<0
        lam=np.clip(np.einsum('ij,j->i',n,L),0,1); lam2=np.clip(np.einsum('ij,j->i',-n,L),0,1)
        shade=np.where(front,lam,lam2)
        shade_all.append(shade); obj_all.append(np.full(len(f),oi)); back_all.append(~front)
        X=sx[f]; Y=sy[f]; Z=dz[f]; Ds=[sd[f] for sd in sds]
        x0=np.floor(X.min(1)-0.5).astype(int); x1=np.floor(X.max(1)-0.5).astype(int)+1
        y0=np.floor(Y.min(1)-0.5).astype(int); y1=np.floor(Y.max(1)-0.5).astype(int)+1
        bw=x1-x0+1; bh=y1-y0+1
        ok=(x1>=0)&(y1>=0)&(x0<W)&(y0<H)
        ids=np.nonzero(ok)[0]
        bwc=np.minimum(bw[ids],4096); bhc=np.minimum(bh[ids],4096)
        def bucket(x): 
            out=np.ones_like(x)
            while True:
                m=out<x
                if not m.any(): return out
                out[m]*=2
        kw=bucket(bwc); kh=bucket(bhc)
        key=kw*8192+kh
        for k in np.unique(key):
            g=ids[key==k]; gw=int(k//8192); gh=int(k%8192)
            npx=gw*gh; chunk=max(1,4_000_000//npx)
            ox,oy=np.meshgrid(np.arange(gw),np.arange(gh)); ox=ox.ravel(); oy=oy.ravel()
            for s0 in range(0,len(g),chunk):
                t=g[s0:s0+chunk]
                px=(x0[t][:,None]+ox[None,:]); py=(y0[t][:,None]+oy[None,:])
                cx=px+0.5; cy=py+0.5
                xa,xb,xc=X[t,0][:,None],X[t,1][:,None],X[t,2][:,None]
                ya,yb,yc=Y[t,0][:,None],Y[t,1][:,None],Y[t,2][:,None]
                area=(xb-xa)*(yc-ya)-(xc-xa)*(yb-ya)
                w0=(xb-cx)*(yc-cy)-(xc-cx)*(yb-cy)
                w1=(xc-cx)*(ya-cy)-(xa-cx)*(yc-cy)
                w2=(xa-cx)*(yb-cy)-(xb-cx)*(ya-cy)
                with np.errstate(divide='ignore',invalid='ignore'):
                    w0=w0/area; w1=w1/area; w2=w2/area
                    w0=np.nan_to_num(w0,nan=-1); w1=np.nan_to_num(w1,nan=-1); w2=np.nan_to_num(w2,nan=-1)
                m=(w0>=-1e-6)&(w1>=-1e-6)&(w2>=-1e-6)&(px>=0)&(py>=0)&(px<W)&(py<H)&(area!=0)
                if not m.any(): continue
                z=(w0*Z[t,0][:,None]+w1*Z[t,1][:,None]+w2*Z[t,2][:,None])
                if Ds:
                    out=np.ones(m.shape,bool)
                    for D in Ds:
                        out&=(w0*D[t,0][:,None]+w1*D[t,1][:,None]+w2*D[t,2][:,None])>0
                    m&=~out
                z=z[m].astype(np.float32); idx=(py*W+px)[m]; tt=np.broadcast_to((t+base)[:,None],m.shape)[m]
                better=z<zbuf[idx]; z=z[better]; idx=idx[better]; tt=tt[better]
                if len(z)==0: continue
                o=np.lexsort((z,idx)); idx=idx[o]; z=z[o]; tt=tt[o]
                first=np.ones(len(idx),bool); first[1:]=idx[1:]!=idx[:-1]
                zbuf[idx[first]]=z[first]; tid[idx[first]]=tt[first]
        base+=len(f)
    shade=np.concatenate(shade_all); obj=np.concatenate(obj_all); back=np.concatenate(back_all)
    img=np.empty((W*H,3),np.float32); img[:]=bg
    hit=tid>=0; t=tid[hit]
    cols=np.array([c for _,c in objs],np.float32)
    oc=cols[obj[t]]
    s=shade[t]
    lit=oc*(0.45+0.6*s[:,None])
    pix=np.nonzero(hit)[0]; hx=pix%W; hy=pix//W
    hatch=((hx+hy)//(3*ss))%3==0
    cut=np.where(hatch[:,None], oc*0.35, oc*0.8+255*0.1)
    isback=back[t] & caps
    col=np.where(isback[:,None], cut, lit)
    img[hit]=np.clip(col,0,255)
    img=img.reshape(H,W,3)
    if outline:
        oid=np.full(W*H,-1); oid[hit]=obj[t]*2+isback.astype(int); oid=oid.reshape(H,W)
        zz=zbuf.reshape(H,W)
        e=np.zeros((H,W),bool)
        e[:,1:]|=oid[:,1:]!=oid[:,:-1]; e[1:,:]|=oid[1:,:]!=oid[:-1,:]
        with np.errstate(invalid='ignore'):
            dzx=np.abs(np.diff(zz,axis=1)); dzy=np.abs(np.diff(zz,axis=0))
            thr=(1.5 if ortho is None else 1.0)
            e[:,1:]|=np.nan_to_num(dzx,posinf=99)>thr; e[1:,:]|=np.nan_to_num(dzy,posinf=99)>thr
        img[e]=img[e]*0.25+np.array([30,30,30])*0.75
    im=Image.fromarray(img.astype(np.uint8))
    if ss>1: im=im.resize(size,Image.LANCZOS)
    return im

SHELL_BLACK=(55,55,60); WHITE=(240,240,236)
PRINTED={
 'V100_COLORED_SHELL_EXTERNAL_THROUGH_BOLTS':SHELL_BLACK,
 'RECESSED_PANEL':WHITE,
 'V100_BOTTOM_CUP.001':(110,150,200),
 'V100_ACOUSTIC_TUBE.001':(140,190,150),
 'V100_OPEN_TOP_ELECTRONICS_CARRIER':(235,160,80),
 'V100_SEPARATE_BOLTED_WAVE_LID':(150,130,200),
 'POLYFILL_GUARD.001':(230,200,90),
 'REFERENCE_LOADED_SENSOR_RETAINER':(220,120,150),
}
STAND={'URN_MAGNET_STAND':(170,170,175)}
METAL=(185,188,195)
def build_refs():
    names=list(MESHES)
    REFS={}
    for n in names:
        if n.startswith('REFERENCE_') and n!='REFERENCE_LOADED_SENSOR_RETAINER' and 'MAX_FIT' not in n:
            if 'FEATHER' in n and 'PCB' in n: REFS[n]=(40,110,70)
            elif 'FEATHER' in n: REFS[n]=(60,60,70)
            elif 'BATTERY' in n: REFS[n]=(200,205,215)
            elif 'JST' in n: REFS[n]=(235,235,225)
            else: REFS[n]=METAL
    for n in ['GRS_NOMINAL_FRAME','GRS_NOMINAL_GASKET','GRS_FRONT_DIAPHRAGM_REFERENCE','GRS_BASKET_CLEARANCE_ENVELOPE','GRS_MAGNET_CLEARANCE_ENVELOPE']:
        REFS[n]=(70,70,78)
    REFS['GRS_NOMINAL_GASKET']=(30,30,30); REFS['GRS_FRONT_DIAPHRAGM_REFERENCE']=(95,95,105)
    for n in ['PREVIEW_TMAG_PCB','PREVIEW_TMAG_IC','PREVIEW_TMAG_STEMMA','PREVIEW_TMAG_STEMMA.001']:
        REFS[n]=(40,110,70) if 'PCB' in n else (60,60,70)

    return REFS
def objs(d): return list(d.items())

def font(sz):
    for p in ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/dejavu/DejaVuSans.ttf']:
        try: return ImageFont.truetype(p,sz)
        except: pass
    return ImageFont.load_default()
def add_legend(im, items, title=None, cols=1):
    f=font(18); ft=font(24)
    rowh=28; pad=20; n=len(items); rows=(n+cols-1)//cols
    th=(36 if title else 0)
    h=pad*2+th+rows*rowh
    out=Image.new('RGB',(im.width, im.height+h),(255,255,255)); out.paste(im,(0,0))
    d=ImageDraw.Draw(out); y0=im.height+pad
    if title: d.text((pad,y0),title,fill=(20,20,20),font=ft); y0+=th
    cw=(im.width-2*pad)//cols
    for i,(label,col) in enumerate(items):
        c=i//rows; r=i%rows; x=pad+c*cw; y=y0+r*rowh
        d.rectangle([x,y+4,x+18,y+22],fill=tuple(col),outline=(30,30,30))
        d.text((x+28,y+2),label,fill=(30,30,30),font=f)
    return out
def caption(im,text):
    f=font(24); out=Image.new('RGB',(im.width,im.height+50),(255,255,255)); out.paste(im,(0,50))
    ImageDraw.Draw(out).text((20,12),text,fill=(20,20,20),font=f); return out

def main(OUT, which):
    REFS=build_refs()
    SPEAKER=(70,70,78); PCB=(40,110,70)
    LEG_PRINTED=[('Shell, pattern (black PLA)',SHELL_BLACK),('Shell, white diffuser (white PLA)',(225,225,220)),
     ('Bottom cup',PRINTED['V100_BOTTOM_CUP.001']),('Acoustic tube',PRINTED['V100_ACOUSTIC_TUBE.001']),
     ('Electronics carrier',PRINTED['V100_OPEN_TOP_ELECTRONICS_CARRIER']),('Wave lid',PRINTED['V100_SEPARATE_BOLTED_WAVE_LID']),
     ('Polyfill guard',PRINTED['POLYFILL_GUARD.001']),('Sensor capture spacer',PRINTED['REFERENCE_LOADED_SENSOR_RETAINER'])]
    LEG_BOUGHT=[('Speaker (GRS 3FR-4)',SPEAKER),('M3 screws, nuts, washers',METAL),('Feather RP2040 Prop-Maker / TMAG5273',PCB),('Battery 4400 mAh',REFS['REFERENCE_CARRIER_DEVICE_BATTERY_354_NOMINAL'])]
    LEG=LEG_PRINTED+LEG_BOUGHT
    ALL=objs(PRINTED)+objs(REFS)
    def save(im,name): im.save(f'{OUT}/{name}.png',optimize=True); print('saved',name,im.size,flush=True)
    note='Cut faces are hatched.'
    if 'side' in which:
        im=render(ALL,eye=(1000,0,122),target=(0,0,122),size=(900,1180),ortho=262,clip=((0,0,0),(1,0,0)),ss=2,light=(0.7,-0.3,0.6))
        save(add_legend(caption(im,'Section, side (rotated 90°). '+note),LEG,cols=2),'section-side')
    if 'cutaway' in which:
        im=render(ALL,eye=(-560,-700,430),target=(0,0,125),size=(1000,1200),fov=17,
                  clip=[((0,0,0),(-1,0,0)),((0,0,0),(0,-1,0))],ss=2)
        save(add_legend(caption(im,'Quarter cutaway, assembled. '+note),LEG,cols=2),'cutaway')
    if 'bottom' in which:
        a=render(ALL,eye=(0,0,-1000),target=(0,0,0),up=(0,1,0),size=(700,700),ortho=175,ss=2,light=(0.3,-0.4,-0.8))
        a=caption(a,'Straight up from below')
        b=render(ALL,eye=(300,-520,-430),target=(0,0,105),size=(700,700),fov=20,ss=2,light=(0.3,-0.5,-0.8))
        b=caption(b,'From below, angled')
        im=Image.new('RGB',(1400,a.height),(255,255,255)); im.paste(a,(0,0)); im.paste(b,(700,0))
        save(add_legend(im,[l for l in LEG if l[0] in ('Bottom cup','Acoustic tube','Shell, pattern (black PLA)','Shell, white diffuser (white PLA)','M3 screws, nuts, washers','Speaker (GRS 3FR-4)')],cols=3),'bottom')
    if 'exploded' in which:
        off={}
        for n in ALL:
            n=n[0]
            if n in ('V100_COLORED_SHELL_EXTERNAL_THROUGH_BOLTS','RECESSED_PANEL'): continue
            if n in ('V100_BOTTOM_CUP.001','REFERENCE_LOADED_SENSOR_RETAINER') or n.startswith('PREVIEW_TMAG') or 'COMMON_M3x20' in n or 'SPEAKER_BUTTON' in n or 'SPEAKER_WASHER' in n: off[n]=(0,0,-230)
            elif n.startswith('GRS_'): off[n]=(0,0,-140)
            elif n=='POLYFILL_GUARD.001': off[n]=(0,0,-85)
            elif n=='V100_ACOUSTIC_TUBE.001' or 'CAPTIVE_M3_NUT' in n: off[n]=(0,0,0)
            elif n=='V100_SEPARATE_BOLTED_WAVE_LID' or 'BOLTED_WAVE' in n: off[n]=(0,0,200)
            else: off[n]=(0,0,100)
        parts=[o for o in ALL if o[0] in off]
        d=np.array([-0.5,-0.8,0.35]); d/=np.linalg.norm(d)
        im=render(parts,eye=np.array([0,0,105])+d*1500,target=(0,0,105),size=(800,1500),fov=29,offsets=off,ss=2)
        save(add_legend(caption(im,'Exploded internals (shell removed)'),[l for l in LEG if not l[0].startswith('Shell')],cols=2),'exploded')
    if 'parts' in which:
        names=[('bottom-cup','V100_BOTTOM_CUP.001'),('acoustic-tube','V100_ACOUSTIC_TUBE.001'),('electronics-carrier','V100_OPEN_TOP_ELECTRONICS_CARRIER'),
               ('wave-lid','V100_SEPARATE_BOLTED_WAVE_LID'),('polyfill-guard','POLYFILL_GUARD.001'),('sensor-capture-spacer','REFERENCE_LOADED_SENSOR_RETAINER'),
               ('urn-stand','URN_MAGNET_STAND'),('shell (both colors)',None)]
        tiles=[]
        for label,n in names:
            if n is None:
                o=[('V100_COLORED_SHELL_EXTERNAL_THROUGH_BOLTS',SHELL_BLACK),('RECESSED_PANEL',WHITE)]
            else: o=[(n,PRINTED.get(n,(170,170,175)))]
            vs=np.concatenate([load(x)[0] for x,_ in o]); lo,hi=vs.min(0),vs.max(0); c=(lo+hi)/2; r=np.linalg.norm(hi-lo)/2
            d=r/np.tan(np.radians(12))*1.05; dirv=np.array([-0.55,-0.75,0.55]); dirv/=np.linalg.norm(dirv)
            t=render(o,eye=c+dirv*d,target=c,size=(500,500),fov=24,ss=2,caps=False)
            tiles.append(caption(t,label))
        W=500; Hh=tiles[0].height; grid=Image.new('RGB',(W*4,Hh*2),(255,255,255))
        for i,t in enumerate(tiles): grid.paste(t,((i%4)*W,(i//4)*Hh))
        save(grid,'printed-parts')


if __name__=='__main__':
    which=sys.argv[1:] or ['side','cutaway','bottom','exploded','parts']
    print('Reading',os.path.relpath(BLEND,ROOT)); extract(BLEND)
    os.makedirs(OUTDIR,exist_ok=True); main(OUTDIR,which)
