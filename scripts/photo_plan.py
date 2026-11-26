"""列出要下載空拍照片的球洞（從 reports/caddie.html 讀球洞範圍與經緯度換算），寫 data/photos/plan.json。
用法：python3 scripts/photo_plan.py 南寶 [其他球場簡稱…]
照片由 GitHub Actions（.github/workflows/photos.yml）執行 scripts/fetch_photos.py 下載。"""
import json, math, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
Z_TW, Z_OTHER, MARGIN_YD = 18, 18, 40

def report():
    s = (ROOT / "reports/caddie.html").read_text(encoding="utf-8")
    i = s.index("const REPORT = ") + len("const REPORT = "); j = s.index(";\nconst NS")
    return json.loads(s[i:j])

def from_px(ll, x, y):
    X, Y = x - ll[4], y - ll[7]; det = ll[2] * ll[6] - ll[3] * ll[5]
    return ll[0] + (-ll[5] * X + ll[2] * Y) / det, ll[1] + (ll[6] * X - ll[3] * Y) / det

def tw(lat, lon): return 21.7 < lat < 26.5 and 118 < lon < 122.3

def tile(lat, lon, z):
    n = 2 ** z; r = math.radians(lat)
    return int((lon + 180) / 360 * n), int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n)

def main(names):
    R = report(); sec = next(x for x in R["sections"] if x.get("kind") == "caddie")
    out = path = ROOT / "data/photos/plan.json"
    plan = json.loads(path.read_text()) if path.exists() else {}
    for c in sec["data"]:
        if names and c["short"] not in names: continue
        for L in c["layouts"]:
            g = R["geo"].get(L["cid"], {})
            for H in L["holes"]:
                if not H.get("ll"): continue
                pts = [p for poly in g.get(str(H["h"]), {}).get("outline", []) for p in poly] or [H["tee"], H["pin"]]
                m = MARGIN_YD / H["k"]
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                cs = [from_px(H["ll"], x, y) for x in (min(xs) - m, max(xs) + m) for y in (min(ys) - m, max(ys) + m)]
                la = [q[0] for q in cs]; lo = [q[1] for q in cs]
                t = tw(sum(la) / 4, sum(lo) / 4); z = Z_TW if t else Z_OTHER
                ax, ay = tile(max(la), min(lo), z); bx, by = tile(min(la), max(lo), z)
                plan[f'{L["cid"]}_{H["h"]}'] = {"course": c["short"], "z": z, "ax": ax, "ay": ay, "nx": bx - ax + 1, "ny": by - ay + 1, "src": "nlsc" if t else "esri"}
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=0, sort_keys=True))
    print(len(plan), "洞", sum(v["nx"] * v["ny"] for v in plan.values()), "張圖磚")

if __name__ == "__main__":
    main(sys.argv[1:])
