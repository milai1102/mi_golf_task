# 桿弟頁瞄準線的總覽圖：把每一洞的輪廓、瞄準線（黃線）、歷次擊球位置（白點）排成一張張大圖，用眼睛檢查。
# 用法：python3 scripts/caddie_sheet.py [球場簡稱,球場簡稱…]   （先跑 build_report.py；圖存在目前的資料夾 sheet_*.jpg；需要 Pillow）
import json,sys
from PIL import Image,ImageDraw,ImageFont
s=open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'..','reports','caddie.html'),encoding='utf-8').read()
i=s.index('const REPORT = ')+15; R,_=json.JSONDecoder().raw_decode(s[i:])
sec=[x for x in R['sections'] if x['kind']=='caddie'][0]; GEO=R['geo']
import glob
fp=[f for f in glob.glob('/usr/share/fonts/**/*',recursive=True) if 'CJK' in f or 'Noto' in f and 'Sans' in f]
font=ImageFont.truetype(sorted(fp,key=lambda f:'CJK' not in f)[0],16)
items=[]
for C in sec['data']:
  for L in C['layouts']:
    for hi,H in enumerate(L['holes']):
      if (H.get('par') or 4)>3: items.append((C,L,hi,H))
only=sys.argv[1] if len(sys.argv)>1 else None
if only: items=[it for it in items if it[0]['short'] in only.split(',')]
TW,TH,SC=230,400,0.62
COL=dict(outline=(40,90,45),tree=(15,50,25),fairway=(120,200,110),green=(170,240,150),water=(60,120,230),sand=(230,210,160))
n=0
for s0 in range(0,len(items),24):
  im=Image.new('RGB',(TW*8,TH*3),(10,10,10)); d=ImageDraw.Draw(im)
  for t,(C,L,hi,H) in enumerate(items[s0:s0+24]):
    ox,oy=(t%8)*TW,(t//8)*TH; g=GEO.get(L['cid'],{}).get(str(H['h']),{})
    out=g.get('outline') or []; 
    if out and not isinstance(out[0][0],list): out=[out]
    allp=[q for p in out for q in p]+[H['tee'],H['pin'][:2]]+H['path']
    x0=min(q[0] for q in allp); x1=max(q[0] for q in allp); y0=min(q[1] for q in allp); y1=max(q[1] for q in allp)
    sc=min((TW-10)/(x1-x0+1),(TH-30)/(y1-y0+1))
    T=lambda q:(ox+5+(q[0]-x0)*sc+((TW-10)-(x1-x0)*sc)/2, oy+25+(q[1]-y0)*sc)
    for kk in ['outline','tree','fairway','green','water','sand']:
      ps=out if kk=='outline' else g.get(kk,[])
      for p in ps:
        if len(p)>=3: d.polygon([T(q) for q in p],fill=COL[kk])
    for p in H.get('plays',[]):
      for sh in p['shots']:
        a=T((sh[2],sh[3])); d.ellipse([a[0]-2,a[1]-2,a[0]+2,a[1]+2],fill=(255,255,255))
    d.line([T(q) for q in H['path']],fill=(255,210,0),width=2)
    a=T(H['pin'][:2]); d.ellipse([a[0]-4,a[1]-4,a[0]+4,a[1]+4],fill=(255,40,40))
    d.text((ox+4,oy+2),f"{C['short']}{L.get('label') or ''} {hi+1} P{H.get('par')}",fill=(255,255,255),font=font)
  n+=1; im.save(f"sheet_{only or 'all'}_{n}.jpg",quality=80); print(n)
