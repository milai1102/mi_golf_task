"""沒打過、也不用 Garmin 球洞圖的球場：用空拍照片 + OpenStreetMap 自己描出每一洞的輪廓。

輸入 data/course_build/<球場代碼>.json：
  {cid, garmin_name, label, area, osm, holes: [{h, par, yd, pin: [緯度, 經度], tee: [緯度, 經度]}]}
  area＝data/areas/<area>/photo_z18.jpg（GitHub Actions 下載的空拍拼圖），osm＝data/areas/<osm>/osm.json。
輸出：
  data/course_geo/<cid>.json      每洞的輪廓（outline、fairway、green、sand、water、tree），座標是這一洞自己的圖框
  data/course_preview/<cid>.json  每洞的旗桿、發球台（圖框像素）、每像素幾碼 k、經緯度→圖框的轉換 ll
圖框：730×730，旗桿在 (365, 110)，發球台在正下方，打球方向朝上。和 Garmin 的球洞圖同樣的擺法，報表不必分兩種。
用法：python3 scripts/build_aerial_course.py 032570 [--preview 檢查圖輸出資料夾]
"""
import json, math, os, sys
import numpy as np
import cv2
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S, PX, PY = 730, 365, 110            # 圖框大小、旗桿位置
YD = 0.9144
MLAT = 110540.0
MLON = lambda lat: 111320.0 * math.cos(math.radians(lat))


