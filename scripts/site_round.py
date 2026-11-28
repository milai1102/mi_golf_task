"""只有網站（桿弟頁）紀錄、手錶沒有資料的場次，也要出現在場次總表：data/raw/caddie/*.json → data/rounds/日期_球場名.csv。

使用者的規則：手錶和網站兩個來源，只要其中一個有，就要有這一場的紀錄。
- 已經有同一天同一球場的場次檔（手錶的）就不動，兩個來源在 build_db / merge_shots 合併。
- 產生的場次檔每洞一列、沒有擊球列（擊球在合併時從網站的原始資料補進來），計分卡編號留空白，
  另外三欄 site_key（Garmin 球場代碼_洞號）、pin_lat、pin_lon 給合併用。
- 之後手錶的資料匯入時，import_garmin.py 會把這些計分卡編號空白的列整份換掉。
用法：python3 scripts/site_round.py（update.py 會自動跑；要先有 reports/caddie.html）
"""
import csv, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_caddie import caddie_courses, px_to_ll

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADER = ["date", "course", "layout", "tee", "seq", "hole", "par", "hole_strokes", "putts", "putts_source", "penalties", "fairway", "hole_length_m",
          "shot_no", "club", "shot_type", "start_lie", "end_lie", "distance_m", "to_pin_start_m", "to_pin_end_m", "garmin_scorecard_id",
          "strokes_to_green", "first_putt_steps", "site_key", "pin_lat", "pin_lon"]

def main():
    if not os.path.exists(os.path.join(ROOT, "reports/caddie.html")): print("還沒有 reports/caddie.html，略過"); return
    names = list(csv.DictReader(open(os.path.join(ROOT, "data/raw/course_names.csv"), encoding="utf-8")))
    zh = {r.get("short") or r["name_zh"]: r for r in names}
    lay = {}                                                     # 球場代碼 → Garmin 的分區名稱
    for p in glob.glob(os.path.join(ROOT, "data/raw/gps", "*.txt")):
        c = open(p, encoding="utf-8").readline().rstrip("\n").split("|")
        if c[0] == "C" and "/" not in c[2]: lay.setdefault(c[1], c[2])
    C = caddie_courses(); made = 0
    for p in sorted(glob.glob(os.path.join(ROOT, "data/raw/caddie", "*.json"))):
        d = json.load(open(p, encoding="utf-8")); short = d["course_short"]; nm = zh.get(short)
        if not nm: print(f"course_names.csv 找不到簡稱 {short}，略過 {os.path.basename(p)}"); continue
        name = nm["name_zh"]; path = os.path.join(ROOT, "data/rounds", f"{d['date']}_{name.replace(' ', '_').replace('&', 'and')}.csv")
        if os.path.exists(path):
            rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
            if any(r["garmin_scorecard_id"] for r in rows): continue        # 手錶的資料已經在了
        course = C.get(short)
        if not course: print(f"桿弟頁沒有 {short} 的球場資料，略過"); continue
        Hs = {(Lx["cid"], x["h"]): (Lx, x) for Lx in course["layouts"] for x in Lx["holes"]}
        def t0(v): return min([s["t"] for s in v.get("shots", []) if s.get("t")] + ([v["st"]] if v.get("st") else []) or [0])
        done = [(k, v) for k, v in d["holes"].items() if v.get("putts") is not None and v.get("on") is not None]
        done.sort(key=lambda kv: (t0(kv[1]) or 9e15))
        rows = []
        for seq, (k, v) in enumerate(done, 1):
            cid, h = k.split("_"); h = int(h); Lx, H = Hs.get((cid, h), (None, None))
            if not H: print(f"{os.path.basename(p)}：找不到 {k} 的球洞資料，略過這一洞"); continue
            pin = (H["pll"][0], H["pll"][1]) if H.get("pll") else px_to_ll(H["ll"], *H["pin"]) if H.get("ll") else None
            yd = ((H["pin"][0] - H["tee"][0]) ** 2 + (H["pin"][1] - H["tee"][1]) ** 2) ** 0.5 * H["k"] if H.get("tee") and H.get("k") else None
            pen = sum(s.get("pen") or 0 for s in v.get("shots", []))
            rows.append([d["date"], name, lay.get(cid) or f"{nm['garmin_name']} ~ {Lx.get('label') or ''}".rstrip(" ~"), "", seq, h, H["par"], v["on"] + v["putts"],
                         v["putts"], "manual", pen or "", "", round(yd / 1.09361) if yd else "", "", "", "", "", "", "", "", "", "",
                         v["on"], v.get("steps", ""), f"{cid}_{h}", round(pin[0], 7) if pin else "", round(pin[1], 7) if pin else ""])
        if not rows: continue
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(HEADER); w.writerows(rows)
        made += 1; print(f"只有網站紀錄的場次：寫入 {os.path.relpath(path, ROOT)}（{len(rows)} 洞、{sum(r[7] for r in rows)} 桿）")
    print(f"只有網站紀錄的場次：{made} 場")

if __name__ == "__main__": main()
