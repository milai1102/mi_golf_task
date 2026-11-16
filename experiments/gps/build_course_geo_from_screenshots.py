"""把 Garmin 的每洞地圖（圖檔不進版控）轉成向量的球場輪廓，存成 data/course_geo/<球場代碼>.json。

用法：python3 scripts/build_course_geo.py <圖檔資料夾> <球場代碼>
圖檔資料夾裡是 hole01.png … holeNN.png（730×730，發球台在下、果嶺在上）。
輸出的座標和 Garmin 擊球資料的 x,y 是同一套像素座標，報表直接拿來畫示意圖，不需要原圖。
需要 numpy / scipy / scikit-image / Pillow（只有這支程式需要，報表本身不需要）。
"""
import glob, json, os, sys
import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from skimage import measure

def polys(mask, min_area, tol=1.6):
    out = []
    lab, n = ndi.label(mask)
    for i in range(1, n + 1):
        m = lab == i
        if m.sum() < min_area: continue
        cs = measure.find_contours(np.pad(m, 1).astype(float), 0.5)
        if not cs: continue
        c = max(cs, key=len) - 1
        p = measure.approximate_polygon(c, tol)
        if len(p) >= 4: out.append([[int(round(x)), int(round(y))] for y, x in p[:-1]])
    return out

def classify(path):
    a = np.asarray(Image.open(path).convert("RGB")).astype(float); R, G, B = a[..., 0], a[..., 1], a[..., 2]
    sky = (B > 200) & (G > 190) & (R < 160)                                  # 地圖外的淺藍底
    water = (B > 170) & (R < 120) & (G < 170) & ~sky
    hole = ndi.binary_fill_holes(ndi.binary_opening(~sky, iterations=2))
    lab, n = ndi.label(hole); hole = lab == (1 + int(np.argmax(ndi.sum(hole, lab, range(1, n + 1)))))
    sand = (R > 185) & (G > 160) & (B > 140) & (R > B + 12) & hole & ~water
    sand = ndi.binary_opening(sand, iterations=1)
    grass = (G > 190) & (G < 232) & (R > 140) & (R < 190) & (B < 125) & (B > 55)
    hp = G - ndi.gaussian_filter(G, 1.2); e = ndi.uniform_filter(np.abs(hp), 11)
    dark = ~((G > 170) & (R > 120)) | (B > 150)
    halo = ndi.binary_dilation(dark, iterations=3)
    fw = (ndi.uniform_filter(grass.astype(float), 7) > 0.9) & (e > 0.85) & (e < 2.2) & ~halo
    fw = ndi.binary_fill_holes(ndi.binary_closing(ndi.binary_opening(fw, structure=np.ones((5, 5))), structure=np.ones((7, 7)), iterations=2))
    green = (R > 105) & (R < 150) & (G > 168) & (G < 206) & (B < 100) & (B > 40) & hole
    green = (ndi.uniform_filter(green.astype(float), 7) > 0.75) & (e > 1.2)
    green = ndi.binary_fill_holes(ndi.binary_closing(ndi.binary_opening(green, structure=np.ones((5, 5))), structure=np.ones((5, 5))))
    tree = hole & ~water & ~sand & ((G < 165) | (R < 115) | ((R > 190) & (G > 190) & (B < 120)))   # 深色樹冠、陰影，和黃色的樹
    tree = ndi.binary_closing(ndi.binary_opening(tree, iterations=2), iterations=2) & ~green
    fw &= ~green
    return {"outline": polys(hole, 5000, 2.0), "fairway": polys(fw, 900), "green": polys(green, 250, 1.2),
            "water": polys(water, 120, 1.2), "sand": polys(sand, 40, 1.0), "tree": polys(tree, 140, 2.2)}

if __name__ == "__main__":
    src, cid = sys.argv[1], sys.argv[2]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = {"course": cid, "size": 730, "holes": {}}
    for f in sorted(glob.glob(os.path.join(src, "hole*.png"))):
        h = int(os.path.basename(f)[4:6]); out["holes"][str(h)] = classify(f)
        print(h, {k: len(v) for k, v in out["holes"][str(h)].items()})
    os.makedirs(os.path.join(root, "data/course_geo"), exist_ok=True)
    p = os.path.join(root, f"data/course_geo/{cid}.json")
    json.dump(out, open(p, "w"), separators=(",", ":")); print(p, os.path.getsize(p), "bytes")
