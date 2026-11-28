"""把桿弟頁匯出的測試紀錄（caddie_log_*.json）轉成網站來源的原始資料：data/raw/caddie/YYYY-MM-DD_簡稱.json。

只留每一洞的成績（ON、推桿、第一推步數）和每一桿（球桿、按下去的時間、位置、球位、罰桿）；
整天的 GPS 軌跡和操作紀錄不存（那份測試紀錄不進公開的 repository）。
已經有同一天同一球場的檔案時，會整份換成新的（網站上的紀錄是使用者自己改過的最新版）。

用法：python3 scripts/ingest_caddie.py caddie_log_2026-11-26.json
（先跑過 build_report.py：要用 reports/caddie.html 的球場資料把「球場簡稱|分區|洞號」對回 Garmin 的球場代碼與洞號）
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def caddie_courses():
    s = open(os.path.join(ROOT, "reports/caddie.html"), encoding="utf-8").read()
    R = json.loads(re.search(r"const REPORT = (\{.*?\});\n", s, re.S).group(1))
    sec = [x for x in R["sections"] if x.get("kind") == "caddie"][0]
    return {c["short"]: c for c in sec["data"]}

def px_to_ll(ll, x, y):
    """球洞圖像素 → 經緯度（H.ll 的反函數）。"""
    lat0, lon0, a, b, c, d, e, f = ll
    det = a * e - b * d
    dlon = ((x - c) * e - b * (y - f)) / det; dlat = (a * (y - f) - d * (x - c)) / det
    return lat0 + dlat, lon0 + dlon

def best_fix(s):
    """按下去前最新的一筆（f）和按下去之後另外要的一筆（g），取誤差小的。"""
    c = [v for v in (s.get("g"), s.get("f")) if v and v[0] is not None]
    if not c: return None
    v = min(c, key=lambda v: v[2] if len(v) > 2 and v[2] is not None else 999)
    return v[0], v[1], v[2] if len(v) > 2 else None

def main(path):
    L = json.load(open(path, encoding="utf-8"))
    days = [L.get("day") or {}] + list((L.get("days") or {}).values())   # 當天＋之前幾天保留下來的
    days = [d for d in days if d.get("day") and d.get("holes")]
    if not days: sys.exit("這份紀錄裡沒有任何一天的成績")
    seen = set()
    for d in days:
        if d["day"] in seen: continue
        seen.add(d["day"]); one(L, d)

def one(L, day):
    date = day.get("day"); holes = day.get("holes", {}); cids = L.get("cids") or {}
    C = caddie_courses(); out = {}
    for key, v in holes.items():
        short, li, h = key.split("|")
        if key in cids and cids[key]: ck = cids[key]
        else:
            Lay = C[short]["layouts"][int(li)]; ck = f"{Lay['cid']}_{h}"
        cid, hn = ck.split("_")
        Hx = next((x for c in [C.get(short)] if c for Lay in c["layouts"] if Lay["cid"] == cid for x in Lay["holes"] if x["h"] == int(hn)), None)
        shots = []
        for s in v.get("s", []):
            fx = best_fix(s); src = "gps"
            if not fx and Hx and Hx.get("ll") and s.get("p"):        # 在圖上點的（補一桿）只有圖上位置
                la, lo = px_to_ll(Hx["ll"], *s["p"]); fx = (la, lo, None); src = "map"
            shots.append({k: val for k, val in {"club": s.get("c"), "t": s.get("t") or None, "lat": round(fx[0], 6) if fx else None, "lon": round(fx[1], 6) if fx else None,
                          "acc": fx[2] if fx else None, "pos": src if fx else None, "lie": s.get("l"), "pen": s.get("pen") or None,
                          "ins": 1 if s.get("ins") else None, "auto": 1 if s.get("auto") else None, "est": 1 if s.get("est") else None}.items() if val is not None})
        out[ck] = {k: val for k, val in {"on": v.get("on"), "putts": v.get("putts"), "steps": v.get("steps"), "st": v.get("st"), "shots": shots}.items() if val is not None}
    short = next(iter(holes)).split("|")[0]
    dst = os.path.join(ROOT, "data/raw/caddie", f"{date}_{short}.json")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    json.dump({"date": date, "course_short": short, "source": "mi-caddie", "exported": L.get("exported"), "holes": out},
              open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"寫入 {os.path.relpath(dst, ROOT)}：{len(out)} 洞、{sum(len(v.get('shots', [])) for v in out.values())} 桿")

if __name__ == "__main__":
    for p in sys.argv[1:]: main(p)
