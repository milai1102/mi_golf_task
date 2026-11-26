"""步驟 2：每場的核心資料 (data/rounds/*.csv) → 彙整資料庫 (data/processed/golf.db)。

資料庫是衍生物，隨時可刪掉重建；任何修正都改 data/rounds 的檔案，不要改資料庫。
用法：python3 scripts/build_db.py
"""
import csv, glob, json, os, shutil, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_shots import merge_round   # 手錶與網站兩個來源的擊球，在這裡合併（見 merge_shots.py）

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUNDS, OUT = os.path.join(ROOT, "data/rounds"), os.path.join(ROOT, "data/processed")
_cfg = os.path.join(ROOT, "data/config.json")
SHORT_COURSES = set(json.load(open(_cfg, encoding="utf-8")).get("short_courses", [])) if os.path.exists(_cfg) else set()   # 短球場，獨立一類（data/config.json）
YD = 1.09361
n = lambda v: None if v in ("", None) else int(float(v))
HOLE_CAP = 4      # 算差點時每洞最多算到標準桿 +4（正式規則是「淨 Double Bogey」，需要各洞難度排序，這裡用 +4 近似）

# 球場難度 (Course Rating) 與坡度 (Slope)：Garmin 原始資料裡每張計分卡都有，用 garmin_scorecard_id 對回每一場
RATING = {}
for f in sorted(glob.glob(os.path.join(ROOT, "data/raw/parts/rounds_*.txt"))):
    for row in open(f, encoding="utf-8").read().strip().split(";"):
        c = row.split("|")
        if len(c) > 11: RATING[c[1]] = (int(c[7] or 0), float(c[10]) if c[10] else None, float(c[11]) if c[11] else None)
SHORT = {r["name_zh"]: r.get("short") or r["name_zh"] for r in csv.DictReader(open(os.path.join(ROOT, "data/raw/course_names.csv"), encoding="utf-8"))}   # 球場簡稱
MANUAL = {}       # 手動補的難度與坡度（Garmin 沒給的球場）：data/course_ratings.csv，欄位 file,rating,slope
mp = os.path.join(ROOT, "data/course_ratings.csv")
if os.path.exists(mp):
    for r in csv.DictReader(open(mp, encoding="utf-8-sig")): MANUAL[r["file"]] = (float(r["rating"]), float(r["slope"]))

def rating_of(fname, ids):
    """回傳 (難度, 坡度, 是否為近似)。18 洞一張卡直接用；前九後九兩張卡把難度相加、坡度平均；缺一半就用另一半的兩倍。"""
    if fname in MANUAL: return (*MANUAL[fname], 0)
    cards = [RATING[i] for i in ids if i in RATING]; ok = [c for c in cards if c[1] and c[2]]
    if not ok: return None, None, 0
    if len(cards) == 1 or ok[0][1] > 50: return ok[0][1], ok[0][2], 0
    slope = sum(c[2] for c in ok) / len(ok)
    if len(ok) == len(cards) == 2: return ok[0][1] + ok[1][1], slope, 0
    return ok[0][1] * 2, slope, 1