def tile_xy(lat, lon, z):
    n = 2 ** z
    return (lon + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n


def frame(H):
    """這一洞的圖框：回傳 (k 碼/像素, 由經緯度到圖框的函式, ll 係數)。"""
    plat, plon = H["pin"]; tlat, tlon = H["tee"]
    te, tn = (tlon - plon) * MLON(plat), (tlat - plat) * MLAT       # 旗桿 → 發球台（公尺）
    L = math.hypot(te, tn); ue, un = te / L, tn / L                 # 朝發球台的單位向量（圖上往下）
    re, rn = -un, ue                                                 # 面向旗桿時的右手邊（圖上往右）
    span = 540                                                       # 旗桿到發球台在圖上的長度（像素）
    km = max(L / span, 0.3 * YD)                                     # 每像素幾公尺；三桿洞不要放太大
    a, b = MLON(plat) * re / km, MLAT * rn / km
    d, e = MLON(plat) * ue / km, MLAT * un / km
    ll = [plat, plon, round(a, 3), round(b, 3), PX, round(d, 3), round(e, 3), PY]
    f = lambda lat, lon: (a * (lon - plon) + b * (lat - plat) + PX, d * (lon - plon) + e * (lat - plat) + PY)
    return km / YD, f, ll, (a, b, d, e)


def inverse(ll):
    plat, plon, a, b, c, d, e, f = ll
    det = a * e - b * d
    def g(x, y):
        x, y = x - c, y - f
        return plat + (-d * x + a * y) / det, plon + (e * x - b * y) / det
    return g


def sample(photo, meta, ll):
    """把空拍拼圖重新取樣到這一洞的圖框（730×730）。"""
    g = inverse(ll)
    ys, xs = np.mgrid[0:S, 0:S].astype(np.float64)
    plat, plon, a, b, c, d, e, f = ll; det = a * e - b * d
    lat = plat + (-d * (xs - c) + a * (ys - f)) / det
    lon = plon + (e * (xs - c) - b * (ys - f)) / det
    n = 2 ** meta["z"]
    tx = (lon + 180) / 360 * n
    ty = (1 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2 * n
    mx = ((tx - meta["ax"]) * 256).astype(np.float32); my = ((ty - meta["ay"]) * 256).astype(np.float32)
    return cv2.remap(photo, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(30, 60, 30))


def polys(mask, min_area, eps):
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cs:
        if cv2.contourArea(c) < min_area: continue
        c = cv2.approxPolyDP(c, eps, True)
        if len(c) >= 3: out.append([[int(p[0][0]), int(p[0][1])] for p in c])
    return out


def fill(polylist, f):
    m = np.zeros((S, S), np.uint8)
    for pts in polylist:
        q = np.array([f(la, lo) for la, lo in pts], np.float32)
        cv2.fillPoly(m, [np.round(q).astype(np.int32)], 1)
    return m.astype(bool)


def classify(img, H, k, f, osm):
    rgb = img.astype(np.float32); r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV).astype(np.float32); V, Sat = hsv[..., 2], hsv[..., 1]
    m1 = cv2.blur(V, (7, 7)); m2 = cv2.blur(V * V, (7, 7)); sd = np.sqrt(np.maximum(m2 - m1 * m1, 0))
    sand = (r > 175) & (g > 160) & (b > 120) & (r - b > 20) & (Sat < 95)        # 沙坑：比枯黃的草更亮更白
    sand = cv2.morphologyEx(sand.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab0, st, _ = cv2.connectedComponentsWithStats(sand)
    keep0 = np.zeros(n, bool)
    for i in range(1, n):                                                          # 細長的是車道，不是沙坑
        w, h, a = st[i, 2], st[i, 3], st[i, 4]
        keep0[i] = a * k * k >= 12 and a >= 0.35 * w * h and max(w, h) * k <= 45
    sand = keep0[lab0] | fill(osm["bunker"], f)
    water = fill(osm["water"], f)
    dark = cv2.blur(V, (9, 9)) < 92
    rough_tex = cv2.blur(sd, (9, 9)) > 15
    tree = (dark | (rough_tex & (cv2.blur(V, (9, 9)) < 125))) & ~sand & ~water
    tree = cv2.morphologyEx(tree.astype(np.uint8), cv2.MORPH_OPEN, np.ones((7, 7), np.uint8)).astype(bool)
    tree = cv2.morphologyEx(tree.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8)).astype(bool)
    open_ = ~tree & ~water
    open_ = cv2.morphologyEx(open_.astype(np.uint8), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)).astype(bool)
    # 只留下和這一洞相連的那一塊（旗桿到發球台的連線上）
    _, lab = cv2.connectedComponents((open_ | sand | water).astype(np.uint8))
    tx, ty = PX, PY + int(round(H["_len_px"]))
    keep = set()
    for t in np.linspace(0, 1, 25):
        x, y = int(PX), int(PY + t * (ty - PY))
        if 0 <= y < S and lab[y, x]: keep.add(lab[y, x])
    course = np.isin(lab, list(keep)) if keep else (open_ | sand | water)
    course = cv2.morphologyEx(course.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8)).astype(bool)
    # 果嶺：OSM 有的就用 OSM；否則從旗桿往外長「平滑、明亮」的區塊，最多 22 碼
    green = np.zeros((S, S), bool)
    for pts in osm["green"]:
        gm = fill([pts], f)
        ys, xs = np.nonzero(gm)
        if len(xs) and gm[PY, PX]: green |= gm                                    # OSM 的果嶺常有偏移：要包住旗桿才用
    if not green.any():
        smooth = (sd < 7) & ~sand & ~tree & ~water
        smooth = cv2.morphologyEx(smooth.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        yy, xx = np.mgrid[0:S, 0:S]; near = np.hypot(xx - PX, yy - PY) * k < 22
        _, lab2 = cv2.connectedComponents((smooth.astype(bool) & near).astype(np.uint8))
        if lab2[PY, PX]: green = lab2 == lab2[PY, PX]
        if green.sum() * k * k < 250: green = np.hypot(xx - PX, yy - PY) * k < 13     # 抓不到就用半徑 13 碼的圓
    fair = course & ~sand & ~water & ~green
    return {"outline": course, "fairway": fair, "green": green, "sand": sand & course, "water": water, "tree": tree & ~course}


def osm_layers(path):
    d = json.load(open(path)) if path and os.path.exists(path) else {"elements": []}
    out = {"green": [], "bunker": [], "water": []}
    for e in d["elements"]:
        t = e.get("tags", {}); g = e.get("geometry")
        if not g or len(g) < 3: continue
        pts = [(p["lat"], p["lon"]) for p in g]
        if t.get("golf") == "green": out["green"].append(pts)
        elif t.get("golf") == "bunker" or t.get("natural") == "sand": out["bunker"].append(pts)
        elif t.get("natural") == "water" or t.get("landuse") in ("reservoir", "basin") or t.get("golf") in ("water_hazard", "lateral_water_hazard"): out["water"].append(pts)
    return out


def main():
    cid = sys.argv[1]; prev = sys.argv[sys.argv.index("--preview") + 1] if "--preview" in sys.argv else None
    B = json.load(open(os.path.join(ROOT, "data/course_build", f"{cid}.json"), encoding="utf-8"))
    A = os.path.join(ROOT, "data/areas", B["area"])
    meta = json.load(open(os.path.join(A, "photo_z18.json")))
    photo = np.asarray(Image.open(os.path.join(A, "photo_z18.jpg")).convert("RGB"))
    osm = osm_layers(os.path.join(ROOT, "data/areas", B.get("osm") or B["area"], "osm.json"))
    geo, pv = {}, []
    for H in B["holes"]:
        k, f, ll, _ = frame(H)
        tee = f(*H["tee"]); H["_len_px"] = tee[1] - PY
        img = sample(photo, meta, ll)
        L = classify(img, H, k, f, osm)
        geo[str(H["h"])] = {"outline": polys(L["outline"], 2000, 3), "fairway": polys(L["fairway"], 600, 2.5), "green": polys(L["green"], 60, 1.5),
                            "water": polys(L["water"], 150, 2), "sand": polys(L["sand"], 25, 1.5), "tree": polys(L["tree"], 260, 3.2)}
        pv.append({"h": H["h"], "par": H["par"], "yd": H["yd"], "pin": [H["pin"][0], H["pin"][1], PX, PY],
                   "tee_px": [round(tee[0]), round(tee[1])], "k": round(k, 4), "ll": ll})
        if prev:
            os.makedirs(prev, exist_ok=True)
            o = img.copy(); col = {"fairway": (150, 230, 110), "green": (60, 255, 120), "sand": (255, 230, 160), "water": (60, 120, 255), "tree": (20, 70, 20)}
            for name in ("tree", "fairway", "water", "sand", "green"):
                for p in geo[str(H["h"])][name]: cv2.polylines(o, [np.array(p, np.int32)], True, col[name], 2)
            for p in geo[str(H["h"])]["outline"]: cv2.polylines(o, [np.array(p, np.int32)], True, (255, 255, 255), 1)
            cv2.circle(o, (PX, PY), 7, (255, 0, 0), -1); cv2.circle(o, (int(tee[0]), int(tee[1])), 7, (255, 255, 0), -1)
            cv2.putText(o, f"{H['h']} P{H['par']} {H['yd']}y k{k:.2f}", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            Image.fromarray(o).save(os.path.join(prev, f"{cid}_{H['h']:02d}.jpg"), quality=80)
        print(H["h"], "k", round(k, 3), "len", round(k * (tee[1] - PY)), "yd(card)", H["yd"], {n: len(v) for n, v in geo[str(H["h"])].items()})
    json.dump({"course": cid, "size": S, "source": "aerial", "holes": geo}, open(os.path.join(ROOT, "data/course_geo", f"{cid}.json"), "w"), separators=(",", ":"))
    out = {k: B[k] for k in ("cid", "garmin_name", "label", "source") if k in B}
    out.update({"layout": B.get("layout", B["garmin_name"]), "frame": "aerial", "pars": "".join(str(h["par"]) for h in B["holes"]), "holes": pv})
    json.dump(out, open(os.path.join(ROOT, "data/course_preview", f"{cid}.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
