"""把瀏覽器下載的檔案放進專案：garmin_export_*.json（計分卡與座標）或 course_geo_export.json（球洞輪廓）。

用法：python3 scripts/ingest_export.py <檔案路徑> [更多檔案…]
- garmin_export_*.json → data/raw/parts/{rounds,holes,shots,clubs}_<時間>.txt 與 data/raw/gps/<計分卡編號>.txt
  已經匯入過的計分卡會自動略過，所以同一個檔重複執行、或每次都下載全部，都沒有問題。
- course_geo_export.json → data/course_geo/<球場代碼>.json（已經有的球場不覆寫；加 --force 才覆寫）
做完之後執行：python3 scripts/update.py
"""
import datetime, glob, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARTS, GPS, GEO = (os.path.join(ROOT, p) for p in ("data/raw/parts", "data/raw/gps", "data/course_geo"))

def djb(s):
    """和瀏覽器端同一個校驗碼（以 UTF-16 code unit 計算）。"""
    x = 5381; b = s.encode("utf-16-le")
    for i in range(0, len(b), 2): x = ((x * 33) ^ (b[i] | b[i + 1] << 8)) & 0xFFFFFFFF
    return x

def known_ids():
    ids = set()
    for p in glob.glob(os.path.join(PARTS, "rounds_*.txt")):
        for row in open(p, encoding="utf-8").read().strip().split(";"):
            c = row.split("|")
            if len(c) > 1: ids |= {c[0], c[1]}
    return ids

def ingest_rounds(ex, path):
    parts = ex["parts"]
    for k, (ln, h) in ex.get("check", {}).items():
        got = (len(parts[k].encode("utf-16-le")) // 2, djb(parts[k]))
        if got != (ln, h): sys.exit(f"錯誤：{path} 的 {k} 校驗不符（檔案不完整或被改過）。請重新下載。")
    known = known_ids()
    rounds = [r for r in parts["rounds"].split(";") if r and r.split("|")[0] not in known]
    new = {r.split("|")[0] for r in rounds}
    if not new:
        print(f"{os.path.basename(path)}：沒有新的計分卡（{len([r for r in parts['rounds'].split(';') if r])} 張都已匯入過）"); return 0
    keep = lambda text: ";".join(r for r in text.split(";") if r and r.split(",")[0] in new)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(PARTS, exist_ok=True); os.makedirs(GPS, exist_ok=True)
    out = {"rounds": ";".join(rounds), "holes": keep(parts["holes"]), "shots": keep(parts["shots"]), "clubs": parts["clubs"]}
    for k, v in out.items():
        if v: open(os.path.join(PARTS, f"{k}_{stamp}.txt"), "w", encoding="utf-8").write(v)
    n = 0
    for sid, text in ex.get("gps", {}).items():
        if str(sid) in new: open(os.path.join(GPS, f"{sid}.txt"), "w", encoding="utf-8").write(text); n += 1
    print(f"{os.path.basename(path)}：新增 {len(new)} 張計分卡、{len([s for s in out['shots'].split(';') if s])} 桿、{n} 個座標檔")
    if ex.get("failed"): print(f"  注意：下載時有 {len(ex['failed'])} 張計分卡失敗，沒有包含在內：{[f[0] for f in ex['failed']]}")
    return len(new)

def _area(p): return abs(sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))) / 2
def _dp(pts, tol):
    if len(pts) < 4: return pts
    keep = [0] * len(pts); keep[0] = keep[-1] = 1; st = [(0, len(pts) - 1)]
    while st:
        a, b = st.pop(); (x1, y1), (x2, y2) = pts[a], pts[b]; L = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** .5 or 1; md, mi = 0, -1
        for i in range(a + 1, b):
            d = abs((x2 - x1) * (y1 - pts[i][1]) - (x1 - pts[i][0]) * (y2 - y1)) / L
            if d > md: md, mi = d, i
        if md > tol: keep[mi] = 1; st += [(a, mi), (mi, b)]
    return [p for p, k in zip(pts, keep) if k]

def ingest_geo(ex, path, force):
    os.makedirs(GEO, exist_ok=True); made = skipped = 0
    for gid, holes in ex.get("courses", {}).items():
        cid = str(gid).zfill(6); out = os.path.join(GEO, f"{cid}.json")
        if os.path.exists(out) and not force: skipped += 1; continue
        for g in holes.values():                              # 樹的輪廓最多：去掉太小的、再簡化一次，檔案才不會太大
            trees = []
            for p in g.get("tree", []):
                if _area(p) < 260: continue
                mid = len(p) // 2; q = _dp(p[:mid + 1], 3.2) + _dp(p[mid:], 3.2)[1:]
                if len(q) >= 4: trees.append(q)
            g["tree"] = trees
        json.dump({"course": cid, "size": ex.get("size", 730), "holes": holes}, open(out, "w", encoding="utf-8"), separators=(",", ":")); made += 1
    print(f"{os.path.basename(path)}：寫入 {made} 個球場的輪廓，已存在略過 {skipped}" + (f"；下載時失敗 {len(ex['failed'])} 洞" if ex.get("failed") else ""))

def main(argv):
    force = "--force" in argv; files = [a for a in argv if not a.startswith("--")]
    if not files: sys.exit(__doc__)
    for p in files:
        p = os.path.expanduser(p)
        if not os.path.exists(p): sys.exit(f"錯誤：找不到檔案 {p}")
        try: ex = json.load(open(p, encoding="utf-8"))
        except Exception as e: sys.exit(f"錯誤：{p} 不是完整的 JSON（{e}）。請重新下載。")
        if ex.get("kind") == "course_geo": ingest_geo(ex, p, force)
        elif "parts" in ex: ingest_rounds(ex, p)
        else: sys.exit(f"錯誤：{p} 不是 garmin_download.js 或 garmin_course_download.js 產生的檔案。")
    print("下一步：python3 scripts/update.py")

if __name__ == "__main__":
    main(sys.argv[1:])
