import numpy as np
from PIL import Image
from scipy import ndimage as ndi
def fairway_mask(path):
    a=np.asarray(Image.open(path).convert('RGB')).astype(float); R,G,B=a[...,0],a[...,1],a[...,2]
    grass=(G>190)&(G<232)&(R>140)&(R<190)&(B<125)&(B>55)
    hp=G-ndi.gaussian_filter(G,1.2); e=ndi.uniform_filter(np.abs(hp),11)
    tree=ndi.binary_dilation(~((G>170)&(R>120)) | (B>150), iterations=3)      # 樹、陰影、水、外框，往外擴 3px 去掉光暈
    cand=ndi.uniform_filter(grass.astype(float),7)>0.9
    m=cand&(e>0.85)&(e<2.2)&~tree
    m=ndi.binary_opening(m,structure=np.ones((5,5)),iterations=1); m=ndi.binary_closing(m,structure=np.ones((7,7)),iterations=2)
    m=ndi.binary_fill_holes(m)
    lab,n=ndi.label(m); sizes=ndi.sum(m,lab,range(1,n+1))
    keep=np.isin(lab,[i+1 for i,s in enumerate(sizes) if s>=900])
    return a.astype(np.uint8),keep
if __name__=='__main__':
    import sys
    tiles=[]
    for h in map(int,sys.argv[1:]):
        a,m=fairway_mask(f'img/hole{h:02d}.png'); o=a.copy(); edge=m^ndi.binary_erosion(m,iterations=2); o[edge]=(255,0,255)
        tiles.append(Image.fromarray(o))
    W=Image.new('RGB',(730*len(tiles),730)); [W.paste(t,(730*i,0)) for i,t in enumerate(tiles)]; W.resize((W.size[0]*2//3,730*2//3)).save('segcheck.png')