tmp = os.path.join(tempfile.mkdtemp(), "golf.db")  # 掛載的資料夾不支援 SQLite 鎖定，先在暫存區建好再複製
con = sqlite3.connect(tmp)
con.executescript("""
CREATE TABLE rounds(round_id INTEGER PRIMARY KEY, file TEXT, date TEXT, course TEXT, layouts TEXT, category TEXT,
  holes_played INTEGER, strokes INTEGER, par INTEGER, over_par INTEGER, shots_recorded INTEGER, has_putts INTEGER,
  rating REAL, slope REAL, rating_approx INTEGER, adj_strokes INTEGER, differential REAL, course_short TEXT);
CREATE TABLE holes(round_id INTEGER, seq INTEGER, hole INTEGER, layout TEXT, par INTEGER, strokes INTEGER, over_par INTEGER,
  putts INTEGER, putts_source TEXT, unrecorded INTEGER, penalties INTEGER, fairway TEXT, length_m INTEGER, length_yd INTEGER,
  shots_recorded INTEGER, strokes_to_green INTEGER, first_putt_steps INTEGER, PRIMARY KEY(round_id, seq));
CREATE TABLE shots(round_id INTEGER, seq INTEGER, shot_no INTEGER, club TEXT, shot_type TEXT, start_lie TEXT, end_lie TEXT,
  distance_m INTEGER, distance_yd INTEGER, to_pin_start_m INTEGER, to_pin_end_m INTEGER,
  source TEXT, watch_club TEXT, hazard TEXT);   -- source：garmin 只有手錶／site 只有網站（手錶漏記）／both 兩邊都有；watch_club：兩邊球桿不同時手錶記的球桿
""")
problems = []
for rid, path in enumerate(sorted(glob.glob(os.path.join(ROUNDS, "*.csv"))), 1):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    fname = os.path.basename(path); holes = {}
    for r in rows:
        seq = int(r["seq"]); key = (r["par"], r["hole_strokes"], r["putts"], r["putts_source"], r["penalties"], r["fairway"], r["hole_length_m"])
        if seq in holes and holes[seq][0] != key: problems.append(f"{fname} 第 {seq} 洞：同一洞的洞資料不一致")
        holes.setdefault(seq, (key, r, []))
        if r["shot_no"]: holes[seq][2].append(r)
    layouts = list(dict.fromkeys(r["layout"] for r in rows))
    M = merge_round(fname, rows)
    tot = par = nshots = adj = 0; has_putts = 0
    LIE = {"T": "TeeBox", "F": "Fairway", "R": "Rough", "B": "Bunker", "W": "Water"}
    for seq, (_, r, ss) in sorted(holes.items()):
        st, p = int(r["hole_strokes"]), int(r["par"]); putts, pen, m = n(r["putts"]), n(r["penalties"]), n(r["hole_length_m"])
        src, on, steps = r["putts_source"], n(r.get("strokes_to_green")), n(r.get("first_putt_steps"))
        mg = M.get(seq, {"shots": [], "hole": {}}); ms, mh = mg["shots"], mg["hole"]
        if src != "manual" and mh.get("putts") is not None:          # 網站記的成績（場次檔的人工修正優先）
            putts, src = mh["putts"], "manual"
            on = mh["on"] if mh.get("on") is not None else on; steps = mh["steps"] if mh.get("steps") is not None else steps
        if pen is None and any(x["pen"] for x in ms): pen = sum(x["pen"] for x in ms)
        tot += st; par += p; nshots += len(ms); adj += min(st, p + HOLE_CAP); has_putts |= src in ("garmin", "manual")
        unrec = None if putts is None else max(st - len(ms) - (pen or 0) - putts, 0)   # 合併兩個來源之後仍然沒記到的擊球數
        con.execute("INSERT INTO holes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (rid, seq, int(r["hole"]), r["layout"], p, st, st - p, putts, src, unrec,
            pen, r["fairway"], m, round(m * YD) if m else None, len(ms), on, steps))
        for k, x in enumerate(ms):
            row, nxt = x["row"], ms[k + 1] if k + 1 < len(ms) else None
            if row and not (nxt and nxt["src"] == "site") and not (not nxt and row is not None and x["src"] != "garmin" and False):
                d, ps, pe = n(row["distance_m"]), n(row["to_pin_start_m"]), n(row["to_pin_end_m"])
                stype, sl, el = row["shot_type"], row["start_lie"], row["end_lie"]
            else:                                                        # 只有網站的一桿，或後面接著手錶漏記的一桿：距離由兩桿的起點重算
                d, ps, pe = x["dist_m"], x["pin_s"], x["pin_e"]
                sl = row["start_lie"] if row else LIE.get(x["lie"], "Unknown")
                el = LIE.get(nxt["lie"], "Unknown") if nxt and nxt["lie"] else (nxt["row"]["start_lie"] if nxt and nxt["row"] else "Green" if not nxt else "Unknown")
                stype = row["shot_type"] if row else ("TEE" if k == 0 else "CHIP" if ps is not None and ps <= 55 else "APPROACH")
            if x.get("haz"): d = None                                    # 下水／OB 的那一桿不算距離（不進球桿距離的統計）
            if x["conflict"]: problems.append(f"{fname} 第 {seq} 洞第 {k + 1} 桿：球桿兩邊不一致（網站 {x['club']}、手錶 {x['conflict']}），請使用者確認後寫進 data/shot_fixes.csv")
            con.execute("INSERT INTO shots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (rid, seq, k + 1, x["club"], stype, sl, el, d, round(d * YD) if d is not None else None,
                ps, pe, x["src"], x["conflict"], x.get("haz")))
    course = rows[0]["course"]; k = len(holes)
    cat = "short_course" if course in SHORT_COURSES else "full_18" if k == 18 else "nine" if k == 9 else "partial"
    rating, slope, approx = rating_of(fname, list(dict.fromkeys(r["garmin_scorecard_id"] for r in rows))) if cat == "full_18" else (None, None, 0)
    diff = round(113 / slope * (adj - rating), 1) if rating else None      # 差點微分：這一場打出來的水準
    con.execute("INSERT INTO rounds VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (rid, fname, rows[0]["date"], course, " / ".join(layouts), cat, k,
        tot, par, tot - par, nshots, int(has_putts), rating, slope, approx, adj if rating else None, diff, SHORT.get(course, course)))
con.commit()
os.makedirs(OUT, exist_ok=True); shutil.copyfile(tmp, os.path.join(OUT, "golf.db"))
q = lambda s: con.execute(s).fetchall()
print("場次", q("SELECT category, COUNT(*), SUM(holes_played), SUM(strokes) FROM rounds GROUP BY 1"))
print("洞/桿", q("SELECT COUNT(*), SUM(strokes) FROM holes"), q("SELECT COUNT(*) FROM shots"))
print("差點", q("SELECT COUNT(*), SUM(differential IS NOT NULL), SUM(rating_approx) FROM rounds WHERE category='full_18'"))
miss = sorted({r[0] for r in q("SELECT course FROM rounds")} - set(SHORT))
if miss: problems.append(f"course_names.csv 沒有這些球場的簡稱：{miss}")
print("問題：", problems or "無")
