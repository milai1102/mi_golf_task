import sys, glob, zlib, os, json, numpy as np
from PIL import Image
def decode(path, CW, CH, CELL):
    a=np.asarray(Image.open(path).convert('L')).astype(float); H,W=a.shape
    for S in [W/738*f for f in (1,1.001,0.999,1.002,0.998,1.003,0.997)]:
        for off in (0,0.5,-0.5):
            c=CELL*S; xs=((np.arange(CW)+0.5)*c+off); ys=((np.arange(CH)+0.5)*c+off)
            xi=np.clip(np.round(xs-0.5).astype(int),0,W-2); yi=np.clip(np.round(ys-0.5).astype(int),0,H-2)
            v=(a[np.ix_(yi,xi)]+a[np.ix_(yi,xi+1)]+a[np.ix_(yi+1,xi)]+a[np.ix_(yi+1,xi+1)])/4 if CELL>=4 else a[np.ix_(np.clip(np.round(ys).astype(int),0,H-1),np.clip(np.round(xs).astype(int),0,W-1))]
            cal=v[0]; lv=[cal[i::4].mean() for i in range(4)]; th=[(lv[0]+lv[1])/2,(lv[1]+lv[2])/2,(lv[2]+lv[3])/2]
            sym=np.digitize(v[1:].ravel(),th).astype(np.uint8); n=len(sym)//4*4; s=sym[:n].reshape(-1,4)
            by=((s[:,0]<<6)|(s[:,1]<<4)|(s[:,2]<<2)|s[:,3]).astype(np.uint8).tobytes()
            k,total,ln=by[0],by[1],(by[2]<<8)|by[3]; crc=int.from_bytes(by[4:8],'big'); data=by[8:8+ln]
            if len(data)==ln and zlib.crc32(data)==crc: return k,total,data
    return None
def unpack(raw):
    p=0
    def u(n):
        nonlocal p; v=int.from_bytes(raw[p:p+n],'big'); p+=n; return v
    def vz():
        nonlocal p; sh=0; v=0
        while True:
            b=raw[p]; p+=1; v|=(b&127)<<sh; sh+=7
            if b<128: break
        return (v>>1)^-(v&1)
    out={}; n=u(2)
    for _ in range(n):
        gid=u(4); hole=u(1); g={}
        for cls in ('outline','fairway','green','water','sand','tree'):
            polys=[]
            for _ in range(u(1)):
                m=u(2); x=u(2); y=u(2); pts=[[x,y]]
                for _ in range(m-1): x+=vz(); y+=vz(); pts.append([x,y])
                polys.append(pts)
            g[cls]=polys
        out.setdefault(f'{gid:06d}',{})[str(hole)]=g
    return out
