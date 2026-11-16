import numpy as np, json
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi
from seg import fairway_mask
YD=1.09361
holes=[]; cur=None
for line in open('wufong_raw.txt',encoding='utf-8'):
    p=line.strip().split('|')
    if not p[0]: continue
    if p[0].startswith('H'):
        pin=list(map(int,p[2].split(','))); cur={'hole':int(p[0][1:]),'par':int(p[1]),'pin':pin,'shots':[]}; holes.append(cur)
    else:
        s=p[4].split(','); e=p[5].split(',')
        cur['shots'].append({'n':int(p[0]),'club':p[1],'type':p[2],'m':int(p[3]),'s':(int(s[2]),int(s[3])),'e':(int(e[2]),int(e[3])),'slie':s[4],'elie':e[4],
                             'sll':(int(s[0]),int(s[1])),'ell':(int(e[0]),int(e[1]))})
def font(sz):
    for f in ('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc','/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'):
        try: return ImageFont.truetype(f,sz)
        except Exception: pass
    return ImageFont.load_default()
res=[]; tiles=[]
for H in holes:
    a,m=fairway_mask(f"img/hole{H['hole']:02d}.png"); im=Image.fromarray(a).convert('RGB'); d=ImageDraw.Draw(im,'RGBA')
    edge=m^ndi.binary_erosion(m,iterations=1); ov=np.zeros((730,730,4),np.uint8); ov[edge]=(255,255,255,150); im.paste(Image.fromarray(ov),(0,0),Image.fromarray(ov))
    d=ImageDraw.Draw(im,'RGBA')
    for S in H['shots']:
        (x0,y0),(x1,y1)=S['s'],S['e']; L=np.hypot(x1-x0,y1-y0)
        if S['club'] in ('A杆','54度','60度') or L<5: 
            d.line([x0,y0,x1,y1],fill=(255,255,255,120),width=2); continue
        mpp=S['m']/L; ux,uy=(x1-x0)/L,(y1-y0)/L; nx,ny=uy,-ux           # n 指向行進方向的左邊
        # 沿垂直方向掃描球道
        ts=np.arange(-120,121); xs=np.clip(np.round(x1+nx*ts).astype(int),0,729); ys=np.clip(np.round(y1+ny*ts).astype(int),0,729); row=m[ys,xs]
        info=None
        if row.any():
            lab,n=ndi.label(row); best=None
            for k in range(1,n+1):
                idx=np.where(lab==k)[0]; lo,hi=ts[idx[0]],ts[idx[-1]]
                dist=0 if lo<=0<=hi else min(abs(lo),abs(hi))
                if hi-lo>=6 and (best is None or dist<best[0]): best=(dist,lo,hi)
            if best:
                _,lo,hi=best; width=(hi-lo)*mpp*YD; center=(lo+hi)/2; off=center*mpp*YD     # 正值＝球在球道中線的右邊
                inside=lo<=0<=hi; out=0 if inside else (min(abs(lo),abs(hi))*mpp*YD)
                info={'width':round(width),'off':round(off),'inside':bool(inside),'out':round(out),'side':'右' if off>0 else '左'}
                d.line([x1+nx*lo,y1+ny*lo,x1+nx*hi,y1+ny*hi],fill=(255,255,0,255),width=3)
        col={'一號木桿':(255,60,60,255),'鐵木桿':(255,160,0,255)}.get(S['club'],(60,140,255,255))
        d.line([x0,y0,x1,y1],fill=col,width=3); d.ellipse([x1-6,y1-6,x1+6,y1+6],fill=col,outline=(255,255,255,255),width=2)
        res.append({'hole':H['hole'],'par':H['par'],'n':S['n'],'club':S['club'],'yd':round(S['m']*YD),'elie':S['elie'],'fw':info})
    px,py=H['pin'][2],H['pin'][3]; d.line([px,py,px,py-18],fill=(255,255,255,255),width=2); d.polygon([px,py-18,px+10,py-14,px,py-10],fill=(255,0,0,255))
    d.rectangle([8,8,150,44],fill=(0,0,0,150)); d.text((14,10),f"第 {H['hole']} 洞 Par {H['par']}",font=font(22),fill=(255,255,255,255))
    tiles.append(im)
json.dump(res,open('wufong_res.json','w'),ensure_ascii=False,indent=1)
for r in res:
    if r['club'] in('一號木桿','鐵木桿'): print(r['hole'],r['n'],r['club'],r['yd'],r['elie'],r['fw'])
cols=6; rows=3; W=Image.new('RGB',(730*cols,730*rows))
for i,t in enumerate(tiles): W.paste(t,(730*(i%cols),730*(i//cols)))
W.save('wufong_all.png'); W.resize((730*cols//3,730*rows//3)).save('wufong_all_s.png')
for i,t in enumerate(tiles): t.save(f'tile{i+1:02d}.png')
