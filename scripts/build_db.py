"""步驟 2：每場的核心資料 (data/rounds/*.csv) → 彙整資料庫 (data/processed/golf.db)。

資料庫是衍生物，隨時可刪掉重建；任何修正都改 data/rounds 的檔案，不要改資料庫。
用法：python3 scripts/build_db.py
"""
import csv, glob, json, os, shutil, sqlite3, tempfile

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
  distance_m INTEGER, distance_yd INTEGER, to_pin_start_m INTEGER, to_pin_end_m INTEGER);
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
    tot = par = nshots = adj = 0; has_putts = 0
    for seq, (_, r, ss) in sorted(holes.items()):
        st, p = int(r["hole_strokes"]), int(r["par"]); putts, pen, m = n(r["putts"]), n(r["penalties"]), n(r["hole_length_m"])
        tot += st; par += p; nshots += len(ss); adj += min(st, p + HOLE_CAP); has_putts |= r["putts_source"] in ("garmin", "manual")
        nonputt = sum(1 for s in ss if s["shot_type"] != "PUTT")
        unrec = None if putts is None else max(st - nonputt - (pen or 0) - putts, 0)   # 手錶漏記的擊球數
        con.execute("INSERT INTO holes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (rid, seq, int(r["hole"]), r["layout"], p, st, st - p, putts, r["putts_source"], unrec,
            pen, r["fairway"], m, round(m * YD) if m else None, len(ss), int(r["strokes_to_green"]) if r.get("strokes_to_green") else None, int(r["first_putt_steps"]) if r.get("first_putt_steps") else None))
        for s in ss:
            d = n(s["distance_m"])
            con.execute("INSERT INTO shots VALUES(?,?,?,?,?,?,?,?,?,?,?)", (rid, seq, int(s["shot_no"]), s["club"], s["shot_type"],
                s["start_lie"], s["end_lie"], d, round(d * YD), n(s["to_pin_start_m"]), n(s["to_pin_end_m"])))
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
