"""步驟 3：彙整資料庫 (data/processed/golf.db) → 一頁式報表 (reports/index.html)。

所有圖表都在同一頁，一張圖一個區塊。新增圖表：寫一個 section_xxx(con) 函式回傳
{id, title, note, kind, data, table}，加進 SECTIONS，並在頁面的 DRAW 物件加對應的繪圖函式。
用法：python3 scripts/build_report.py
"""
import csv, datetime, glob, json, os, shutil, sqlite3, statistics, tempfile
import math, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merge_shots   # 手錶與網站兩個來源的擊球合併

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB, OUT = os.path.join(ROOT, "data/processed/golf.db"), os.path.join(ROOT, "reports/index.html")
# 個人設定（球桿名稱、球袋、簡寫、短球場、要不要訓練計劃頁）：data/config.json。新使用者用 scripts/setup_config.py 從自己的資料產生，見 docs/SETUP.md
CFG = json.load(open(os.path.join(ROOT, "data/config.json"), encoding="utf-8")) if os.path.exists(os.path.join(ROOT, "data/config.json")) else {}
_C = CFG.get("clubs", {})
DRIVER, FAIRWAY, CHIP_CLUB, SAND_CLUB = _C.get("driver", "Driver"), _C.get("fairway", ""), _C.get("chip", ""), _C.get("sand", "")
WOODS = set(CFG.get("woods", [])); ABBR = CFG.get("abbr", {}); SITE_TITLE = CFG.get("site_title", "高爾夫數據"); PLAN_ON = bool(CFG.get("plan", False))
MIN_N = 10        # 樣本少於此數的球桿不畫
SUCCESS = 0.9     # 成功：打到該桿眾數的 90% 以上（整頁統一用 90%，色條也涵蓋 90% 的球）

def t_pvalue(t, df):
    """t 分布的雙尾 p 值（數值積分，不依賴 scipy）。"""
    import math
    c = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)
    f = lambda x: c * (1 + x * x / df) ** (-(df + 1) / 2)
    a, n = abs(t), 4000; hstep = 80 / n
    area = sum((1 if i in (0, n) else 4 if i % 2 else 2) * f(a + i * hstep) for i in range(n + 1)) * hstep / 3
    return min(1.0, 2 * area)

def add_trend(sec, unit):
    """替趨勢圖加上線性迴歸的趨勢線：對場次順序迴歸，回報每 10 場的變化與是否顯著。"""
    ys = [d["v"] for d in sec["data"]]; n = len(ys); xs = list(range(n))
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs); b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx; a = my - b * mx
    sse = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys)); se = (sse / (n - 2) / sxx) ** 0.5
    pv = t_pvalue(b / se, n - 2) if se else 0.0
    lo_t, hi_t = 0.0, 20.0                                    # 用二分法找 95% 的 t 臨界值
    for _ in range(40):
        mid = (lo_t + hi_t) / 2
        if t_pvalue(mid, n - 2) > 0.05: lo_t = mid
        else: hi_t = mid
    s_res = (sse / (n - 2)) ** 0.5
    band = [[round(a + b * x - hi_t * s_res * (1 / n + (x - mx) ** 2 / sxx) ** 0.5, 2),
             round(a + b * x + hi_t * s_res * (1 / n + (x - mx) ** 2 / sxx) ** 0.5, 2)] for x in xs]   # 迴歸線的 95% 信賴帶
    sec.setdefault("opts", {})["trend"] = {"y0": round(a, 2), "y1": round(a + b * (n - 1), 2), "per10": round(b * 10, 1), "sig": pv < 0.05,
                                           "unit": unit, "band": band}
    sec["note"] += f" 橫軸依場次順序等距排列。趨勢線是對場次順序做線性迴歸：每 10 場 {b * 10:+.1f}{unit}，p = {pv:.3f}；p 小於 0.05 才算統計上顯著。趨勢線畫成虛線，淡色帶狀是迴歸線的 95% 信賴帶。"
    return sec

def pct(a, p):
    a = sorted(a); i = (len(a) - 1) * p; lo = int(i); hi = min(lo + 1, len(a) - 1)
    return a[lo] + (a[hi] - a[lo]) * (i - lo)

ROLE_LABEL = {"full": "全揮桿", "chip": "切球桿", "recovery": "救球桿", "practice": "練習中"}   # 順序即圖上由左到右；練習中放最右

def club_roles():
    """data/club_roles.csv：使用者指定每支桿的用途（full / chip / recovery）。沒列到的當 full。"""
    path = os.path.join(ROOT, "data/club_roles.csv")
    return {r["club"]: r["role"] for r in csv.DictReader(open(path, encoding="utf-8-sig"))} if os.path.exists(path) else {}

def kde_mode(a):
    """眾數：用高斯核密度估計找出分布的最高點（最常打出的距離），比 10 碼一格的直方圖精細。"""
    import math
    n = len(a); sd = statistics.pstdev(a); iqr = pct(a, .75) - pct(a, .25)
    h = max(0.9 * min(sd, iqr / 1.34 if iqr else sd) * n ** -0.2, 3)
    best, peak = None, -1
    for x in range(int(min(a)), int(max(a)) + 1):
        d = sum(math.exp(-0.5 * ((x - v) / h) ** 2) for v in a)
        if d > peak: best, peak = x, d
    return best

def section_club_distance(con):
    roles = club_roles()
    recent = {r[0] for r in con.execute("SELECT round_id FROM rounds ORDER BY date DESC LIMIT 10")}
    rows = con.execute("""SELECT club, distance_yd, shot_type, round_id FROM shots
        WHERE shot_type != 'PUTT' AND distance_yd IS NOT NULL AND club NOT IN ('','Putter','已刪除球桿','未知球桿')""").fetchall()
    by, by_recent = {}, {}
    for club, yd, st, rid in rows:
        if rid in recent and not (roles.get(club, "full") in ("full", "practice") and st in ("CHIP", "RECOVERY")): by_recent.setdefault(club, []).append(yd)
        # 全揮桿的桿子排除 Garmin 標成切球／救球的擊球；切球桿、救球桿本來就是做這件事，全部計入
        if roles.get(club, "full") in ("full", "practice") and st in ("CHIP", "RECOVERY"): continue
        by.setdefault(club, []).append(yd)
    data, skipped = [], []
    for club, a in by.items():
        if len(a) < MIN_N: skipped.append(f"{club}（{len(a)} 桿）"); continue
        data.append({"club": club, "role": roles.get(club, "full"), "group": ROLE_LABEL[roles.get(club, "full")], "n": len(a), "pts": sorted(a), "p5": round(pct(a, .05)), "p95": round(pct(a, .95)), "p10": round(pct(a, .1)), "q1": round(pct(a, .25)), "med": round(pct(a, .5)),
                     "q3": round(pct(a, .75)), "p90": round(pct(a, .9)), "mean": round(statistics.mean(a)), "mode": kde_mode(a),
                     "min": min(a), "max": max(a)})
    data.sort(key=lambda d: (list(ROLE_LABEL).index(d["role"]), -d["med"]))
    total = sum(len(a) for a in by.values())          # 使用比例的分母：所有球桿的擊球（不含推桿）
    for d in data:
        d["share"] = round(100 * d["n"] / total)
        d["success"] = round(100 * sum(1 for v in d["pts"] if v >= SUCCESS * d["mode"]) / d["n"])   # 成功率：打到眾數的 90% 以上
        rc = by_recent.get(d["club"], []); total_recent = sum(len(a) for a in by_recent.values())
        d["share10"] = round(100 * len(rc) / total_recent)
        d["success10"] = round(100 * sum(1 for v in rc if v >= SUCCESS * d["mode"]) / len(rc)) if len(rc) >= MIN_N else None   # 近 10 場不到 10 桿不算
    note = "切球桿的成功率是另一種定義：每一洞第一次在離旗桿 60 碼內切球，只切一次就上果嶺的比例，不分球桿，整組共用一個數字（細節見第二頁「切球一次上果嶺的比例」）。" \
           "近 10 場的兩列只算最近 10 場，箭頭代表和全部場次相比升或降 3 個百分點以上；近 10 場不到 10 桿的球桿不算成功率。使用率＝該桿佔全部擊球的比例。成功率＝該桿打到眾數的 90% 以上的比例。粗體數字是眾數（最常打出的距離，由核密度估計的最高點求得），細線旁的小字是中位數。平均會被打壞的球拉低，所以不標在圖上。" \
           "色條每格 10 碼，顏色是球落在該距離的機率（以該桿最高的一格為紅）。色條只畫成功的範圍（眾數的 90% 以上，到第 95 百分位為止），彩色的點是成功的球，灰點是沒成功的球。" \
           "每洞最後一筆被記錄的擊球，Garmin 不知道落點，會當成打到旗桿，所以那一桿的距離其實是「擊球點到旗桿」的距離。" \
           "球桿用途依 data/club_roles.csv 分組：全揮桿的桿子排除切球與救球，切球桿與救球桿則全部計入，所以三組的距離不能互相比較。"
    if skipped: note += f" 樣本不足 {MIN_N} 桿未列入：{'、'.join(skipped)}。"
    cols = [("club", "球桿"), ("share", "使用率 %"), ("share10", "近 10 場使用率 %"), ("success", "成功率 %"), ("success10", "近 10 場成功率 %"), ("n", "桿數"), ("mode", "眾數"), ("p5", "5%"), ("q1", "25%"), ("med", "中位數"), ("q3", "75%"), ("p95", "95%"), ("mean", "平均")]
    # 切球桿打多遠是自己決定的，「打到眾數的 90%」沒有意義；改用不分球桿的「切球一次上果嶺」比例，整組共用一個數字
    past, rec = section_chip_once(con)["data"]["cols"]
    for d in data:
        if d["role"] == "chip":
            d["success"] = round(100 * (past["x"] + rec["x"]) / (past["n"] + rec["n"])); d["success10"] = round(rec["v"]); d["grouped"] = True
    table = {"head": [label for _, label in cols], "rows": [["—" if d[k] is None else d[k] for k, _ in cols] for d in data]}
    return {"id": "club-distance", "title": "各球桿擊球距離（碼）", "note": note, "kind": "box", "data": data, "table": table}

CHIP_BUCKETS = [(0, 20), (20, 30), (30, 40), (40, 60), (60, 80), (80, 100)]   # 切球前離旗桿（碼）
CHIP_ROWS = ["再 1 桿內進洞", "再 2 桿", "再 3 桿", "再 4 桿以上"]

def section_chip(con):
    roles = club_roles(); clubs = [c for c, r in roles.items() if r == "chip"]
    rows = con.execute(f"""SELECT s.to_pin_start_m, h.strokes - s.shot_no FROM shots s JOIN holes h USING(round_id, seq)
        WHERE s.shot_type != 'PUTT' AND s.to_pin_start_m IS NOT NULL AND s.club IN ({','.join('?' * len(clubs))})""", clubs).fetchall()
    cols = []
    for lo, hi in CHIP_BUCKETS:
        rem = [max(r, 0) for m, r in rows if lo <= m * 1.09361 < hi]
        cnt = [sum(1 for r in rem if r <= 1), rem.count(2), rem.count(3), sum(1 for r in rem if r >= 4)]
        mean = round(statistics.mean(rem), 2) if rem else None
        cols.append({"label": f"{lo}–{hi}" if lo else f"{hi} 內", "n": len(rem), "mean": mean,
                     "top": f"平均再 {mean:.1f} 桿" if rem else "", "sub": f"{len(rem)} 桿",
                     "p": [round(100 * c / len(rem), 1) if rem else 0 for c in cnt]})
    note = f"對象是切球桿（{'、'.join(clubs)}）在 100 碼內的每一次擊球。「再幾桿進洞」= 該洞總桿 − 這一桿是該洞第幾筆紀錄；" \
           "前面如果有沒被手錶記錄到的擊球，這個數字會偏高。每一欄的機率加總為 100%。"
    table = {"head": ["離旗桿（碼）", "桿數"] + CHIP_ROWS + ["平均再幾桿"],
             "rows": [[c["label"], c["n"]] + [f"{v}%" for v in c["p"]] + [c["mean"]] for c in cols]}
    return {"id": "chip", "title": "切球之後還要幾桿進洞", "note": note, "kind": "heat",
            "data": {"cols": cols, "rows": CHIP_ROWS, "xlabel": "切球前離旗桿（碼）"}, "table": table}

def section_putt_trend(con):
    rows = con.execute("""SELECT r.date, r.course, r.holes_played, r.has_putts, AVG(h.putts) * 18, SUM(h.putts IS NOT NULL)
        FROM rounds r JOIN holes h USING(round_id) WHERE r.category != 'short_course' GROUP BY r.round_id ORDER BY r.date""").fetchall()
    data = []
    for i, (date, course, holes, measured, p18, n) in enumerate(rows):
        last = [r[4] for r in rows[max(0, i - 4):i + 1]]
        data.append({"date": date, "course": course, "v": round(p18, 1), "measured": bool(measured), "holes": holes,
                     "avg": round(statistics.mean(last), 1) if len(last) >= 3 else None})
    note = "每場的推桿數換算成 18 洞（平均每洞推桿 × 18），不含中油短球場。空心點是估算值（總桿 − 記錄擊球 − 罰桿，限 1 到 3），" \
           "實心點是手錶實際輸入。估算值可能略為高估。折線是最近 5 場的移動平均。"
    table = {"head": ["日期", "球場", "洞數", "推桿／18 洞", "來源", "近 5 場平均"],
             "rows": [[d["date"], d["course"], d["holes"], d["v"], "實際" if d["measured"] else "估算", d["avg"] or ""] for d in data]}
    return {"id": "putt-trend", "title": "每場推桿數（換算 18 洞）", "note": note, "kind": "trend", "data": data, "table": table}

def section_putt_par(con):
    rows = con.execute("""SELECT MIN(h.par, 5), h.putts FROM holes h JOIN rounds r USING(round_id)
        WHERE r.category != 'short_course' AND h.putts IS NOT NULL""").fetchall()
    names = {3: "三桿洞", 4: "四桿洞", 5: "五桿洞"}
    cols = [putt_col(names[par], [p for q, p in rows if q == par]) for par in (3, 4, 5)]
    labels = PUTT_ROWS
    note = "不含中油短球場；六桿洞併入五桿洞。推桿數多數是估算值（見上一張圖的說明）。每一欄的機率加總為 100%。"
    table = putt_table("洞型", cols)
    return {"id": "putt-par", "title": "各洞型的推桿數", "note": note, "kind": "heat",
            "data": {"cols": cols, "rows": labels, "xlabel": ""}, "table": table}

PUTT_ROWS = ["1 推", "2 推", "3 推以上"]

def putt_col(label, a, sub_unit="洞"):
    cnt = [a.count(1), a.count(2), sum(1 for p in a if p >= 3)]; mean = round(statistics.mean(a), 2)
    return {"label": label, "n": len(a), "mean": mean, "top": f"平均 {mean:.2f} 推", "sub": f"{len(a)} {sub_unit}",
            "p": [round(100 * c / len(a), 1) for c in cnt]}

def putt_table(first, cols):
    return {"head": [first, "洞數"] + PUTT_ROWS + ["平均推桿"],
            "rows": [[c["label"], c["n"]] + [f"{v}%" for v in c["p"]] + [c["mean"]] for c in cols]}

APPROACH_BUCKETS = [(0, 20), (20, 40), (40, 70), (70, 110), (110, 150), (150, 999)]   # 上果嶺那一桿離旗桿（碼）

def section_putt_approach(con):
    rows = con.execute("""SELECT s.to_pin_start_m * 1.09361, h.putts FROM holes h JOIN rounds r USING(round_id)
        JOIN shots s ON s.round_id = h.round_id AND s.seq = h.seq
        WHERE r.category != 'short_course' AND h.putts IS NOT NULL AND s.end_lie = 'Green' AND s.shot_type != 'PUTT'
          AND s.to_pin_start_m IS NOT NULL
          AND s.shot_no = (SELECT MAX(shot_no) FROM shots x WHERE x.round_id = h.round_id AND x.seq = h.seq AND x.shot_type != 'PUTT')""").fetchall()
    cols = []
    for lo, hi in APPROACH_BUCKETS:
        a = [p for yd, p in rows if lo <= yd < hi]
        if a: cols.append(putt_col(f"{hi} 內" if lo == 0 else f"{lo} 以上" if hi == 999 else f"{lo}–{hi}", a))
    note = "只算最後一筆記錄擊球停在果嶺上的洞，以那一桿擊球前離旗桿的距離分組。不含中油短球場。推桿數多數是估算值。"
    return {"id": "putt-approach", "title": "上果嶺那一桿的距離與推桿數", "note": note, "kind": "heat",
            "data": {"cols": cols, "rows": PUTT_ROWS, "xlabel": "上果嶺那一桿離旗桿（碼）"}, "table": putt_table("離旗桿（碼）", cols)}

def section_putt_share(con):
    rows = con.execute("""SELECT r.date, r.course, r.holes_played, r.has_putts, 100.0 * SUM(h.putts) / SUM(h.strokes)
        FROM rounds r JOIN holes h USING(round_id) WHERE r.category != 'short_course' AND h.putts IS NOT NULL
        GROUP BY r.round_id ORDER BY r.date""").fetchall()
    data = []
    for i, (date, course, holes, measured, v) in enumerate(rows):
        last = [r[4] for r in rows[max(0, i - 4):i + 1]]
        data.append({"date": date, "course": course, "v": round(v, 1), "measured": bool(measured), "holes": holes,
                     "avg": round(statistics.mean(last), 1) if len(last) >= 3 else None})
    note = "每場的推桿數 ÷ 總桿（只算有推桿數的洞），不含中油短球場。空心點的推桿是估算值。折線是最近 5 場的移動平均。"
    table = {"head": ["日期", "球場", "洞數", "推桿佔比 %", "來源", "近 5 場平均"],
             "rows": [[d["date"], d["course"], d["holes"], d["v"], "實際" if d["measured"] else "估算", d["avg"] or ""] for d in data]}
    return {"id": "putt-share", "title": "推桿佔總桿的比例", "note": note, "kind": "trend", "data": data, "table": table,
            "opts": {"unit": "%", "step": 5}}

def section_putt_course(con):
    rows = con.execute("""SELECT r.course, h.putts FROM holes h JOIN rounds r USING(round_id)
        WHERE r.category != 'short_course' AND h.putts IS NOT NULL""").fetchall()
    by = {}
    for course, p in rows: by.setdefault(course, []).append(p)
    short = lambda n: n.replace("高爾夫鄉村俱樂部", "").replace("高爾夫俱樂部", "").replace("高爾夫球場", "").replace("國際", "")
    cols = sorted((putt_col(short(c), a) for c, a in by.items() if len(a) >= 36), key=lambda c: c["mean"])
    note = "只列打過 36 洞以上的球場，由平均推桿少到多排列。不含中油短球場。推桿數多數是估算值。"
    return {"id": "putt-course", "title": "各球場的推桿數", "note": note, "kind": "heat",
            "data": {"cols": cols, "rows": PUTT_ROWS, "xlabel": ""}, "table": putt_table("球場", cols)}

RECENT_N = 10   # 「最近」＝最近 10 場

def section_club_recent(con):
    roles = club_roles()
    recent = {r[0] for r in con.execute("SELECT round_id FROM rounds ORDER BY date DESC LIMIT ?", (RECENT_N,))}
    rows = con.execute("""SELECT club, distance_yd, shot_type, round_id FROM shots
        WHERE shot_type != 'PUTT' AND distance_yd IS NOT NULL AND club NOT IN ('','Putter','已刪除球桿','未知球桿')""").fetchall()
    by = {}
    for club, yd, st, rid in rows:
        if roles.get(club, "full") in ("full", "practice") and st in ("CHIP", "RECOVERY"): continue
        by.setdefault(club, ([], []))[rid in recent].append(yd)
    data = []
    tot_old, tot_new = sum(len(o) for o, _ in by.values()), sum(len(n) for _, n in by.values())   # 各時期全部擊球，使用率的分母
    for club, (old, new) in by.items():
        if len(old) < 30 or len(new) < 30: continue      # 樣本太少的球桿色條零散、眾數亂跳，不畫
        ref = kde_mode(old + new)                       # 成功率的門檻：全部資料的眾數，兩個時期用同一把尺
        side = lambda a: {"pts": sorted(a), "n": len(a), "p10": round(pct(a, .05)), "p90": round(pct(a, .95)), "mode": kde_mode(a),   # 比較圖的色條範圍：第 5 到第 95 百分位
                          "success": round(100 * sum(1 for v in a if v >= SUCCESS * ref) / len(a))}
        role = roles.get(club, "full")
        data.append({"club": club, "role": role, "group": ROLE_LABEL[role], "ref": ref, "old": side(old), "new": side(new)})
        data[-1]["old"]["share"] = round(100 * len(old) / tot_old); data[-1]["new"]["share"] = round(100 * len(new) / tot_new)
    data.sort(key=lambda d: (list(ROLE_LABEL).index(d["role"]), -d["ref"]))
    note = f"每支桿左邊窄的是過去（最近 {RECENT_N} 場之前）、右邊寬的是最近 {RECENT_N} 場。色條涵蓋 90% 的球，數字是眾數。" \
           "兩個時期都至少要有 30 桿才列入。"
    table = {"head": ["球桿", "過去桿數", "近期桿數", "過去使用率 %", "近期使用率 %", "過去眾數", "近期眾數", "過去成功率 %", "近期成功率 %"],
             "rows": [[d["club"], d["old"]["n"], d["new"]["n"], d["old"]["share"], d["new"]["share"], d["old"]["mode"], d["new"]["mode"],
                       d["old"]["success"], d["new"]["success"]] for d in data]}
    return {"id": "club-recent", "title": f"各球桿：最近 {RECENT_N} 場與過去比較（碼）", "note": note, "kind": "compare", "data": data, "table": table}

SEQ_GOOD, SEQ_BAD = 0.9, 0.5   # 依前後擊球判斷：前進量 ÷ 目標 ≥ 0.9 成功，< 0.5 失敗
SEQ_ROWS = ["成功", "普通", "失敗"]

def classified_shots(con):
    """用擊球序列判斷每一桿全揮桿的結果。
    目標 = min(該桿眾數, 擊球前離旗桿的距離)；前進量 = 擊球前離旗桿 − 擊球後離旗桿（看下一桿從哪裡打）。
    該洞最後一筆記錄擊球之後只剩推桿，視為打上果嶺＝成功。回傳 [(round_id, seq, shot_no, club, 結果 0/1/2)]。"""
    roles = club_roles()
    rows = con.execute("""SELECT s.round_id, s.seq, s.shot_no, s.club, s.shot_type, s.distance_yd, s.to_pin_start_m, s.to_pin_end_m,
            s.shot_no = (SELECT MAX(shot_no) FROM shots x WHERE x.round_id = s.round_id AND x.seq = s.seq AND x.shot_type != 'PUTT')
        FROM shots s JOIN rounds r USING(round_id) WHERE r.category != 'short_course' ORDER BY s.round_id, s.seq, s.shot_no""").fetchall()
    full = [r for r in rows if roles.get(r[3]) == "full" and r[4] not in ("CHIP", "RECOVERY", "PUTT") and r[5] is not None and r[6] is not None and r[7] is not None]
    by = {}
    for r in full: by.setdefault(r[3], []).append(r[5])
    mode = {c: kde_mode(a) for c, a in by.items() if len(a) >= MIN_N}
    out = []
    for rid, seq, no, club, st, yd, start, end, last in full:
        if club not in mode: continue
        target = min(mode[club], start * 1.09361)
        if target < 40: continue                                   # 太近的不算全揮桿
        ratio = 1.0 if (last and end == 0) else (start - end) * 1.09361 / target
        out.append((rid, seq, no, club, 0 if ratio >= SEQ_GOOD else 1 if ratio >= SEQ_BAD else 2))
    return out

def seq_col(label, a, sub=None):
    return {"label": label, "n": len(a), "top": "", "sub": sub or f"{len(a)} 桿", "p": [round(100 * a.count(k) / len(a), 1) for k in (0, 1, 2)]}

SEQ_NOTE = "判斷方式：看下一桿從哪裡打，算出這一桿朝旗桿前進了多少，再除以目標（該桿的眾數距離，離旗桿比較近時取離旗桿的距離）。" \
           f"達到 {int(SEQ_GOOD * 100)}% 以上算成功，不到 {int(SEQ_BAD * 100)}% 算失敗；一洞最後一筆記錄擊球之後只剩推桿，視為成功。" \
           "只算全揮桿球桿，不含中油短球場。"

def section_seq_club(con):
    by = {}
    for rid, seq, no, club, res in classified_shots(con): by.setdefault(club, []).append(res)
    cols = sorted((seq_col(c, a) for c, a in by.items() if len(a) >= 20), key=lambda c: -c["n"])
    table = {"head": ["球桿", "桿數"] + [r + " %" for r in SEQ_ROWS], "rows": [[c["label"], c["n"]] + c["p"] for c in cols]}
    return {"id": "seq-club", "title": "各球桿的成功與失敗（由前後擊球判斷）", "note": SEQ_NOTE, "kind": "heat",
            "data": {"cols": cols, "rows": SEQ_ROWS, "xlabel": ""}, "table": table}

def section_seq_chain(con):
    shots = classified_shots(con); prev = {(r, q, n): res for r, q, n, c, res in shots}
    groups = {"上一桿成功": [], "上一桿普通": [], "上一桿失敗": []}; first = []
    for rid, seq, no, club, res in shots:
        p = prev.get((rid, seq, no - 1))
        (first if p is None else groups[["上一桿成功", "上一桿普通", "上一桿失敗"][p]]).append(res)
    cols = [seq_col("該洞第一桿", first)] + [seq_col(k, a) for k, a in groups.items() if a]
    table = {"head": ["情況", "桿數"] + [r + " %" for r in SEQ_ROWS], "rows": [[c["label"], c["n"]] + c["p"] for c in cols]}
    return {"id": "seq-chain", "title": "上一桿的結果會不會影響這一桿", "note": SEQ_NOTE + " 每一欄是「這一桿」的結果分布。", "kind": "heat",
            "data": {"cols": cols, "rows": SEQ_ROWS, "xlabel": ""}, "table": table}

def section_seq_cost(con):
    fails = {}
    for rid, seq, no, club, res in classified_shots(con): fails[(rid, seq)] = fails.get((rid, seq), 0) + (res == 2)
    over = {(r, q): o for r, q, o in con.execute("SELECT round_id, seq, over_par FROM holes")}
    groups = {"沒有失敗": [], "失敗 1 次": [], "失敗 2 次以上": []}
    for key, k in fails.items(): groups[list(groups)[min(k, 2)]].append(over[key])
    cols = [score_col(name, a) for name, a in groups.items() if a]
    return {"id": "seq-cost", "title": "一洞裡全揮桿失敗幾次與該洞成績", "note": SEQ_NOTE, "kind": "heat",
            "data": {"cols": cols, "rows": SCORE_ROWS, "xlabel": ""}, "table": score_table("該洞全揮桿失敗次數", cols)}

CHIP_MAX_YD = 60   # 「切球」：離旗桿 60 碼內、不在果嶺也不在沙坑的擊球

def wilson(x, n, z=1.96):
    """比例的 95% 信賴區間（Wilson），回傳百分比 (下限, 上限)。"""
    p = x / n; den = 1 + z * z / n; ctr = (p + z * z / (2 * n)) / den
    hw = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / den
    return round(100 * (ctr - hw)), round(100 * (ctr + hw))

def section_chip_once(con):
    import math
    recent = {r[0] for r in con.execute("SELECT round_id FROM rounds ORDER BY date DESC LIMIT ?", (RECENT_N,))}
    rows = con.execute("""SELECT s.round_id, s.seq, s.shot_no, s.end_lie, h.strokes, COALESCE(h.penalties, 0),
            (SELECT MAX(shot_no) FROM shots x WHERE x.round_id = s.round_id AND x.seq = s.seq AND x.shot_type != 'PUTT')
        FROM shots s JOIN holes h USING(round_id, seq) JOIN rounds r USING(round_id)
        WHERE r.category != 'short_course' AND s.shot_type != 'PUTT' AND s.start_lie NOT IN ('Green', 'Bunker')
          AND s.to_pin_start_m * 1.09361 <= ? ORDER BY s.round_id, s.seq, s.shot_no""", (CHIP_MAX_YD,)).fetchall()
    holes = {}
    for rid, seq, no, end_lie, strokes, pen, last in rows: holes.setdefault((rid, seq), []).append((no, end_lie, strokes - no - pen, last))
    res = {False: [], True: []}
    for (rid, seq), chips in holes.items():
        no, end_lie, after, last = chips[0]
        # 0 = 一次上果嶺：這一洞只切一次，而且落點在果嶺；1 = 手錶記到第二次切球；2 = 其他（落點不在果嶺）
        res[rid in recent].append(1 if len(chips) > 1 else 0 if (no == last and end_lie == "Green") else 2)
    cols = []
    for key, label in ((False, "過去"), (True, f"最近 {RECENT_N} 場")):
        a = res[key]; x = a.count(0); lo, hi = wilson(x, len(a))
        cols.append({"label": label, "n": len(a), "x": x, "top": f"95% 信賴區間 {lo}–{hi}%", "sub": f"{len(a)} 洞",
                     "p": [round(100 * a.count(k) / len(a), 1) for k in (0, 1, 2)]})
    (n1, x1), (n2, x2) = [(c["n"], c["x"]) for c in cols]; pp = (x1 + x2) / (n1 + n2)
    z = (x2 / n2 - x1 / n1) / math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2)); pv = math.erfc(abs(z) / math.sqrt(2))
    labels = ["一次上果嶺", "切了兩次以上", "其他"]
    note = f"對象：每一洞第一次在離旗桿 {CHIP_MAX_YD} 碼內、不在果嶺也不在沙坑的擊球，不分球桿。不含中油短球場。" \
           "「一次上果嶺」＝這一洞只記錄到這一次切球，而且落點在果嶺。「其他」是落點不在果嶺。" \
           "限制：手錶沒偵測到的第二次切球看不出來，所以這個比例會偏高。" \
           f"兩個時期「一次上果嶺」比例的差異：雙比例 z 檢定 z = {z:.2f}，p = {pv:.2f}（p 小於 0.05 才算統計上顯著）。過去不包含最近 {RECENT_N} 場。"
    table = {"head": ["時期", "洞數"] + labels + ["一次上果嶺的 95% 信賴區間"],
             "rows": [[c["label"], c["n"]] + [f"{v}%" for v in c["p"]] + [c["top"].replace("95% 信賴區間 ", "")] for c in cols]}
    for c in cols: c["v"] = c["p"][0]; c["lo"], c["hi"] = wilson(c["x"], c["n"])
    verdict = f"{cols[0]['p'][0]:.0f}% → {cols[1]['p'][0]:.0f}%" + ("（差異達統計顯著）" if pv < 0.05 else f"（差異未達統計顯著，p = {pv:.2f}）")
    return {"id": "chip-once", "title": "切球一次上果嶺的比例", "note": note, "kind": "rate",
            "data": {"cols": cols, "verdict": verdict}, "table": table}

CHIP_SWITCH = "2026-08-28"   # 使用者從這一場起把切球從 54 度換成 A 桿（54 度之後只在沙坑用）
CHIP_OK = 2                  # 切球成功：切完再 2 桿內進洞

def section_chip_switch(con):
    rows = con.execute("""SELECT s.club, r.date, s.start_lie, s.to_pin_start_m * 1.09361, h.strokes - s.shot_no
        FROM shots s JOIN holes h USING(round_id, seq) JOIN rounds r USING(round_id)
        WHERE s.shot_type != 'PUTT' AND s.to_pin_start_m IS NOT NULL AND s.club IN ('54度', 'A杆')""").fetchall()
    eras = [("54 度（換桿前）", lambda c, d: c == "54度" and d < CHIP_SWITCH), ("A 桿（換桿後）", lambda c, d: c == "A杆" and d >= CHIP_SWITCH)]
    buckets = [(f"{hi} 內" if lo == 0 else f"{lo}–{hi}", (lambda lo, hi: lambda lie, yd: lie != "Bunker" and lo <= yd < hi)(lo, hi))
               for lo, hi in [(0, 30), (30, 40), (40, 60), (60, 100)]]
    cols = []
    for label, inb in buckets:
        cells = [[max(rem, 0) for c, d, lie, yd, rem in rows if era(c, d) and inb(lie, yd)] for _, era in eras]
        p = [round(100 * sum(1 for v in a if v <= CHIP_OK) / len(a), 1) if a else 0 for a in cells]
        diff = p[1] - p[0]
        cols.append({"label": label, "n": sum(len(a) for a in cells), "ns": [len(a) for a in cells], "p": p,
                     "mean": [round(statistics.mean(a), 2) if a else None for a in cells],
                     "top": (f"A 桿 {'+' if diff >= 0 else ''}{diff:.0f} 個百分點" if all(cells) else ""), "sub": " / ".join(f"{len(a)} 桿" for a in cells)})
    note = f"切球成功的定義：切完再 {CHIP_OK} 桿內進洞。換桿日是 {CHIP_SWITCH}：上面一列是那天之前的 54 度，下面一列是那天之後的 A 桿。" \
           "不含從沙坑打的球。每欄下方是兩列各自的桿數。兩個時期相隔數月，整體球技的進步也會反映在差距裡。"
    table = {"head": ["離旗桿（碼）", "54 度桿數", "A 桿桿數", "54 度成功率 %", "A 桿成功率 %", "54 度平均再幾桿", "A 桿平均再幾桿"],
             "rows": [[c["label"]] + c["ns"] + c["p"] + c["mean"] for c in cols]}
    return {"id": "chip-switch", "title": "切球成功率：54 度換成 A 桿前後", "note": note, "kind": "heat",
            "data": {"cols": cols, "rows": [e[0] for e in eras], "xlabel": "切球前離旗桿（碼）"}, "table": table}

MISHIT_RATIO = 0.7   # 打壞：距離低於該桿中位數的 70%

def full_swing_shots(con):
    """全揮桿球桿的擊球（排除切球、救球、推桿），附上是否打壞。"""
    roles = club_roles()
    rows = con.execute("""SELECT r.round_id, r.date, r.course, s.club, s.distance_yd FROM shots s JOIN rounds r USING(round_id)
        WHERE s.shot_type NOT IN ('CHIP','RECOVERY','PUTT') AND s.distance_yd IS NOT NULL ORDER BY r.date""").fetchall()
    rows = [r for r in rows if roles.get(r[3]) == "full"]
    by = {}
    for r in rows: by.setdefault(r[3], []).append(r[4])
    med = {c: statistics.median(a) for c, a in by.items()}
    return [r + (r[4] < MISHIT_RATIO * med[r[3]],) for r in rows]

def section_mishit_trend(con):
    by = {}
    for rid, date, course, club, yd, bad in full_swing_shots(con): by.setdefault((date, course), []).append(bad)
    rows = [(d, c, 100 * sum(a) / len(a), len(a)) for (d, c), a in sorted(by.items()) if len(a) >= 10]
    data = []
    for i, (date, course, v, n) in enumerate(rows):
        last = [r[2] for r in rows[max(0, i - 4):i + 1]]
        data.append({"date": date, "course": course, "v": round(v, 1), "measured": True, "extra": f"{n} 桿",
                     "avg": round(statistics.mean(last), 1) if len(last) >= 3 else None})
    note = f"打壞的定義：全揮桿球桿的擊球距離低於該桿整體中位數的 {int(MISHIT_RATIO * 100)}%。每場至少 10 桿全揮桿才列入。折線是最近 5 場的移動平均。"
    table = {"head": ["日期", "球場", "全揮桿桿數", "打壞率 %", "近 5 場平均"], "rows": [[d["date"], d["course"], d["extra"], d["v"], d["avg"] or ""] for d in data]}
    return {"id": "mishit-trend", "title": "每場的打壞率", "note": note, "kind": "trend", "data": data, "table": table,
            "opts": {"unit": "%", "step": 5, "plain": True}}

def section_mishit_club(con):
    groups = {DRIVER: "一號木桿", FAIRWAY: "鐵木桿"}; rows_lbl = ["一號木桿", "鐵木桿", "7 鐵到 P 桿"]
    quarter = lambda d: f"{d[:4]} Q{(int(d[5:7]) - 1) // 3 + 1}"
    by = {}
    for rid, date, course, club, yd, bad in full_swing_shots(con):
        by.setdefault(quarter(date), {}).setdefault(groups.get(club, "7 鐵到 P 桿"), []).append(bad)
    cols = []
    for q in sorted(by):
        allb = [b for a in by[q].values() for b in a]
        cols.append({"label": q, "n": len(allb), "top": f"整體 {100 * sum(allb) / len(allb):.0f}%", "sub": f"{len(allb)} 桿",
                     "p": [round(100 * sum(by[q][g]) / len(by[q][g]), 1) if by[q].get(g) else 0 for g in rows_lbl],
                     "ns": [len(by[q].get(g, [])) for g in rows_lbl]})
    note = f"每格是該季該球桿的打壞率（距離低於該桿中位數的 {int(MISHIT_RATIO * 100)}%）。樣本數見數字表；一格少於 20 桿時不要下結論。"
    table = {"head": ["季", "桿數"] + [f"{g} 打壞率（桿數）" for g in rows_lbl],
             "rows": [[c["label"], c["n"]] + [f"{v}%（{n}）" for v, n in zip(c["p"], c["ns"])] for c in cols]}
    return {"id": "mishit-club", "title": "各球桿的打壞率（按季）", "note": note, "kind": "heat",
            "data": {"cols": cols, "rows": rows_lbl, "xlabel": ""}, "table": table}

SCORE_ROWS = ["Par 以內", "Bogey", "Double", "Triple 以上（爆桿）"]

def score_col(label, a):
    cnt = [sum(1 for v in a if v <= 0), a.count(1), a.count(2), sum(1 for v in a if v >= 3)]; mean = statistics.mean(a)
    return {"label": label, "n": len(a), "mean": round(mean, 2), "top": f"平均 +{mean:.2f}", "sub": f"{len(a)} 洞",
            "p": [round(100 * c / len(a), 1) for c in cnt]}

def score_table(first, cols):
    return {"head": [first, "洞數"] + SCORE_ROWS + ["平均超出標準桿"],
            "rows": [[c["label"], c["n"]] + [f"{v}%" for v in c["p"]] + [c["mean"]] for c in cols]}

def section_score_par(con):
    rows = con.execute("""SELECT MIN(h.par, 5), h.over_par FROM holes h JOIN rounds r USING(round_id) WHERE r.category != 'short_course'""").fetchall()
    cols = [score_col(name, [v for q, v in rows if q == par]) for par, name in ((3, "三桿洞"), (4, "四桿洞"), (5, "五桿洞"))]
    return {"id": "score-par-dist", "title": "各洞型的成績分布", "note": "不含中油短球場；六桿洞併入五桿洞。每一欄的機率加總為 100%。", "kind": "heat",
            "data": {"cols": cols, "rows": SCORE_ROWS, "xlabel": ""}, "table": score_table("洞型", cols)}

def make_length_section(par, name, buckets):
    def section(con):
        rows = con.execute("""SELECT h.length_yd, h.over_par FROM holes h JOIN rounds r USING(round_id)
            WHERE r.category != 'short_course' AND h.par = ? AND h.length_yd IS NOT NULL""", (par,)).fetchall()
        cols = []
        for lo, hi in buckets:
            a = [v for yd, v in rows if lo <= yd < hi]
            if a: cols.append(score_col(f"{hi} 以下" if lo == 0 else f"{lo} 以上" if hi == 999 else f"{lo}–{hi}", a))
        note = "洞長是開球點到旗桿的直線距離（由手錶紀錄推算），沒有開球紀錄的洞不列入。不含中油短球場。"
        return {"id": f"length-par{par}", "title": f"{name}：洞長與成績", "note": note, "kind": "heat",
                "data": {"cols": cols, "rows": SCORE_ROWS, "xlabel": "洞長（碼）"}, "table": score_table("洞長（碼）", cols)}
    return section

def section_blowup(con):
    rows = con.execute("""SELECT h.over_par >= 3, h.strokes, COALESCE(h.putts, 0), COALESCE(h.penalties, 0), COALESCE(h.unrecorded, 0),
        (SELECT COUNT(*) FROM shots s WHERE s.round_id = h.round_id AND s.seq = h.seq AND s.shot_type = 'TEE'),
        (SELECT COUNT(*) FROM shots s WHERE s.round_id = h.round_id AND s.seq = h.seq AND s.shot_type IN ('APPROACH','LAYUP','UNKNOWN')),
        (SELECT COUNT(*) FROM shots s WHERE s.round_id = h.round_id AND s.seq = h.seq AND s.shot_type = 'CHIP'),
        (SELECT COUNT(*) FROM shots s WHERE s.round_id = h.round_id AND s.seq = h.seq AND s.shot_type = 'RECOVERY')
        FROM holes h JOIN rounds r USING(round_id) WHERE r.category != 'short_course' AND h.putts IS NOT NULL""").fetchall()
    labels = ["開球", "長桿", "切球", "救球", "罰桿", "推桿", "手錶漏記的擊球"]
    def col(name, sel):
        a = [r for r in rows if bool(r[0]) == sel]
        vals = [statistics.mean(r[i] for r in a) for i in (5, 6, 7, 8, 3, 2, 4)]
        return {"label": name, "n": len(a), "top": f"平均 {statistics.mean(r[1] for r in a):.1f} 桿", "sub": f"{len(a)} 洞", "p": [round(v, 2) for v in vals]}
    cols = [col("一般的洞（+2 以內）", False), col("爆桿的洞（+3 以上）", True)]
    cols.append({"label": "爆桿洞多打的桿數", "n": 1, "top": "", "sub": "", "p": [max(round(b - a, 2), 0) for a, b in zip(cols[0]["p"], cols[1]["p"])]})
    note = "每格是平均每洞打了幾桿。長桿包含進攻果嶺與過渡的擊球。推桿多數是估算值（上限 3），超出的部分會算在「手錶漏記的擊球」。不含中油短球場。"
    table = {"head": ["類別"] + [c["label"] for c in cols], "rows": [[l] + [c["p"][i] for c in cols] for i, l in enumerate(labels)]}
    return {"id": "blowup", "title": "爆桿的洞多打在哪裡", "note": note, "kind": "heat",
            "data": {"cols": cols, "rows": labels, "xlabel": "", "unit": ""}, "table": table}

# ---------- 擊球座標與球場輪廓（有 data/raw/gps/<計分卡編號>.txt 的場次才有） ----------
GEO = {}     # 球場代碼 → 每洞的向量輪廓（data/course_geo/*.json），整份報表共用一份

def _inside(pt, poly):
    x, y = pt; ins = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: ins = not ins
    return ins

def fairway_offset(s, e, meters, fairways):
    """沿著垂直於擊球方向的線掃過落點，找最近的一段球道：回傳 (球道寬度碼, 落點離球道中線幾碼；正值＝偏右)。"""
    L = ((e[0] - s[0]) ** 2 + (e[1] - s[1]) ** 2) ** 0.5
    if L < 5 or not fairways: return None
    ux, uy = (e[0] - s[0]) / L, (e[1] - s[1]) / L; nx, ny = uy, -ux; ypp = meters / L * 1.09361     # n 指向行進方向的左邊
    hit = [any(_inside((e[0] + nx * t, e[1] + ny * t), p) for p in fairways) for t in range(-120, 121)]
    best = None; t = 0
    while t < len(hit):
        if hit[t]:
            u = t
            while u + 1 < len(hit) and hit[u + 1]: u += 1
            lo, hi = t - 120, u - 120; d = 0 if lo <= 0 <= hi else min(abs(lo), abs(hi))
            if hi - lo >= 6 and (best is None or d < best[0]): best = (d, lo, hi)
            t = u
        t += 1
    if not best: return None
    _, lo, hi = best
    return round((hi - lo) * ypp), round((lo + hi) / 2 * ypp)

_LAYOUT_ZH = {"East": "東", "West": "西", "South": "南", "North": "北", "Center": "中", "Mountain": "青山", "Lake": "綠水", "Left": "左", "Right": "右"}
_LAYOUT_OVERRIDE = None

def layout_labels(layout):
    """Garmin 的分區名稱 → 這張計分卡各九洞的區名（一張 18 洞的卡如果是 C/A 這種組合就回傳兩個）。可用 data/layout_names.csv（layout,label）覆寫，label 用 / 分隔前後九。"""
    global _LAYOUT_OVERRIDE
    if _LAYOUT_OVERRIDE is None:
        p = os.path.join(ROOT, "data/layout_names.csv")
        _LAYOUT_OVERRIDE = {r["layout"]: r["label"] for r in csv.DictReader(open(p, encoding="utf-8-sig"))} if os.path.exists(p) else {}
    if layout in _LAYOUT_OVERRIDE: return [x.strip() for x in _LAYOUT_OVERRIDE[layout].split("/") if x.strip()]
    if "~" not in layout: return []
    suf = layout.split("~", 1)[1].split("+")[0].strip()                 # 「South Left+… ~ North Right」只取前面
    out = []
    for part in suf.split("/"):
        w = part.split()
        if len(w) > 1 and w[-1] in ("Left", "Right"): w = w[:-1]         # 尾巴的 Left/Right 是果嶺或發球台的左右，不是區名
        out.append("".join(_LAYOUT_ZH.get(x, x) for x in w) if all(x in _LAYOUT_ZH for x in w) else " ".join(w))
    return out

def round_parts(fname):
    """這一場每一洞屬於哪一區（組合球場才有意義）。回傳 (每洞區名的 list, 組合名稱如「青山＋綠水」)；不是組合就回傳 (None, "")。"""
    rows = list(csv.DictReader(open(os.path.join(ROOT, "data/rounds", fname), encoding="utf-8-sig")))
    seqs = {}
    for r in rows: seqs.setdefault(r["garmin_scorecard_id"] or "site:" + r["layout"], {})[int(r["seq"])] = r["layout"]   # 只有網站紀錄的場次沒有計分卡編號，依分區分組
    lab = {}
    for sid, d in seqs.items():
        L = layout_labels(next(iter(d.values()))); ss = sorted(d)
        for i, q in enumerate(ss): lab[q] = (L[1] if len(L) == 2 and len(ss) > 9 and i >= 9 else L[0]) if L else ""
    per = [lab[q] for q in sorted(lab)]; groups = [x for i, x in enumerate(per) if i == 0 or x != per[i - 1]]
    if len(groups) < 2 or not all(groups): return None, ""
    return per, "＋".join(groups)

def bad_shot(shots, k, med, roles):
    """明細表裡要圈起來的「打壞」：① 切球（A 杆、54 度）之後還有下一桿，例如連切兩次、A 接 54、A 接 P（切了沒上果嶺）；② 全揮桿的距離不到這支桿常態（中位數）的 70%，
    而且也不到擊球前離旗桿距離的 70%（本來就只差一點就到的不算）。下水／OB 已經另外標，不重複圈。"""
    c, d, sr, wc, hz, pin = shots[k]
    if hz: return 1                                                 # 下水／OB 本身就是失誤
    chip = lambda x: roles.get(x) == "chip" or x == SAND_CLUB
    if chip(c):
        return 1 if k + 1 < len(shots) else 0                       # 切了之後還有下一桿（不是推桿）＝沒切上果嶺
    m = med.get(c)
    if not m or d is None or roles.get(c) not in ("full", "recovery") and c != DRIVER: return 0
    room = pin * 1.09361 if pin else None
    return 1 if d < MISHIT_RATIO * m and (room is None or d < MISHIT_RATIO * room) else 0

def round_gps(fname):
    """讀一場的擊球座標；沒有就回傳 None。前九後九是兩張計分卡、兩個球場代碼，洞號會重複，所以每一洞都帶自己的球場代碼和這一場的第幾洞。"""
    rows = list(csv.DictReader(open(os.path.join(ROOT, "data/rounds", fname), encoding="utf-8-sig")))
    seq_of = {(r["garmin_scorecard_id"], int(r["hole"])): int(r["seq"]) for r in rows}
    roles = club_roles(); holes = []; drive = []
    for sid in dict.fromkeys(r["garmin_scorecard_id"] for r in rows):
        p = os.path.join(ROOT, "data/raw/gps", f"{sid}.txt")
        if not os.path.exists(p): continue
        cur = None; cid = None
        for line in open(p, encoding="utf-8"):
            c = line.rstrip("\n").split("|")
            if c[0] == "C":
                cid = c[1]
                if cid not in GEO:
                    gp = os.path.join(ROOT, "data/course_geo", f"{cid}.json")
                    GEO[cid] = json.load(open(gp))["holes"] if os.path.exists(gp) else {}
            elif c[0].startswith("H"):
                n = int(c[0][1:]); seq = seq_of.get((sid, n)); pin = c[2].split(",")
                cur = None
                if seq is None: continue
                hc, hn = cid, n                                                  # 一張 18 洞的卡可能是兩區組合：後九其實是另一個球場代碼的第 1 到 9 洞，以地圖路徑為準
                mp = c[3].split("/") if len(c) > 3 and c[3] else []
                if len(mp) >= 2 and mp[0].startswith("gid") and mp[1].startswith("hole"): hc, hn = mp[0][3:], int(mp[1][4:])
                if hc not in GEO:
                    gp = os.path.join(ROOT, "data/course_geo", f"{hc}.json")
                    GEO[hc] = json.load(open(gp))["holes"] if os.path.exists(gp) else {}
                cur = {"n": seq, "h": hn, "cid": hc, "par": int(c[1]) if c[1] else None, "pin": [int(pin[2]), int(pin[3])] if len(pin) > 3 and pin[2] else None, "shots": []}; holes.append(cur)
            elif c[0] and cur is not None:
                s, e = c[4].split(","), c[5].split(",")
                if not s[2] or not e[2]: continue
                club = c[1]; cat = "d" if club == DRIVER else "c" if roles.get(club) == "chip" else "w" if (club in WOODS or "木" in club) else "i"
                sp, ep = (int(s[2]), int(s[3])), (int(e[2]), int(e[3])); yd = round(int(c[3]) * 1.09361)
                cur["shots"].append([club, cat, sp[0], sp[1], ep[0], ep[1], yd, e[4]])
                if cat == "d" and int(c[0]) == 1:                                   # 只算開球那一桿
                    fo = fairway_offset(sp, ep, int(c[3]), GEO.get(cur["cid"], {}).get(str(cur["h"]), {}).get("fairway", []))
                    drive.append({"hole": seq, "yd": yd, "width": fo[0] if fo else None, "off": fo[1] if fo else None, "fw": e[4] == "Fairway"})
    if holes and (rows[0]["date"], merge_shots.SHORT.get(rows[0]["course"], rows[0]["course"])) in merge_shots.caddie_files():
        M = merge_shots.merge_round(fname, rows); fit = _geofit()               # 這一場網站也有記：每一洞的擊球換成兩個來源合併後的版本
        for cur in holes:
            mg = M.get(cur["n"]); ll = fit.get((cur["cid"], cur["h"]))
            if not mg: continue
            def px(q, ll=ll):
                if not q or not ll: return None
                return (round(ll[2] * (q[1] - ll[1]) + ll[3] * (q[0] - ll[0]) + ll[4]), round(ll[5] * (q[1] - ll[1]) + ll[6] * (q[0] - ll[0]) + ll[7]))
            ms = mg["shots"]; pts = [x["sp"] or px(x["s"]) for x in ms]; pinp = tuple(cur["pin"]) if cur["pin"] else None; new = []
            for k, x in enumerate(ms):
                sp = pts[k]; ep = pts[k + 1] if k + 1 < len(ms) else (pinp or x["ep"])
                if not sp or not ep: continue
                club = x["club"]; cat = "d" if club == DRIVER else "c" if roles.get(club) == "chip" else "w" if (club in WOODS or "木" in club) else "i"
                el = x["elie"] if x["src"] != "site" and not (k + 1 < len(ms) and ms[k + 1]["src"] == "site") else ("Green" if k + 1 == len(ms) else "")
                new.append([club, cat, sp[0], sp[1], ep[0], ep[1], round(x["dist_m"] * 1.09361) if x["dist_m"] is not None else 0, el or "", x["src"], x.get("haz") or ""])
            cur["shots"] = new
    holes.sort(key=lambda h: h["n"]); drive.sort(key=lambda d: d["hole"])
    return {"holes": holes, "drive": drive} if holes else None

_GEOFIT = None
def _geofit():
    global _GEOFIT
    if _GEOFIT is None: _GEOFIT = caddie_geofit()
    return _GEOFIT

def section_courses(con):
    """球場紀錄：每個球場、每一洞把歷次打過的路線疊在一起，下場前或打完檢討用。"""
    courses = {}      # 簡稱 → {name, layouts: {cid: {label, holes: {洞號: {par, plays: [...]}}}}}
    for rid, date, course, short, fname in con.execute("SELECT round_id, date, course, course_short, file FROM rounds ORDER BY date"):
        g = round_gps(fname)
        if not g: continue
        hrow = {seq: (par, st, putts, yd) for seq, par, st, putts, yd in con.execute("SELECT seq, par, strokes, putts, length_yd FROM holes WHERE round_id=?", (rid,))}
        layout = {r["garmin_scorecard_id"]: r["layout"] for r in csv.DictReader(open(os.path.join(ROOT, "data/rounds", fname), encoding="utf-8-sig"))}
        cid_layout = {}
        for sid, lay in layout.items():
            p = os.path.join(ROOT, "data/raw/gps", f"{sid}.txt")
            if os.path.exists(p): cid_layout[open(p, encoding="utf-8").readline().split("|")[1]] = "／".join(layout_labels(lay))
        per = round_parts(fname)[0]
        pins = []
        for sid in layout:
            gp = os.path.join(ROOT, "data/raw/gps", f"{sid}.txt")
            if os.path.exists(gp):
                for line in open(gp, encoding="utf-8"):
                    if line.startswith("H"):
                        q = line.split("|")[2].split(",")
                        if q[0]: pins.append((int(q[0]) * 180 / 2 ** 31, int(q[1]) * 180 / 2 ** 31))      # Garmin 的 semicircle → 度
        c = courses.setdefault(short, {"name": course, "pins": [], "short": short, "layouts": {}, "last": date, "rounds": set()})
        c["last"] = date; c["rounds"].add(rid); c["pins"] += pins
        for H in g["holes"]:
            par, st, putts, yd = hrow.get(H["n"], (H["par"], None, None, None))
            L = c["layouts"].setdefault(H["cid"], {"cid": H["cid"], "label": (per[H["n"] - 1] if per and H["n"] <= len(per) else "") or cid_layout.get(H["cid"], ""), "holes": {}})
            hh = L["holes"].setdefault(H["h"], {"h": H["h"], "par": par, "plays": [], "yds": []})
            if yd: hh["yds"].append(yd)
            hh["plays"].append({"date": date, "st": st, "putts": putts, "pin": H["pin"], "shots": H["shots"]})
    out = []
    for c in courses.values():
        for L in c["layouts"].values():
            for hh in L["holes"].values():
                y = sorted(hh.pop("yds")); hh["yd"] = round(y[len(y) // 2]) if y else None      # 洞長：歷次開球點到旗桿的中位數
    for c in sorted(courses.values(), key=lambda c: c["last"], reverse=True):
        lays = [{"cid": L["cid"], "label": L["label"], "holes": [L["holes"][k] for k in sorted(L["holes"])]} for L in c["layouts"].values()]
        ll = [round(sum(p[0] for p in c["pins"]) / len(c["pins"]), 4), round(sum(p[1] for p in c["pins"]) / len(c["pins"]), 4)] if c["pins"] else None
        tw = bool(ll) and 21.5 < ll[0] < 25.6 and 119 < ll[1] < 122.5
        out.append({"short": c["short"], "name": c["name"], "n": len(c["rounds"]), "last": c["last"], "layouts": lays, "ll": ll, "region": ("北台灣" if ll[0] > 24.5 else "中台灣" if ll[0] > 23.6 else "南台灣") if tw else ("越南河內" if 20 < ll[0] < 22 and 105 < ll[1] < 106.6 else "越南" if 8 < ll[0] < 23.5 and 102 < ll[1] < 110 else "國外") if ll else "其他"})
    note = "每一洞把歷次的擊球路線疊在同一張圖上，最近一次最清楚，越早的越淡。紅＝一號木、橘＝鐵木桿、藍＝鐵桿、細白線＝切球桿。球場輪廓是依 Garmin 地圖重新描的示意圖。"
    return {"id": "courses", "title": "打過的球場", "note": note, "kind": "courses", "data": out, "table": {"head": [], "rows": []}}

# ---- 訓練計劃（第四個頁籤）：目標與計劃的文字在這裡改，數字由資料算出 ----
# ---------------------------------------------------------------- AI 桿弟：攻略引擎的資料（模擬器用）
CADDIE_BAG = CFG.get("bag", [])     # 一號木只在發球台、54 度只在沙坑（JS 端判斷）
CADDIE_EDGES = [0, 10, 20, 30, 40, 60, 80, 100, 125, 150, 175, 200, 250, 300, 350, 400, 450, 700]   # 離旗桿（碼）的分組
CADDIE_LIE = {"TeeBox": "T", "Fairway": "F", "Rough": "R", "Unknown": "R", "Bunker": "B", "Waste": "B", "Green": "G"}

def _isotonic(pts):
    """pts = [(x, y, w)] 依 x 排序；回傳 y 不遞減的加權擬合（pool adjacent violators）。"""
    blocks = [[y, w, 1] for _, y, w in pts]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0] + 1e-9:
            a, b = blocks[i], blocks[i + 1]; w = a[1] + b[1]
            blocks[i:i + 2] = [[(a[0] * a[1] + b[0] * b[1]) / w, w, a[2] + b[2]]]; i = max(i - 1, 0)
        else: i += 1
    out = []
    for y, _, k in blocks: out += [y] * k
    return out

def caddie_model(con, before=None):
    """攻略引擎用的兩張表。before＝只用這一天之前的場次（回放測試用）。
    E[球位] = [(離旗桿碼, 平均還要幾桿)]：每一筆擊球「擊球前」的位置 → 那一洞從這一桿算起還用了幾桿（總桿 − 第幾筆 + 1）。
    clubs[球桿] = 每一筆擊球的 [擊球前離旗桿, 前進碼數, 擊球後離旗桿, 擊球後球位, 是否該洞最後一筆, 這一桿之後實際還用了幾桿, 擊球前球位]。"""
    q = """SELECT s.club, s.start_lie, s.end_lie, s.to_pin_start_m*1.09361, s.to_pin_end_m*1.09361, s.shot_no, h.strokes, h.shots_recorded
           FROM shots s JOIN holes h ON h.round_id=s.round_id AND h.seq=s.seq JOIN rounds r ON r.round_id=s.round_id
           WHERE r.category!='short_course' AND s.to_pin_start_m IS NOT NULL AND h.strokes IS NOT NULL"""
    rows = con.execute(q + (" AND r.date < ?" if before else ""), (before,) if before else ()).fetchall()
    cell = {}
    for club, sl, el, ds, de, no, st, rec in rows:
        g = CADDIE_LIE.get(sl, "R")
        for i in range(len(CADDIE_EDGES) - 1):
            if CADDIE_EDGES[i] <= ds < CADDIE_EDGES[i + 1]: cell.setdefault((g, i), []).append((ds, st - no + 1))
    E = {}
    for g in "FRT":
        grp = [v for (gg, i), v in sorted(cell.items(), key=lambda kv: kv[0][1]) if gg == g and len(v) >= 5]
        pts = [(sum(d for d, _ in v) / len(v), sum(r for _, r in v) / len(v), len(v)) for v in grp]
        ses = [(sum((r - m) ** 2 for _, r in v) / (len(v) - 1)) ** 0.5 / len(v) ** 0.5 for v, (_, m, _) in zip(grp, pts)]      # 每一組平均的標準誤
        fit = _isotonic(pts); E[g] = [[round(x, 1), round(y, 2), n, round(se, 2)] for (x, _, n), y, se in zip(pts, fit, ses)]
    interp = lambda tab, d: tab[0][1] if d <= tab[0][0] else tab[-1][1] if d >= tab[-1][0] else next(a[1] + (b[1] - a[1]) * (d - a[0]) / (b[0] - a[0]) for a, b in zip(tab, tab[1:]) if a[0] <= d <= b[0])
    bun = [r - interp(E["R"], d) for (g, _), v in cell.items() if g == "B" for d, r in v]            # 沙坑樣本少：用「比長草多幾桿」一個數字
    bunker_extra = round(sum(bun) / len(bun), 2) if len(bun) >= 10 else 0.2
    putts = con.execute("SELECT AVG(putts) FROM holes h JOIN rounds r USING(round_id) WHERE r.category!='short_course' AND putts IS NOT NULL" + (" AND r.date < ?" if before else ""), (before,) if before else ()).fetchone()[0]
    clubs = {}
    for club, sl, el, ds, de, no, st, rec in rows:
        if club not in CADDIE_BAG: continue
        last = 1 if no == rec else 0
        if not last and not de: continue                                                              # 中間的擊球卻沒有落點，不能用
        end = 0 if last else de
        clubs.setdefault(club, []).append([round(ds), round(ds - end), round(end), "G" if last else CADDIE_LIE.get(el, "R"), last, st - no, CADDIE_LIE.get(sl, "R")])
    out = {}
    for club, v in clubs.items():
        adv = sorted(x[1] for x in v); med = adv[len(adv) // 2]
        full = sorted(x[1] for x in v if x[0] >= med * 1.2) or adv                                     # 全揮桿：離旗桿夠遠、不必收力的那些球
        out[club] = {"reach": full[int(len(full) * 0.75)], "med": full[len(full) // 2], "n": len(v), "s": v}
    return {"E": E, "bunker": bunker_extra, "green": round(putts, 2), "clubs": out, "bag": [c for c in CADDIE_BAG if c in out], "shots": len(rows)}

def caddie_geofit():
    """每一洞「經緯度 → 球洞圖像素」的仿射轉換，用 data/raw/gps 裡每一桿的起點、落點和旗桿（同時有經緯度和像素座標）做最小平方法。
    回傳 {(球場代碼, 洞號): [lat0, lon0, a, b, c, d, e, f]}：x = a·Δlon + b·Δlat + c，y = d·Δlon + e·Δlat + f（Δ 是減掉 lat0、lon0 之後的度數）。"""
    import glob
    import numpy as np
    pts = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "data/raw/gps", "*.txt"))):
        cid = None; key = None
        for line in open(path, encoding="utf-8"):
            c = line.rstrip("\n").split("|")
            if c[0] == "C": cid = c[1]
            elif c[0].startswith("H"):
                hc, hn = cid, int(c[0][1:]); mp = c[3].split("/") if len(c) > 3 and c[3] else []
                if len(mp) >= 2 and mp[0].startswith("gid") and mp[1].startswith("hole"): hc, hn = mp[0][3:], int(mp[1][4:])
                key = (hc, hn); q = c[2].split(",")
                if len(q) > 3 and q[0] and q[2]: pts.setdefault(key, []).append((int(q[0]), int(q[1]), int(q[2]), int(q[3])))
            elif c[0] and key:
                for part in (c[4], c[5]):
                    q = part.split(",")
                    if len(q) > 3 and q[0] and q[2]: pts.setdefault(key, []).append((int(q[0]), int(q[1]), int(q[2]), int(q[3])))
    out = {}; K = 180 / 2 ** 31
    for key, v in pts.items():
        v = list(set(v))
        if len(v) < 2: continue
        lat = np.array([p[0] * K for p in v]); lon = np.array([p[1] * K for p in v]); lat0, lon0 = float(lat.mean()), float(lon.mean())
        X = np.array([p[2] for p in v], float); Y = np.array([p[3] for p in v], float)
        if len(v) >= 4:
            A = np.column_stack([lon - lon0, lat - lat0, np.ones(len(v))])
            cx, *_ = np.linalg.lstsq(A, X, rcond=None); cy, *_ = np.linalg.lstsq(A, Y, rcond=None)
            err = float(np.max(np.hypot(A @ cx - X, A @ cy - Y)))
            if err <= 12:
                out[key] = [round(lat0, 7), round(lon0, 7)] + [round(float(x), 3) for x in list(cx) + list(cy)]; continue
        # 點太少或共線：改用相似轉換（只有旋轉、縮放、平移，地圖本來就是正射的），兩個點就夠
        me, mn = 111320.0 * np.cos(np.radians(lat0)), 110540.0
        w = me * (lon - lon0) + 1j * mn * (lat - lat0); z = X - 1j * Y
        if np.ptp(np.abs(w - w.mean())) == 0 and np.abs(w - w.mean()).max() < 20: continue   # 點都擠在一起，算不出方向
        M = np.column_stack([w, np.ones(len(v))]); (a, b), *_ = np.linalg.lstsq(M, z, rcond=None)
        err = float(np.max(np.abs(M @ np.array([a, b]) - z)))
        if err > 15 or not (0.5 < abs(a) < 5): continue
        cx = [a.real * me, -a.imag * mn, b.real]; cy = [-a.imag * me, -a.real * mn, -b.imag]
        out[key] = [round(lat0, 7), round(lon0, 7)] + [round(float(x), 3) for x in cx + cy]
    return out

def caddie_path(tee, pin, k, fairways):
    """球道的中線：發球台 → 球道每 40 碼的中心點 → 旗桿（和頁面上 JS 的 pathOf 同一個算法）。回傳折線的點。"""
    L = ((pin[0] - tee[0]) ** 2 + (pin[1] - tee[1]) ** 2) ** 0.5 or 1
    ux, uy = (pin[0] - tee[0]) / L, (pin[1] - tee[1]) / L; bins = {}
    for poly in fairways or []:
        xs = [q[0] for q in poly]; ys = [q[1] for q in poly]
        for x in range(min(xs), max(xs) + 1, 5):
            for y in range(min(ys), max(ys) + 1, 5):
                dx, dy = x - tee[0], y - tee[1]; t = (dx * ux + dy * uy) * k; lat = (dx * uy - dy * ux) * k
                if t < 70 or t > L * k - 40 or abs(lat) > 70 or not _inside((x, y), poly): continue
                bins.setdefault(int(t // 40), []).append((lat, x, y))
    mid = []; prev = 0.0                       # 同一段有兩條分開的球道（隔壁洞、雙球道）時，從旗桿往回挑連得上的那一條，不取兩條的平均
    for bi in sorted(bins, reverse=True):
        v = sorted(bins[bi]); groups = [[v[0]]]
        for q in v[1:]:
            if q[0] - groups[-1][-1][0] > 12: groups.append([])
            groups[-1].append(q)
        groups = [g for g in groups if len(g) >= 6]
        if not groups: continue
        g = min(groups, key=lambda g: abs(sum(q[0] for q in g) / len(g) - prev)); prev = sum(q[0] for q in g) / len(g)
        mid.append((sum(q[1] for q in g) / len(g), sum(q[2] for q in g) / len(g)))
    mid.reverse()
    pts = [tuple(tee)] + mid + [tuple(pin)]
    if sum(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a, b in zip(pts, pts[1:])) > L * 1.12: pts = [tuple(tee), tuple(pin)]
    return pts

ROUTE_STEP = 5                                # 找瞄準線用的格子大小（像素）
ROUTE_COST = {"out": 6.0, "rough": 2.0, "tree": 4.5, "sand": 2.5, "water": 6.0, "green": 1.1, "fairway": 1.0}      # 每走一碼的代價：球道最便宜，樹和水最貴
_ROUTE_CACHE = None

def _mask(polys, X, Y):
    """格點是否落在任一多邊形裡（numpy 的射線法）。"""
    import numpy as np
    m = np.zeros(X.shape, bool)
    for poly in polys or []:
        if len(poly) < 3: continue
        ins = np.zeros(X.shape, bool)
        for i in range(len(poly)):
            (x1, y1), (x2, y2) = poly[i], poly[i - 1]
            if y1 == y2: continue
            ins ^= ((y1 > Y) != (y2 > Y)) & (X < (x2 - x1) * (Y - y1) / (y2 - y1) + x1)
        m |= ins
    return m

def caddie_route(tee, pin, k, geo, par=4):
    """瞄準線：從發球台到旗桿「代價最低」的路線（球道便宜、長草普通、樹和水很貴），再拉直成幾段折線。
    三桿洞、沒有球道輪廓、或算出來長得不合理時用直線。結果依輸入做快取（data/processed/caddie_route.json）。"""
    import numpy as np, heapq, hashlib
    global _ROUTE_CACHE
    straight = [[round(tee[0]), round(tee[1])], [round(pin[0]), round(pin[1])]]
    if (par or 4) <= 3 or not geo or not geo.get("fairway"): return straight
    cpath = os.path.join(ROOT, "data/processed/caddie_route.json")
    if _ROUTE_CACHE is None:
        try: _ROUTE_CACHE = json.load(open(cpath))
        except Exception: _ROUTE_CACHE = {}
    key = hashlib.md5(json.dumps([tee, list(pin)[:2], k, geo, ROUTE_STEP, ROUTE_COST, 6], sort_keys=True).encode()).hexdigest()
    if key in _ROUTE_CACHE: return _ROUTE_CACHE[key]
    g = ROUTE_STEP
    allp = [q for kk in geo for poly in geo[kk] for q in (poly if poly and isinstance(poly[0], (list, tuple)) else [])] + [tee, list(pin)[:2]]
    x0 = int(min(q[0] for q in allp)) - 2 * g; y0 = int(min(q[1] for q in allp)) - 2 * g
    nx = int((max(q[0] for q in allp) - x0) // g) + 3; ny = int((max(q[1] for q in allp) - y0) // g) + 3
    X, Y = np.meshgrid(x0 + np.arange(nx) * g, y0 + np.arange(ny) * g)
    out = geo.get("outline") or []
    out = [out] if out and not isinstance(out[0][0], (list, tuple)) else out
    C = np.full(X.shape, ROUTE_COST["out"])
    C[_mask(out, X, Y)] = ROUTE_COST["rough"]
    C[_mask(geo.get("tree"), X, Y)] = ROUTE_COST["tree"]
    fw = _mask(geo.get("fairway"), X, Y); er = fw.copy(); C[fw] = ROUTE_COST["fairway"] + 0.3
    for n in range(3):                                                                              # 球道越靠中間越便宜，路線才不會貼著邊走
        e2 = er.copy(); e2[1:] &= er[:-1]; e2[:-1] &= er[1:]; e2[:, 1:] &= er[:, :-1]; e2[:, :-1] &= er[:, 1:]; er = e2
        C[er] = ROUTE_COST["fairway"] + 0.2 - 0.1 * n
    C[_mask([q for q in geo.get("green") or [] if q and min(math.hypot(v[0] - pin[0], v[1] - pin[1]) for v in q) * k < 40], X, Y)] = ROUTE_COST["green"]      # 只認旗桿旁邊的果嶺；輪廓裡其他的「果嶺」常常是發球台或辨識錯的區塊
    C[_mask(geo.get("sand"), X, Y)] = ROUTE_COST["sand"]
    wm = _mask(geo.get("water"), X, Y); C[wm] = ROUTE_COST["water"]
    dt = np.hypot(X - tee[0], Y - tee[1]) * k; dp = np.hypot(X - pin[0], Y - pin[1]) * k
    C[dt < 110] = np.minimum(C[dt < 110], 1.4); C[(dt < 110) & wm] = 3.0                              # 但水例外：繞得開就繞，繞不開（正前方的水）才直接越過                                                       # 開球會飛過發球台前面那一段，那裡是什麼地形不重要
    C[dp < 15] = 1.0
    node = lambda pt: (min(ny - 1, max(0, int(round((pt[1] - y0) / g)))), min(nx - 1, max(0, int(round((pt[0] - x0) / g)))))
    a, b = node(tee), node(pin); INF = 1e18
    D = np.full(X.shape, INF); D[a] = 0; prev = {}; pq = [(0.0, a)]
    Cl = C.tolist(); Dl = D.tolist()
    nb = [(-1, 0, 1), (1, 0, 1), (0, -1, 1), (0, 1, 1), (-1, -1, 1.4142), (-1, 1, 1.4142), (1, -1, 1.4142), (1, 1, 1.4142)]
    while pq:
        d, (i, j) = heapq.heappop(pq)
        if (i, j) == b: break
        if d > Dl[i][j]: continue
        ci = Cl[i][j]
        for di, dj, w in nb:
            u, v = i + di, j + dj
            if 0 <= u < ny and 0 <= v < nx:
                nd = d + w * (ci + Cl[u][v]) / 2
                if nd < Dl[u][v]: Dl[u][v] = nd; prev[(u, v)] = (i, j); heapq.heappush(pq, (nd, (u, v)))
    cells = [b]
    while cells[-1] != a and cells[-1] in prev: cells.append(prev[cells[-1]])
    cells.reverse()
    pts = [tuple(tee)] + [(x0 + j * g, y0 + i * g) for i, j in cells[1:-1]] + [tuple(list(pin)[:2])]
    def seg(p, q):                                                                                   # 直線段的代價（每格取樣）
        L = math.hypot(q[0] - p[0], q[1] - p[1]); n = max(1, int(L / (g / 2))); tot = 0
        for t in range(n):
            f = (t + 0.5) / n; i, j = node((p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f)); tot += Cl[i][j]
        return tot / n * L / g
    cum = [0.0]
    for p, q in zip(pts, pts[1:]): cum.append(cum[-1] + seg(p, q))
    res = [pts[0]]; i = 0
    while i < len(pts) - 1:                                                                          # 拉直：能用一條直線取代、代價又沒有變貴的就取代
        j = len(pts) - 1
        while j > i + 1 and seg(pts[i], pts[j]) > (cum[j] - cum[i]) * 1.012 + 0.3: j -= 1
        res.append(pts[j]); i = j
    L0 = math.hypot(pin[0] - tee[0], pin[1] - tee[1]) or 1
    Lp = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(res, res[1:]))
    r = straight if Lp > L0 * 1.45 else [[round(p[0]), round(p[1])] for p in res]
    _ROUTE_CACHE[key] = r
    try:
        os.makedirs(os.path.dirname(cpath), exist_ok=True); json.dump(_ROUTE_CACHE, open(cpath, "w"))
    except Exception: pass
    return r

COVER_IN, COVER_OUT = 0.3, 0.9                # 桿弟圖落點範圍：內圈、外圈各涵蓋多少比例的球


def _path_offset(pt, pts, k):
    """點到折線的距離（碼）；正值＝在行進方向的右邊。"""
    best = None
    for a, b in zip(pts, pts[1:]):
        vx, vy = b[0] - a[0], b[1] - a[1]; n2 = vx * vx + vy * vy or 1
        t = max(0, min(1, ((pt[0] - a[0]) * vx + (pt[1] - a[1]) * vy) / n2)); dx, dy = pt[0] - a[0] - t * vx, pt[1] - a[1] - t * vy
        d = (dx * dx + dy * dy) ** 0.5 * k
        if best is None or d < abs(best): best = d if vx * dy - vy * dx >= 0 else -d
    return best

PREVIEW_PIN_TOP = 58      # Garmin 的球洞圖：旗桿離輪廓上緣約 58 碼（打過的 380 洞：中位數 58，一半的洞在 54 到 63 之間），用來估沒打過的球場每像素幾碼
PREVIEW_TEE_BOTTOM = 65   # 發球台離輪廓下緣約 65 碼（一半的洞在 52 到 83 之間）

def preview_courses():
    """還沒打過的球場（data/course_preview/*.json）：只有 Garmin 的球洞輪廓和旗桿座標，沒有擊球紀錄。比例尺和發球台位置是估的。"""
    names = {r["garmin_name"]: r for r in csv.DictReader(open(os.path.join(ROOT, "data/raw/course_names.csv"), encoding="utf-8"))}
    out = []
    for fp in sorted(glob.glob(os.path.join(ROOT, "data/course_preview/*.json"))):
        pv = json.load(open(fp, encoding="utf-8")); cid = pv["cid"]
        gp = os.path.join(ROOT, "data/course_geo", f"{cid}.json")
        if cid not in GEO: GEO[cid] = json.load(open(gp))["holes"] if os.path.exists(gp) else {}
        holes = []
        for H in pv["holes"]:
            if pv.get("frame") == "aerial":                                      # 自己用空拍描的球場：圖框、比例尺、經緯度轉換都已經算好
                holes.append({"h": H["h"], "par": H["par"], "plays": [], "yd": H["yd"], "k": H["k"], "pin": H["pin"][2:4], "tee": H["tee_px"],
                              "pll": H["pin"][:2], "ll": H["ll"], "aerial": 1}); continue
            g = GEO[cid].get(str(H["h"]), {}); o = (g.get("outline") or [[]])[0]
            if not o: continue
            lat, lon, px, py = H["pin"]; y0 = min(q[1] for q in o); y1 = max(q[1] for q in o)
            k = min(1.1, max(0.3, PREVIEW_PIN_TOP / max(20, py - y0)))
            ty = y1 - PREVIEW_TEE_BOTTOM / k; xs = [q[0] for q in o if abs(q[1] - ty) < 25] or [px]
            tee = [round((min(xs) + max(xs)) / 2), round(ty)]
            yd = round(k * ((tee[0] - px) ** 2 + (tee[1] - py) ** 2) ** 0.5)
            if H.get("yd"):                                                      # 有計分卡上的碼數：沿「旗桿→發球台」方向移動發球台，讓洞長等於計分卡
                f = H["yd"] / max(1, yd); tee = [round(px + (tee[0] - px) * f), round(py + (tee[1] - py) * f)]; yd = H["yd"]
            holes.append({"h": H["h"], "par": H["par"], "plays": [], "yd": yd, "k": round(k, 4), "pin": [px, py], "tee": tee, "pll": [lat, lon]})
        if not holes: continue
        nm = names.get(pv["garmin_name"], {})
        same = next((c for c in out if c["garmin"] == pv["garmin_name"]), None)          # 同一個球場的不同分區（第一 Arnold／Palmer）併成一個球場
        if same:
            same["layouts"].append({"cid": cid, "label": pv.get("label", ""), "holes": holes}); continue
        out.append({"short": nm.get("short") or pv["garmin_name"], "name": nm.get("name_zh") or pv["garmin_name"], "n": 0, "last": "", "preview": 1, "garmin": pv["garmin_name"],
                    "layouts": [{"cid": cid, "label": pv.get("label", ""), "holes": holes}],
                    "ll": [round(sum(h["pll"][0] for h in holes) / len(holes), 5), round(sum(h["pll"][1] for h in holes) / len(holes), 5)], "region": ""})
    return out

def section_caddie(con):
    """AI 桿弟的模擬器：在球洞圖上點一個位置，依歷史紀錄建議球桿與距離。"""
    courses = section_courses(con)["data"]; fit = caddie_geofit(); lateral = {}
    for c in courses:
        for L in c["layouts"]:
            keep = []
            for H in L["holes"]:
                if (L["cid"], H["h"]) in fit: H["ll"] = fit[(L["cid"], H["h"])]
                ks, tees, pin = [], [], None
                for p in H["plays"]:
                    if p["pin"]: pin = p["pin"]
                    for i, sh in enumerate(p["shots"]):
                        px = ((sh[4] - sh[2]) ** 2 + (sh[5] - sh[3]) ** 2) ** 0.5
                        if px > 15 and sh[6] > 20: ks.append(sh[6] / px)
                        if i == 0: tees.append((sh[2], sh[3]))
                if not ks or not pin or not tees: continue
                far = [((t[0] - pin[0]) ** 2 + (t[1] - pin[1]) ** 2) ** 0.5 for t in tees]; fm = sorted(far)[len(far) // 2]; kk = sorted(ks)[len(ks) // 2]      # 第一筆紀錄不一定是開球：只取離旗桿距離和中位數差不多的那一群
                tees = [t for t, f in zip(tees, far) if fm - 30 / kk <= f <= fm + 40 / kk]
                ks.sort(); tx = sorted(t[0] for t in tees); ty = sorted(t[1] for t in tees)
                H["k"] = round(ks[len(ks) // 2], 4); H["pin"] = pin; H["tee"] = [tx[len(tx) // 2], ty[len(ty) // 2]]
                o = (GEO.get(L["cid"], {}).get(str(H["h"]), {}).get("outline") or [[]])[0]; ydm = H.get("yd") or 0
                if o:                                                                               # 發球台的檢查：落在球洞輪廓外面、或離旗桿比 Garmin 的洞長短很多（第一筆紀錄不是開球），就改用輪廓估
                    tl = H["k"] * math.hypot(H["tee"][0] - pin[0], H["tee"][1] - pin[1])
                    off = not (min(q[0] for q in o) - 15 <= H["tee"][0] <= max(q[0] for q in o) + 15 and min(q[1] for q in o) - 15 <= H["tee"][1] <= max(q[1] for q in o) + 15)
                    if off or (ydm and tl < 0.72 * ydm) or ((max(q[1] for q in o) - H["tee"][1]) * H["k"] > 160 and tl < {3: 80, 4: 220, 5: 380}.get(H.get("par") or 4, 220)):
                        ty = max(q[1] for q in o) - PREVIEW_TEE_BOTTOM / H["k"]; xs = [q[0] for q in o if abs(q[1] - ty) < 25] or [pin[0]]
                        tee = [round((min(xs) + max(xs)) / 2), round(ty)]; tl2 = H["k"] * math.hypot(tee[0] - pin[0], tee[1] - pin[1])
                        if ydm and tl2 > 1: f = min(1.15, max(0.85, ydm / tl2)); tee = [round(pin[0] + (tee[0] - pin[0]) * f), round(pin[1] + (tee[1] - pin[1]) * f)]
                        print("  發球台改用輪廓估計:", c["short"], L.get("label") or "", H["h"], H["tee"], "→", tee, "原本", round(tl), "碼，Garmin", ydm); H["tee"] = tee; H["tee_est"] = 1
                keep.append(H)
                path = H["path"] = caddie_route(H["tee"], pin, H["k"], GEO.get(L["cid"], {}).get(str(H["h"]), {}), H.get("par"))
                for p in H["plays"]:
                    for sh in p["shots"][:-1]:                                                      # 每洞最後一筆的落點被設成旗桿，不能用
                        if sh[0] in CADDIE_BAG and abs(_path_offset((sh[2], sh[3]), path, H["k"])) <= 15 and sh[6] >= 40:      # 從中線附近打出去的球，落點偏離中線幾碼
                            lateral.setdefault(sh[0], []).append((_path_offset((sh[4], sh[5]), path, H["k"]), sh[6]))
            L["holes"] = keep
        c["layouts"] = [L for L in c["layouts"] if L["holes"]]
    courses = [c for c in courses if c["layouts"]]
    have = {L["cid"] for c in courses for L in c["layouts"]}
    for c in preview_courses():
        c["layouts"] = [L for L in c["layouts"] if L["cid"] not in have]
        if c["layouts"]: courses.append(c)
    for c in courses:
        for L in c["layouts"]:
            for H in L["holes"]:
                if "path" not in H: H["path"] = caddie_route(H["tee"], H["pin"], H["k"], GEO.get(L["cid"], {}).get(str(H["h"]), {}), H.get("par"))
    m = caddie_model(con)
    q = lambda v, f: v[min(len(v) - 1, int(len(v) * f))]
    pool = sorted(o / y for v in lateral.values() for o, y in v)                                    # 樣本少的球桿：用全部球桿「偏離 ÷ 距離」的比例
    m["lat"] = {c: [round(q(sorted(o for o, _ in v), 0.1)), round(q(sorted(o for o, _ in v), 0.5)), round(q(sorted(o for o, _ in v), 0.9)), len(v)] for c, v in lateral.items() if len(v) >= 20}
    m["latr"] = [round(q(pool, 0.1), 3), round(q(pool, 0.5), 3), round(q(pool, 0.9), 3)] if pool else [-0.15, 0, 0.15]
    print("左右偏差（碼，右為正）[十分位, 中位數, 九十分位, 球數]:", m["lat"], "比例:", m["latr"])
    def _rad(v):                                                                                    # 每一球落在「十分位到九十分位」橢圓的幾倍半徑上
        os_, ys = sorted(o for o, _ in v), sorted(y for _, y in v)
        oa, ob, ya, yb = q(os_, 0.1), q(os_, 0.9), q(ys, 0.1), q(ys, 0.9)
        return sorted(math.hypot((o - (oa + ob) / 2) / max(1, (ob - oa) / 2), (y - (ya + yb) / 2) / max(1, (yb - ya) / 2)) for o, y in v)
    rad = {c: _rad(v) for c, v in lateral.items() if len(v) >= 20}
    allr = sorted(r for v in rad.values() for r in v)
    cov = lambda v: [round(q(v, COVER_IN), 2), round(q(v, COVER_OUT), 2)]
    m["cov"] = {c: cov(v) for c, v in rad.items() if len(v) >= 40}                                  # 落點橢圓的倍率：[內圈, 外圈]
    m["covr"] = cov(allr) if allr else [0.66, 1.67]
    m["covp"] = [COVER_IN, COVER_OUT]
    print("落點範圍倍率 [內圈 %d%%, 外圈 %d%%]:" % (COVER_IN * 100, COVER_OUT * 100), m["cov"], "全部:", m["covr"], "（原本的橢圓涵蓋 %.0f%%）" % (100 * sum(1 for r in allr if r <= 1) / max(1, len(allr))))
    note = ("在圖上點一下，當成球現在的位置。系統算出離旗桿幾碼、判斷球位，再用你的歷史紀錄估計「用每一支桿，預期還要幾桿進洞」，建議數字最小的那一支。"
            "做法：①「還要幾桿」的表：你每一筆擊球之前的位置（離旗桿幾碼、什麼球位）→ 那一洞從這一桿起實際又用了幾桿，分組取平均並修成不遞減。"
            "② 每支桿的結果直接取你用這支桿打過的球：離旗桿在這支桿打得到的範圍內，取起點距離相近（±25%）的那些球實際的結果；超出範圍就取全揮桿的前進距離，算出落點再查表。"
            "瞄準線：沿著球道走（球道每 40 碼取一個中心點，從發球台連到旗桿），狗腿洞的距離是沿著球道量的。沿球道的長度比直線多出 12% 以上時，當成輪廓辨識有誤，改用直線。球離這條線超過 30 碼時也改成直接指向旗桿。"
            "每一桿下面的兩行是分支：「打得好」＝這支桿十球裡第九遠的距離、下一桿假設在球道；「打不好」＝十球裡第二短的距離、下一桿假設在長草；各自寫出剩幾碼、接哪一支、照常態還要幾桿。"
            "黃色區域是每一桿的落點範圍，不是一個點：前後是這支桿十球有八球的距離，左右是從球道中線附近打出去的球、落點偏離中線的十球有八球範圍（所以你的右曲會讓整個區域偏右）；黃色圓點是最常見的落點。左右的樣本不到 20 球的球桿，用全部球桿「偏離 ÷ 距離」的比例估計。"
            "路線：從球的位置開始，每一桿選預期桿數最低的球桿（和第二名的差距在誤差內、而第二名的球數多三倍以上時，選球數多的那一支），照它的常態距離前進，下一桿假設在球道，直到上果嶺，再加兩推。「打得好可上果嶺」＝這支桿十球裡最遠的那一球到得了。「你從這裡平均幾桿」是第一桿的預期桿數，通常比常態路線多，因為會有打壞的球。"
            "③ 預期桿數＝1＋這些結果的平均，± 是標準誤（含查表那張表本身的誤差）。兩支桿的差距小於誤差時不能說哪一支比較好。"
            f"資料：正規球場 {m['shots']} 筆擊球；沙坑樣本少，用「比長草多 {m['bunker']} 桿」估計；果嶺上平均 {m['green']} 推。"
            "「我在這裡，怎麼打？」用手機的定位找出你在哪個球場、哪一洞、圖上的哪個位置（每一洞的經緯度和圖上座標的對應，是用你歷次擊球的座標算出來的），再唸出同一句話；位置只在你的瀏覽器裡計算，不會傳到任何地方。限制：沒有考慮風、高低差、障礙物的罰桿，也沒有考慮左右偏差；旗桿位置是你上次打的那天的。球場輪廓是依 Garmin 地圖重新描的示意圖，球位判斷可能有誤，可以手動改。")
    return {"id": "caddie", "title": "AI 桿弟（模擬器）", "note": note, "kind": "caddie", "data": courses, "model": m, "table": {"head": [], "rows": []}}

PLAN_START = "2026-11-16"          # 計劃定案的日期，這一天之後的場次才算「計劃開始後」
PLAN_GOAL = {"score": 95, "by": "2026 年底前", "next": "明年第一季 90"}
PLAN_THREE = 7                     # 每場三推幾洞以下
PLAN_BLOW = 1                      # 每場爆桿幾洞以下（推桿的門檻用 TARGET_ROUND_PUTTS）
PLAN_BAG = [["鐵木桿", "120 碼以上"], ["6鐵", "長草、斜坡"], ["8鐵", "100 到 120"], ["P杆", "80 到 100"], ["A杆", "80 以內"]]   # 選桿表：球桿、用在哪個距離（使用者決定，減法原則）
PLAN_TRAIN = [["長推", "三推 7 洞以下", "下場前在練習果嶺推 8 到 15 步"],          # 標題、目標、怎麼練（都要短）
              ["鐵木桿", "打壞 10% 以下", "練習場模擬球洞，重心放這一支"],
              ["80 碼內", "一次上果嶺 85%", "隔天練切球，多練 50 到 80 碼"],
              ["一號木", "150 碼以下每場 1 球", "瞄左、固定右曲，不追距離"]]
PLAN_RULE = ["選桿用常態距離", "一號木瞄左，身體留在後面", "80 碼內先求上果嶺", "長推先求停在洞邊"]   # 上場要注意的事，一句一件
PLAN_LATER = ["一號木 200 碼", "鐵木桿（球道上）140", "6 鐵（長草）100 碼以上算成功", "7 鐵 130、8 鐵 120、9 鐵 110、P 杆 100", "都以場上的常態為準"]   # 長期計劃：短期目標達成後才做

def section_plan(con):
    rounds = con.execute("SELECT round_id, date, course_short, strokes FROM rounds WHERE category='full_18' ORDER BY date DESC").fetchall()
    def stat(rid):
        putts, three, blow = con.execute("SELECT SUM(putts), SUM(putts>=3), SUM(over_par>=3) FROM holes WHERE round_id=?", (rid,)).fetchone()
        return putts, three or 0, blow or 0
    rows = [{"date": d, "short": sh, "strokes": st, "putts": stat(rid)[0], "three": stat(rid)[1], "blow": stat(rid)[2], "after": d > PLAN_START} for rid, d, sh, st in rounds]
    base = [r for r in rows if not r["after"]][:RECENT_N]; after = [r for r in rows if r["after"]]
    avg = lambda k, rs: round(sum(r[k] for r in rs if r[k] is not None) / max(1, sum(r[k] is not None for r in rs)), 1)
    ids = [rid for rid, d, sh, st in rounds if d <= PLAN_START][:RECENT_N]; q = ",".join("?" * len(ids))
    med_all = lambda club: sorted(x[0] for x in con.execute("SELECT distance_yd FROM shots WHERE club=? AND to_pin_end_m>0 AND distance_yd IS NOT NULL", (club,)))
    bag = []
    for club, use in PLAN_BAG:
        d = med_all(club)
        bag.append({"club": club, "use": use, "chip": club == CHIP_CLUB, "n": len(d), "med": d[(len(d) - 1) // 2] if d else None, "good": d[int((len(d) - 1) * .75)] if d else None})
    hy = [x[0] for x in con.execute(f"SELECT distance_yd FROM shots WHERE club=? AND distance_yd IS NOT NULL AND round_id IN ({q})", [FAIRWAY] + ids)]
    hmed = next(b["med"] for b in bag if b["club"] == FAIRWAY)
    facts = {"three": avg("three", base), "hy_n": round(len(hy) / max(1, len(ids)), 1), "hy_bad": round(100 * sum(x < MISHIT_RATIO * hmed for x in hy) / max(1, len(hy)))}
    dr = con.execute(f"SELECT s.distance_yd, h.fairway FROM shots s JOIN holes h USING(round_id, seq) WHERE s.club=? AND s.shot_no=1 AND s.distance_yd IS NOT NULL AND s.round_id IN ({q})", [DRIVER] + ids).fetchall()
    dd = sorted(x[0] for x in dr); pct = lambda k, n: round(100 * k / max(1, n))
    ch = con.execute(f"""SELECT s.to_pin_start_m*1.09361, s.end_lie, s.to_pin_end_m,
        (SELECT COUNT(*) FROM shots t WHERE t.round_id=s.round_id AND t.seq=s.seq AND t.shot_type!='PUTT' AND t.shot_no>s.shot_no)
        FROM shots s WHERE s.shot_type!='PUTT' AND s.to_pin_start_m>0 AND s.to_pin_start_m*1.09361<=80 AND s.round_id IN ({q})
        AND NOT EXISTS(SELECT 1 FROM shots p WHERE p.round_id=s.round_id AND p.seq=s.seq AND p.shot_no<s.shot_no AND p.shot_type!='PUTT' AND p.to_pin_start_m>0 AND p.to_pin_start_m*1.09361<=80)""", ids).fetchall()
    on = lambda rs: sum(1 for d, lie, end, later in rs if later == 0 and (lie == "Green" or end == 0)); far = [r for r in ch if r[0] > 50]
    fl = [f"三推 {facts['three']} 洞", f"打壞 {facts['hy_bad']}%（每場 {facts['hy_n']} 次）", f"一次上果嶺 {pct(on(ch), len(ch))}%（每場 {round(len(ch) / max(1, len(ids)), 1)} 次）",
          f"150 碼以下每場 {round(sum(x < 150 for x in dd) / max(1, len(ids)), 1)} 球"]
    data = {"factlist": fl, "goal": PLAN_GOAL, "check": {"putts": TARGET_ROUND_PUTTS, "blow": PLAN_BLOW}, "start": PLAN_START, "base": {"n": len(base), "strokes": avg("strokes", base), "best": min(r["strokes"] for r in base), "putts": avg("putts", base), "blow": avg("blow", base)},
            "after": after, "recent": base[:5], "train": PLAN_TRAIN, "rule": PLAN_RULE, "later": PLAN_LATER, "bag": bag, "facts": facts, "total": len(rows)}
    note = (f"目標與計劃在 {PLAN_START} 定案，文字寫在 build_report.py 的 PLAN_* 常數。現況是定案前最近 {len(base)} 場完整 18 洞的平均。"
            f"爆桿＝超出標準桿 3 桿以上的洞。常態距離是該桿全部場次的中位數、打得好是第 75 百分位（不含每洞最後一筆被設成旗桿的擊球）。"
            f"鐵木桿打壞＝距離低於中位數的 {int(MISHIT_RATIO * 100)}%。"
            f"80 碼內一次上果嶺＝每洞第一次在 80 碼內的擊球，之後沒有再記錄到其他擊球、而且落點在果嶺（手錶漏記第二次切球時會偏高）。一號木只算每洞第一桿，偏右是 Garmin 的開球判定。")
    return {"id": "plan", "title": "訓練計劃", "note": note, "kind": "plan", "data": data, "table": {"head": [], "rows": []}}

def section_roundlist(con):
    tags = {"short_course": "小九洞", "partial": "未完成", "nine": "9 洞"}; out = []
    CLUBS = CFG.get("full_clubs", [])      # 「各桿距離」只列全揮桿的球桿
    order = [r[0] for r in con.execute("SELECT round_id FROM rounds WHERE category != 'short_course' ORDER BY date")]
    dist = {}
    for rid_, club_, yd_ in con.execute("SELECT round_id, club, distance_yd FROM shots WHERE distance_yd IS NOT NULL AND shot_type != 'PUTT'"): dist.setdefault((rid_, club_), []).append(yd_)
    def med(rids, club):
        d = sorted(x for r_ in rids for x in dist.get((r_, club), []))
        return [round(d[(len(d) - 1) // 2]), len(d)] if d else None
    def club_table(rid):
        prev = order[:order.index(rid)] if rid in order else order
        return [[c, med([rid], c), med(prev[-RECENT_N:], c), med(prev, c)] for c in CLUBS if med([rid], c)]
    roles = club_roles(); _dd = {}                                   # 每支桿的常態距離（中位數），明細表判斷打壞用
    for c_, d_ in con.execute("SELECT s.club, s.distance_yd FROM shots s JOIN rounds r USING(round_id) WHERE r.category != 'short_course' AND s.distance_yd > 0 AND s.shot_type != 'PUTT'"): _dd.setdefault(c_, []).append(d_)
    MED = {c_: sorted(v)[len(v) // 2] for c_, v in _dd.items() if len(v) >= 15}
    for rid, date, course, cat, n, strokes, over, diff, short, fname in con.execute("SELECT round_id, date, course, category, holes_played, strokes, over_par, differential, course_short, file FROM rounds ORDER BY date DESC"):
        holes = []
        for seq, hole, par, st, putts, src, yd, pen, on, unrec in con.execute("SELECT seq, hole, par, strokes, putts, putts_source, length_yd, penalties, strokes_to_green, unrecorded FROM holes WHERE round_id=? ORDER BY seq", (rid,)):
            shots = con.execute("SELECT club, distance_yd, source, watch_club, hazard, to_pin_start_m FROM shots WHERE round_id=? AND seq=? AND shot_type != 'PUTT' ORDER BY shot_no", (rid, seq)).fetchall()
            miss = unrec or 0
            holes.append([hole, par, yd, [[c or "?", d, sr if sr == "site" else "", wc or "", hz or "", bad_shot(shots, k, MED, roles)] for k, (c, d, sr, wc, hz, _) in enumerate(shots)], putts, src == "estimated", st, pen or 0, on, miss])
        pv = [h[4] for h in holes if h[4] is not None]
        out.append({"date": date, "course": course, "short": short, "tag": tags.get(cat, ""), "cat": {"full_18": "full", "short_course": "short"}.get(cat, "partial"), "n": n, "strokes": strokes, "over": over, "hcp": diff,
                    "putts": sum(pv) if pv else None, "est": any(h[5] for h in holes), "holes": holes, "len": {seq: round(yd) for seq, yd in con.execute("SELECT seq, length_yd FROM holes WHERE round_id=? AND length_yd>0", (rid,))}, "clubs": club_table(rid), "gps": round_gps(fname), "site": (date, short) in merge_shots.caddie_files(), "parts": round_parts(fname)[0], "combo": round_parts(fname)[1]})
    note = "由新到舊排列，預設只列完整打完 18 洞的場次，上方按鈕可加入未打完與短球場的場次。每張卡片的柱子是每一洞超出標準桿幾桿（Par 是一小段綠色、Birdie 是往下的藍色），下面的紅點是三推以上的洞；綠色的數字代表達到目標（總桿 %d、推桿 %d、三推 %d 洞、爆桿 %d 洞以下）。點任一場看計分卡與細節。" % (PLAN_GOAL["score"], TARGET_ROUND_PUTTS, PLAN_THREE, PLAN_BLOW) + \
           "擊球欄是被手錶記錄到的擊球，依序為球桿與距離（碼）。沒有手錶紀錄的洞，推桿數由「總桿 − 記錄擊球 − 罰桿」推算。"
    return {"id": "rounds", "title": "場次", "note": note, "kind": "roundlist", "data": out, "targets": {"score": PLAN_GOAL["score"], "putts": TARGET_ROUND_PUTTS, "three": PLAN_THREE, "blow": PLAN_BLOW}, "table": {"head": [], "rows": []}}

def handicap_index(diffs):
    """世界差點系統 (WHS)：取最近 20 場的差點微分，場數不足時照官方對照表取最好的幾場平均。"""
    d = sorted(diffs[-20:]); n = len(d)
    if n < 3: return None
    k, adj = {3: (1, -2.0), 4: (1, -1.0), 5: (1, 0), 6: (2, -1.0)}.get(n) or ((2 if n <= 8 else 3 if n <= 11 else 4 if n <= 14 else 5 if n <= 16 else 6 if n <= 18 else 7 if n == 19 else 8), 0)
    return round(sum(d[:k]) / k + adj, 1)

def section_handicap(con):
    rows = con.execute("SELECT date, course, strokes, differential, rating, slope, rating_approx FROM rounds WHERE category = 'full_18' AND differential IS NOT NULL ORDER BY date").fetchall()
    data, diffs = [], []
    for date, course, strokes, diff, rating, slope, approx in rows:
        diffs.append(diff)
        data.append({"date": date, "course": course, "v": diff, "measured": True, "extra": f"{strokes} 桿", "avg": handicap_index(diffs)})
    now = data[-1]["avg"]
    note = "每一個點是那一場的差點微分：(總桿 − 球場難度) × 113 ÷ 坡度，數字越低代表打得越好，而且已經把球場的難易算進去，不同球場可以互相比較。" \
           "折線是差點指數：最近 20 場裡最好的 8 場取平均（場數不足時照世界差點系統的對照表）。球場難度與坡度來自 Garmin。" \
           "近似的地方：每洞最多算到標準桿 +4（正式規則是淨 Double Bogey）；前九後九分開記的球場把兩個半場的難度相加；Long Bien 缺一個半場的難度，用另一個半場的兩倍代替。"
    table = {"head": ["日期", "球場", "總桿", "球場難度", "坡度", "本場差點", "差點指數"],
             "rows": [[d["date"], d["course"], r[2], round(r[4], 1), round(r[5]), d["v"], d["avg"] or ""] for d, r in zip(data, rows)]}
    return {"id": "handicap", "title": f"差點：目前 {now}", "note": note, "kind": "trend", "data": data, "table": table,
            "opts": {"unit": "", "step": 5, "plain": True, "line": "差點指數", "dot": "每一場的差點"}}

def section_score_trend(con):
    rows = con.execute("SELECT date, course, strokes, over_par FROM rounds WHERE category = 'full_18' ORDER BY date").fetchall()
    data = []
    for i, (date, course, strokes, over) in enumerate(rows):
        last = [r[2] for r in rows[max(0, i - 4):i + 1]]
        data.append({"date": date, "course": course, "v": strokes, "measured": True, "extra": f"+{over}",
                     "avg": round(statistics.mean(last), 1) if len(last) >= 3 else None})
    table = {"head": ["日期", "球場", "總桿", "超出標準桿", "近 5 場平均"], "rows": [[d["date"], d["course"], d["v"], d["extra"], d["avg"] or ""] for d in data]}
    sec = {"id": "score-trend", "title": "總桿的變化", "note": "只列完整打完 18 洞的場次，不含中油短球場與未打完的場次。折線是最近 5 場的移動平均。",
           "kind": "trend", "data": data, "table": table, "opts": {"unit": " 桿", "step": 5, "plain": True}}
    return add_trend(sec, " 桿")

TARGET_OVER = 1.0    # 每洞超出標準桿的目標：+1（18 洞都 +1 就是總桿 90）
TARGET_PUTTS = 2.0   # 每洞推桿的目標：2 推以下（長期）
TARGET_ROUND_PUTTS = 40   # 現階段目標：每場推桿 40 以下（使用者 2026-11-15 訂）

def section_score_pair(con):
    recent = {r[0] for r in con.execute("SELECT round_id FROM rounds ORDER BY date DESC LIMIT ?", (RECENT_N,))}
    rows = con.execute("""SELECT h.round_id, MIN(h.par, 5), h.over_par FROM holes h JOIN rounds r USING(round_id) WHERE r.category != 'short_course'""").fetchall()
    n_full = con.execute("SELECT COUNT(*) FROM rounds WHERE category = 'full_18'").fetchone()[0]
    per_round = {par: c / n_full for par, c in con.execute("""SELECT MIN(h.par, 5), COUNT(*) FROM holes h JOIN rounds r USING(round_id)
        WHERE r.category = 'full_18' GROUP BY 1""")}                      # 一場平均有幾個這種洞
    groups, trows = [], []
    for par, name in ((3, "三桿洞"), (4, "四桿洞"), (5, "五桿洞")):
        old = [o for rid, p, o in rows if p == par and rid not in recent]; new = [o for rid, p, o in rows if p == par and rid in recent]
        mo, mn, k = statistics.mean(old), statistics.mean(new), per_round[par]
        groups.append({"label": name, "old": round(mo, 2), "new": round(mn, 2), "foot": [f"{k:.0f} 洞", f"{mo * k:.1f} → {mn * k:.1f} 桿"]})
        trows.append([name, len(old), len(new), round(mo, 2), round(mn, 2), round(k, 1), round(mo * k, 1), round(mn * k, 1)])
    note = f"柱子是每洞平均超出標準桿幾桿：淡色是過去，實色是最近 {RECENT_N} 場，過去不包含最近 {RECENT_N} 場。不含中油短球場；六桿洞併入五桿洞。" \
           "目標線：每洞都打 Bogey 就是總桿 90。每場合計＝每洞平均 × 一場平均有幾個這種洞。"
    table = {"head": ["洞型", "過去洞數", "近期洞數", "過去每洞", "近期每洞", "每場幾洞", "過去每場合計", "近期每場合計"], "rows": trows}
    return {"id": "score-par", "title": "各洞型每洞平均超出標準桿幾桿", "note": note, "kind": "pair",
            "data": {"groups": groups, "mode": "over", "step": 0.5, "target": TARGET_OVER, "targetLabel": "目標：每洞 Bogey（等於總桿 90）",
                     "foot": [["每場有", 38], ["每場合計丟", 55]]}, "table": table}

def section_putt_count(con):
    """推桿：18 洞裡一推、兩推、三推以上各幾洞，過去對最近 10 場。只講這三個數字。"""
    recent = {r[0] for r in con.execute("SELECT round_id FROM rounds ORDER BY date DESC LIMIT ?", (RECENT_N,))}
    rows = con.execute("""SELECT h.round_id, h.putts FROM holes h JOIN rounds r USING(round_id)
        WHERE r.category != 'short_course' AND h.putts IS NOT NULL""").fetchall()
    old = [p for rid, p in rows if rid not in recent]; new = [p for rid, p in rows if rid in recent]
    groups, trows = [], []
    for name, test in (("一推", lambda p: p == 1), ("兩推", lambda p: p == 2), ("三推以上", lambda p: p >= 3)):
        vo, vn = 18 * sum(map(test, old)) / len(old), 18 * sum(map(test, new)) / len(new)
        groups.append({"label": name, "old": round(vo, 1), "new": round(vn, 1)}); trows.append([name, round(vo, 1), round(vn, 1)])
    ao, an = statistics.mean(old), statistics.mean(new)
    note = f"把每一洞依推桿數分成一推、兩推、三推以上，換算成 18 洞裡各有幾洞。淡色是過去，實色是最近 {RECENT_N} 場，過去不包含最近 {RECENT_N} 場。" \
           f"不含中油短球場。沒有手錶紀錄的洞，推桿數由「總桿 − 記錄擊球 − 罰桿」推算，上限 3 推。每洞平均推桿：過去 {ao:.2f}，最近 {RECENT_N} 場 {an:.2f}。"
    table = {"head": ["", "過去（洞）", f"最近 {RECENT_N} 場（洞）"], "rows": trows + [["每洞平均推桿", round(ao, 2), round(an, 2)]]}
    return {"id": "putt-count", "title": "推桿：18 洞裡一推、兩推、三推各幾洞", "note": note, "kind": "pair",
            "data": {"groups": groups, "mode": "holes", "step": 3, "unit": " 洞", "foot": [],
                     "legendNote": f"每場推桿：{ao * 18:.1f} → {an * 18:.1f}（目標 {TARGET_ROUND_PUTTS} 以下）"}, "table": table}

def section_putt_holes(con):
    rows = con.execute("""SELECT r.round_id, r.date, r.course, SUM(h.putts = 1), SUM(h.putts = 2), SUM(h.putts >= 3), COUNT(h.putts), AVG(h.putts)
        FROM rounds r JOIN holes h USING(round_id) WHERE r.category != 'short_course' AND h.putts IS NOT NULL
        GROUP BY r.round_id HAVING COUNT(h.putts) >= 9 ORDER BY r.date""").fetchall()
    rounds = [{"date": d, "course": c, "c": [round(18 * a / n, 1), round(18 * b / n, 1), round(18 * e / n, 1)], "avg": round(avg, 2), "v": 18 * e / n}
              for rid, d, c, a, b, e, n, avg in rows]
    tmp = add_trend({"data": rounds, "note": ""}, " 洞"); trend = tmp["opts"]["trend"]
    recent = {r[0] for r in con.execute("SELECT round_id FROM rounds ORDER BY date DESC LIMIT ?", (RECENT_N,))}
    rec = con.execute(f"""SELECT AVG(h.putts) FROM holes h JOIN rounds r USING(round_id)
        WHERE r.category != 'short_course' AND h.putts IS NOT NULL AND h.round_id IN ({','.join(str(i) for i in recent)})""").fetchone()[0]
    for r in rounds: del r["v"]
    bym = {}
    for rid, d, c, a, b, e, n, avg in rows:
        k = bym.setdefault(d[:7], [0, 0, 0, 0, 0, 0.0]); k[0] += a; k[1] += b; k[2] += e; k[3] += n; k[4] += 1; k[5] += avg * n
    months = [{"label": f"{int(ym[:4])}/{int(ym[5:])}", "rounds": k[4], "c": [round(18 * k[j] / k[3], 1) for j in range(3)], "avg": round(k[5] / k[3], 2)}
              for ym, k in sorted(bym.items())]
    note = "每個月三根柱子：把該月所有洞依推桿數分成一推、兩推、三推以上，換算成 18 洞裡有幾洞。不含中油短球場。" \
           "沒有手錶紀錄的洞，推桿數由「總桿 − 記錄擊球 − 罰桿」推算，上限 3 推。" + tmp["note"]
    table = {"head": ["月份", "場數", "一推（洞）", "兩推（洞）", "三推以上（洞）", "每洞平均"],
             "rows": [[mm["label"], mm["rounds"]] + mm["c"] + [mm["avg"]] for mm in months]}
    return {"id": "putt-holes", "title": "每月的推桿：18 洞裡幾洞一推、兩推、三推", "note": note, "kind": "putts",
            "data": {"months": months, "trend": trend, "recent": round(rec, 2), "target": TARGET_PUTTS}, "table": table}

def stacked(section):
    """把熱力圖區塊改成堆疊直方圖（各欄加總為 100% 的分布）。"""
    def wrapped(con):
        sec = section(con); sec["kind"] = "stack"; return sec
    return wrapped

def with_trend(section, unit):
    return lambda con: add_trend(section(con), unit)

# 主頁：6 個區塊
MAIN = [section_roundlist]                                      # 場次：最近打得怎麼樣（卡片）
DATA = [section_club_distance,                                  # 數據：每支桿可以期待什麼？（使用者指定排第一）
        section_score_trend, section_handicap,                  # 我有沒有進步？
        section_score_pair,                                     # 桿數丟在哪裡？
        section_putt_count]                                     # 推桿：一推、兩推、三推各幾洞（切球併入球桿距離圖）
# 第二頁：重複、結論已確定、或樣本太少的圖
def plain_trend(section, unit):
    def wrapped(con):
        sec = add_trend(section(con), unit); sec["opts"]["plain"] = True; return sec    # 估算的推桿當成明確數字，不區分
    return wrapped
MORE = [section_putt_holes, section_club_recent, stacked(section_seq_cost), with_trend(section_mishit_trend, " 個百分點"), section_mishit_club, section_seq_club, section_seq_chain, section_chip, section_chip_once,
        section_score_par,
        make_length_section(3, "三桿洞", [(0, 120), (120, 150), (150, 180), (180, 999)]),
        make_length_section(4, "四桿洞", [(0, 300), (300, 330), (330, 360), (360, 400), (400, 999)]),
        make_length_section(5, "五桿洞", [(0, 430), (430, 470), (470, 510), (510, 999)]),
        section_blowup, plain_trend(section_putt_trend, " 推"), plain_trend(section_putt_share, " 個百分點"),
        section_putt_par, section_putt_approach, section_putt_course]

PAGE = r"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="manifest" href="manifest.webmanifest"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black"><meta name="apple-mobile-web-app-title" content="MI 高爾夫"><link rel="apple-touch-icon" href="app/icon-180.png">
<title>__TITLE__</title>
<script>try{var t=localStorage.getItem("mi-theme");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}</script>
<style>
:root{color-scheme:light;--surface:#fcfcfb;--page:#f4f3ef;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--series:#2a78d6;--trend:#eb6834;--up:#0c8a0c;--down:#d03b3b;--putt:#1baf7a}
@media (prefers-color-scheme:dark){:root:not([data-theme]){color-scheme:dark;--surface:#1a1a19;--page:#111110;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--series:#3987e5;--trend:#f08a4b;--up:#2fbf4f;--down:#f06a6a;--putt:#199e70}}
:root[data-theme=dark]{color-scheme:dark;--surface:#1a1a19;--page:#111110;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--series:#3987e5;--trend:#f08a4b;--up:#2fbf4f;--down:#f06a6a;--putt:#199e70}
:root[data-theme=light]{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font-family:-apple-system,"PingFang TC","Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;line-height:1.6}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 60px}
header h1{font-size:24px;font-weight:600;margin:0 0 4px}
header p{margin:0;color:var(--ink2);font-size:14px}header{display:flex;align-items:center;justify-content:space-between;gap:10px}.pthm{display:flex;flex:none;border:1px solid var(--axis);border-radius:999px;padding:2px}.pthm button{font:inherit;font-size:12px;line-height:1;padding:5px 9px;border-radius:999px;border:0;background:none;color:var(--muted);cursor:pointer}.pthm button.on{background:var(--ink);color:var(--page)}.cad-msg{display:flex;align-items:center;gap:8px;font-size:15px;font-weight:600;line-height:1.25;position:absolute;left:0;right:0;top:calc(100% + 8px);z-index:25;background:var(--surface);border:1px solid var(--axis);border-radius:12px;padding:8px 12px;box-shadow:0 6px 18px rgba(0,0,0,.28)}.cad-msg>span{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.cad-next .nl.ok{color:#2fbf5b;font-weight:700}.cad-msg:empty{display:none}.cad-run{flex:1;min-width:0;height:40px;display:flex;align-items:center;justify-content:flex-end;gap:12px;overflow:hidden;white-space:nowrap}.cad-run .g{display:inline-flex;flex-direction:column;align-items:flex-end;line-height:1.1}.cad-run u{text-decoration:none;font-size:11px;color:var(--muted)}.cad-run b{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums}.cad-run .t b{font-size:21px}.cad-next button.fin{flex:1;min-width:0;font-size:15px;text-align:left;padding:0 12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border:2px solid #e6b400;color:var(--ink)}.cad-next button.fin.done{flex:none;max-width:100%;border:1px solid #2fbf5b;font-size:14px}.cad-day{margin-top:10px}.cad-map{position:relative}.cad-fin{position:absolute;right:10px;bottom:10px;z-index:3;font:inherit;font-size:15px;font-weight:800;padding:10px 13px;border-radius:12px;border:2px solid #e6b400;background:rgba(0,0,0,.74);color:#fff;cursor:pointer}.cad-fin.done{border-color:#2fbf5b;font-size:14px}.cad-day summary{font-weight:700;cursor:pointer;padding:6px 0}.cad-t td.cum{font-weight:800}.cad-shots{margin-top:0}.cad-sheet{position:fixed;inset:0;z-index:200}.cad-sheet[hidden]{display:none}.cad-sheet .bg{position:absolute;inset:0;background:rgba(0,0,0,.45)}.cad-sheet .box{overscroll-behavior:contain;-webkit-overflow-scrolling:touch;position:absolute;left:0;right:0;bottom:0;background:var(--surface);color:var(--ink);border-radius:16px 16px 0 0;padding:14px 14px calc(14px + env(safe-area-inset-bottom));max-width:560px;margin:0 auto;max-height:80vh;max-height:86dvh;overflow:auto}.cad-gps .cad-add.nx{background:transparent;color:var(--muted);border:1px solid var(--axis);font-size:16px;font-weight:700}.cad-sheet .hd{display:flex;justify-content:space-between;align-items:baseline;font-size:19px;margin-bottom:4px}.cad-sheet .hd .tot{font-weight:800;font-size:20px}.cad-sheet .lab{font-size:14px;color:var(--muted);margin:12px 0 6px}.cad-sheet .nums{display:flex;flex-wrap:wrap;gap:7px}.cad-sheet .nums button{font:inherit;font-size:20px;font-weight:800;width:50px;height:48px;border-radius:11px;border:1px solid var(--border);background:var(--page);color:var(--ink);cursor:pointer}.cad-sheet .nums button.sg{border:2px solid #e6b400}.cad-sheet .nums button.on{background:var(--ink);color:var(--page);border-color:var(--ink)}.cad-sheet .ok{font:inherit;font-size:18px;font-weight:800;width:100%;height:52px;margin-top:16px;border-radius:12px;border:0;background:var(--ink);color:var(--page);cursor:pointer}.cad-undo{flex:none;font:inherit;font-size:14px;font-weight:700;padding:7px 12px;border-radius:999px;border:2px solid var(--ink);background:transparent;color:var(--ink);cursor:pointer}.cad-bar{position:sticky;top:0;z-index:20;background:var(--page);margin:0 -10px;padding:6px 10px 2px;border-bottom:1px solid var(--grid)}@media(min-width:641px){.cad-bar{background:var(--surface)}}.cad-gps{display:flex;align-items:stretch;gap:7px;margin:0 0 6px;position:relative}.cad-add,.cad-finb{font:inherit;font-weight:800;height:48px;border-radius:12px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 8px}.cad-add{flex:1.5 1 0;min-width:0;font-size:17px;border:0;background:#e6b400;color:#111}.cad-finb{flex:1 1 0;min-width:0;font-size:15px;border:2px solid var(--axis);background:var(--surface);color:var(--ink)}.cad-finb.done{border-color:#2fbf5b}.cad-map .cad-here{position:absolute;right:10px;bottom:10px;z-index:3;height:46px;padding:0 16px;border-radius:23px;background:rgba(0,0,0,.78);color:#fff;border:1.5px solid rgba(255,255,255,.55);font-size:16px}.cad-sheet .hd .x{font:inherit;font-size:22px;border:0;background:none;color:var(--muted);padding:4px 10px;cursor:pointer}.cad-sheet .clubs{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:10px}.cad-sheet .clubs button{font:inherit;display:flex;align-items:baseline;gap:10px;height:62px;padding:0 16px;border-radius:13px;border:1px solid var(--border);background:var(--page);color:var(--ink);cursor:pointer;text-align:left}.cad-sheet .clubs button b{font-size:26px;font-weight:800;min-width:48px}.cad-sheet .clubs button span{font-size:14px;color:var(--muted)}.cad-sheet .clubs button.sg{border:3px solid #e6b400}.cad-sheet .clubs button.sg span{color:#b88a00;font-weight:800}.cad-sheet .clubs button.o{background:transparent;border-style:dashed}.cad-here{font:inherit;font-size:16px;font-weight:800;height:44px;padding:0 14px;border-radius:12px;border:0;background:var(--ink);color:var(--page);cursor:pointer;flex:none;display:inline-flex;align-items:center;gap:7px}.cad-here i{width:10px;height:10px;border-radius:50%;background:#8a8a8a;flex:none}.cad-here.live i{background:#2fbf5b}.cad-here.busy i{background:#f0a020;animation:cadp 1s infinite}@keyframes cadp{50%{opacity:.25}}.cad-log{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:10px 0 4px;font-size:13px;color:var(--muted)}.cad-log button{font:inherit;font-size:13px;padding:7px 12px;border-radius:9px;border:1px solid var(--border);background:var(--surface);color:var(--ink);cursor:pointer}.cad-log button.x{color:var(--muted)}.cad-log .lm{flex-basis:100%;min-height:16px;font-size:12px}
.cad-pick{flex-basis:100%;display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}.cad-pick button,.cad-ed .cks button{font:inherit;font-size:14px;font-weight:700;min-width:38px;padding:7px 6px;border-radius:9px;border:1px solid var(--border);background:var(--surface);color:var(--ink2);cursor:pointer}.cad-pick button.on,.cad-ed .cks button.on{background:var(--ink);color:var(--page);border-color:var(--ink)}
.cad-ed{margin:10px 0 6px;font-size:14px}.cad-ed .eh{color:var(--muted);font-size:13px;margin-bottom:6px}.cad-ed .er{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:6px 0;border-top:1px solid var(--grid)}.cad-ed .er>b{font-size:16px}.cad-ed .yd{min-width:54px;font-weight:700;font-variant-numeric:tabular-nums}.cad-ed .cks{display:flex;gap:4px;flex-wrap:wrap;flex:1}.cad-ed .cks button{font-size:13px;min-width:32px;padding:6px 4px}
.cad-ed .del{font:inherit;border:0;background:none;color:var(--muted);font-size:16px;padding:6px 8px;cursor:pointer}.cad-ed .ef{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding-top:8px;border-top:1px solid var(--grid)}.cad-ed .st{display:inline-flex;align-items:center;gap:6px;color:var(--muted)}.cad-ed .st button{font:inherit;font-size:18px;width:36px;height:34px;border-radius:9px;border:1px solid var(--border);background:var(--surface);color:var(--ink);cursor:pointer}.cad-ed .st b{min-width:16px;text-align:center;color:var(--ink);font-size:16px}.cad-ed .tot{font-weight:800;font-size:16px;margin-left:auto}
.cad-next{display:flex;align-items:center;gap:5px;min-height:40px;margin:0 0 6px;overflow-x:auto}.cad-next .nl{font-size:13px;color:var(--muted);white-space:nowrap;margin-right:2px}.cad-next button{font:inherit;font-size:15px;font-weight:700;min-width:40px;height:36px;padding:0 6px;border-radius:9px;border:1px solid var(--border);background:var(--surface);color:var(--ink2);cursor:pointer;flex:0 0 auto}.cad-next button.sg{border-color:#e6b400}.cad-next button.on{background:var(--ink);color:var(--page);border-color:var(--ink)}
.cad-top{display:flex;gap:5px;align-items:stretch;margin:0 0 7px;height:50px}.cad-top select{height:auto!important;font-size:17px!important;max-width:none!important;padding:0 4px 0 8px!important}.cad-top select:first-child{flex:1 1 104px!important;min-width:0!important;text-overflow:ellipsis}.cad-top select:nth-child(2){flex:0 0 auto!important;min-width:56px!important}.cad-hole{flex:2 0 auto;min-width:0;padding:0 8px;font:inherit;display:flex;align-items:center;justify-content:center;gap:6px;border-radius:11px;border:2px solid var(--ink);background:var(--ink);color:var(--page);cursor:pointer;white-space:nowrap}.cad-hole b{font-size:20px;font-weight:800}.cad-hole i{font-style:normal;font-size:14px;opacity:.8}.cad-step{flex:none;width:42px;padding:0;font:inherit;font-size:18px;border-radius:11px;border:1px solid var(--axis);background:var(--surface);color:var(--ink);cursor:pointer}.cad-step:disabled{opacity:.3}.cad-step.nx{width:auto;padding:0 9px;font-size:16px;font-weight:700;white-space:nowrap}.cad-top{height:52px;gap:6px}.cad-course{flex:1.35 1 0;min-width:0;font:inherit;display:flex;align-items:center;justify-content:center;gap:5px;border-radius:11px;border:1px solid var(--axis);background:var(--surface);color:var(--ink);cursor:pointer;padding:0 8px;white-space:nowrap}.cad-course b{font-size:18px;font-weight:800;overflow:hidden;text-overflow:ellipsis}.cad-course i,.cad-hole i{font-style:normal;font-size:13px;opacity:.7}.cad-top .cad-hole{flex:1 1 0;padding:0 6px}.cad-top .cad-step.nx{flex:1 1 0;width:auto}
.cad-map .hud{position:absolute;z-index:3;pointer-events:none;color:#fff}.cad-map .hud.tl{left:10px;top:8px;text-shadow:0 0 3px #000,0 0 6px #000,0 1px 2px #000}.cad-map .hud.tl b{display:block;font-size:36px;font-weight:900;line-height:1.05;font-variant-numeric:tabular-nums}.cad-map .hud.tl span{font-size:16px;font-weight:700}.cad-map .hud.tl.stale{opacity:.5}
.cad-map .hud.br{max-height:calc(100% - 76px);overflow-y:auto;overscroll-behavior:contain;right:8px;bottom:8px;background:rgba(0,0,0,.66);border-radius:12px;padding:7px 10px;min-width:118px;max-width:62%;pointer-events:auto}.cad-map .hud.br .hd{display:flex;flex-direction:column;line-height:1.15;margin-bottom:3px}.cad-map .hud.br .hd span{font-size:12px;color:#d8d8d8}.cad-map .hud.br .hd b{font-size:19px;color:#ffd23f;font-weight:900}.cad-map .hud.br .alt{font-size:12px;color:#cfcfcf;margin-bottom:3px}.cad-map .hud.br .alt.go{color:#ff8a5c}
.cad-map .hud.br .r{display:flex;align-items:baseline;gap:6px;font:inherit;width:100%;background:none;border:0;padding:2px 0;text-align:left;font-size:18px;line-height:1.25}.cad-map .hud.br button.r{cursor:pointer}.cad-map .hud.br .r i{font-style:normal;font-size:15px;width:18px}.cad-map .hud.br .r b{font-weight:900;min-width:30px}.cad-map .hud.br .r span{font-weight:900;font-variant-numeric:tabular-nums}.cad-map .hud.br .r em{font-style:normal;font-size:12px;font-weight:700}.cad-map .hud.br .done{color:#b6ff9c}.cad-map .hud.br .done em{color:#ff9a9a}.cad-map .hud.br .plan{color:#ffd23f}.cad-map .hud.br .plan em{color:#ff8a5c}
.cad-map .hud.bl{left:8px;bottom:8px;width:70px;background:rgba(0,0,0,.55);border:1.5px solid rgba(255,255,255,.75);border-radius:8px;padding:3px;pointer-events:auto;cursor:pointer}.cad-map .hud.bl svg{width:100%;height:auto;display:block}
.cad-map>svg{touch-action:none}.cad-map .cad-here{top:8px;bottom:auto;right:8px;height:44px}.cad-gps{gap:7px}.cad-edit{flex:1 1 auto;min-width:0;height:52px;border-radius:12px;border:2px solid #2fbf5b;background:var(--surface);color:var(--ink);font:inherit;display:flex;align-items:center;justify-content:space-between;padding:0 14px;cursor:pointer}.cad-edit b{font-size:18px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.cad-edit span{font-size:15px;color:var(--muted);flex:none;margin-left:8px}.cad-edit[hidden]{display:none}.cad-gps .cad-add{flex:1 1 auto;height:52px;font-size:19px}.cad-gps .cad-add.sm{flex:0 0 62px;font-size:28px;padding:0}
body.tabs-top{padding-bottom:0!important}body.tabs-top .ptabs{position:sticky;top:0;bottom:auto;left:auto;right:auto;margin:0;border-width:0 0 1px;border-radius:0;padding:calc(6px + env(safe-area-inset-top)) 8px 6px;z-index:60;transform:none}body.tabs-top .cad-bar{top:var(--tabh,52px)}body.tabs-hide{padding-bottom:0!important}body.tabs-hide .ptabs{position:fixed;top:0;left:0;right:0;bottom:auto;margin:0;border-width:0 0 1px;border-radius:0;padding:calc(6px + env(safe-area-inset-top)) 8px 6px;transform:translateY(-101%);transition:transform .18s;z-index:60}body.tabs-hide.tabs-on .ptabs{transform:none}body.tabs-hide .wrap{transition:transform .18s}body.tabs-hide.tabs-on .wrap{transform:translateY(var(--tabh,56px))}body.tabs-on .cad-menu{background:var(--ink);color:var(--page)}.tabgrip{position:fixed;left:0;right:0;bottom:0;height:calc(2px + env(safe-area-inset-bottom));pointer-events:none}header .cad-menu{height:40px;width:44px;font-size:20px}header h1{flex:1;min-width:0}.cad-menu{flex:none;width:46px;font:inherit;font-size:22px;font-weight:700;border-radius:12px;border:1px solid var(--axis);background:var(--surface);color:var(--ink);cursor:pointer;padding:0}.cad-foot{position:relative;display:flex;gap:7px;margin:8px 0 0}.cad-modebar{display:none;flex:1;align-items:center;gap:10px;padding:6px 6px 6px 14px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}.cad-modebar span{font-size:15px;color:var(--ink2);flex:1}.cad-modebar button.lg{background:transparent;color:var(--ink);border:1px solid var(--axis)}.cad-modebar button{font-size:17px;font-weight:800;padding:10px 16px;border-radius:10px;border:0;background:#e3b100;color:#111}.cad-view .cad-modebar{display:flex}.cad-view .cad-gps,.cad-view .cad-foot>.cad-card,.cad-view .cad-foot>.cad-finb{display:none}.cad-tomode{display:block;margin:0 0 10px auto;font-size:13px;padding:6px 12px;border-radius:8px;border:1px solid var(--border);background:none;color:var(--ink2)}.cad-card{flex:1;min-width:0;font:inherit;display:flex;align-items:center;gap:8px;height:54px;padding:0 12px;border-radius:12px;border:1px solid var(--axis);background:var(--surface);color:var(--ink);cursor:pointer;text-align:left}.cad-card .l{flex:1;min-width:0;font-size:13px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.cad-card b{font-size:19px;font-weight:800;white-space:nowrap}.cad-card i{font-style:normal;font-size:22px;color:var(--muted)}.cad-foot .cad-finb{flex:0 0 auto;height:54px;padding:0 14px;font-size:16px}.cad-card{height:auto;min-height:64px;padding:5px 8px;gap:6px}.cad-card .csc{flex:1;min-width:0;margin:0;gap:1px}.cad-card .rsc b{font-size:12px;font-weight:800}.cad-card .rsc i{font-size:9px;line-height:9px;height:9px}.cad-card .rsc u{font-size:9px;line-height:10px;margin-bottom:1px}.rsc b.e{border:1px dashed var(--grid)}.rsc b.e.o{color:var(--muted);font-size:10px}.rsc .c.cur b{outline:2px solid #e8b400;outline-offset:1px}.cad-card .cr{flex:0 0 auto;display:flex;flex-direction:column;align-items:flex-end;line-height:1.15;min-width:44px}.cad-card .cr .o{font-size:11px;color:var(--muted);white-space:nowrap}.cad-card .cr b{font-size:20px}.cad-card .cr .t{font-size:11px;color:var(--muted);white-space:nowrap}.cad-foot .cad-finb{width:72px;height:auto;min-height:64px;padding:4px 2px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;font-size:14px;line-height:1.1}.cad-finb .ic{font-size:20px}.cad-sheet .fsc{display:flex;align-items:center;gap:6px;margin:6px 0}.cad-sheet .fsc .nm{flex:0 0 32px;font-size:13px;color:var(--muted);font-weight:700}.cad-sheet .fsc .rsc{flex:1;margin:0}.cad-sheet .fsc .rsc b{max-width:28px;font-size:14px}.cad-sheet .fsc .rsc i{font-size:10px}.cad-sheet .fsc .c{cursor:pointer}.cad-sheet .fsc .sub{flex:0 0 28px;text-align:right;font-size:16px}.cad-sheet .owe button{white-space:nowrap;flex:0 0 auto}.cad-sheet .hd .bk{font:inherit;font-size:26px;line-height:1;border:0;background:none;color:var(--ink);padding:0 8px 0 0;cursor:pointer}.cad-sheet .clist .n small{display:block;font-size:12px;color:var(--muted);font-weight:400}.cad-map .hud.br button.r.done{min-height:40px;font-size:21px;padding:4px 2px;align-items:center;border-bottom:1px solid rgba(255,255,255,.14)}.cad-map .hud.br button.r.done:last-of-type{border-bottom:0}.cad-map .hud.br .hd.fin{margin:6px 0 0;padding-top:5px;border-top:1px solid rgba(255,255,255,.3)}.cad-map .hud.br .hd.fin span{color:#d8d8d8}.cad-map .hud.br .hd.fin b{color:#b6ff9c}.cad-map .hud.br .lb{font-size:11px;color:#9a9a9a;margin:4px 0 0;letter-spacing:1px}.cad-map .hud.br .r small{font-size:11px;font-weight:700;color:#cfcfcf}.cad-map .hud.tl span.sub{display:block;font-size:14px;opacity:.92;color:#fff;margin:0}.cad-map .hud.bl:not([hidden]) ~ .cad-rs{left:86px}.cad-map .cad-rs{position:absolute;bottom:10px;left:8px;z-index:3;font:inherit;font-size:14px;font-weight:800;padding:8px 12px;border-radius:16px;border:1.5px solid #e6b400;background:rgba(0,0,0,.7);color:#fff}.cad-view .cad-map{-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}.cad-map .cad-side{position:absolute;top:100px;height:120px;width:44px;z-index:3;font:inherit;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;padding:0;border-radius:14px;border:1.5px solid rgba(255,255,255,.55);background:rgba(0,0,0,.6);color:#fff;cursor:pointer;-webkit-tap-highlight-color:transparent}.cad-map .cad-side.nx{right:6px}.cad-map .cad-side.pv{left:6px;opacity:.75}.cad-map .cad-side b{font-size:17px;line-height:1}.cad-map .cad-side span{writing-mode:vertical-rl;font-size:16px;font-weight:800;letter-spacing:3px}.cad-map .cad-side:disabled{display:none}.cad-map .cad-side:active{background:rgba(255,210,63,.85);color:#111}.cad-map .hud.br{max-height:calc(100% - 104px)}.cad-sheet .acts button.warn{border-color:#e5484d!important;color:#e5484d!important}.cad-map.filling .hud,.cad-map.filling .cad-side,.cad-map.filling .cad-here,.cad-map.filling .cad-ph,.cad-map.filling .cad-msg{display:none!important}.cad-fill{position:absolute;left:6px;right:6px;top:6px;z-index:40;background:rgba(18,18,18,.92);color:#fff;border:1.5px solid #ffd23f;border-radius:14px;padding:8px 10px;display:flex;flex-direction:column;gap:6px}.cad-fill .ft{display:flex;align-items:baseline;gap:8px}.cad-fill .ft b{font-size:20px;color:#ffd23f}.cad-fill .ft span{font-size:13px;color:#ccc;flex:1}.cad-fill .ft .x{font:inherit;font-size:14px;padding:6px 10px;border-radius:10px;border:1px solid #888;background:transparent;color:#eee}.cad-fill .fl{font-size:13px;color:#ccc}.cad-fill .fc{display:flex;gap:6px;overflow-x:auto;padding-bottom:2px}.cad-fill .fc button{flex:none;min-width:46px;height:40px;font:inherit;font-size:17px;font-weight:800;border-radius:10px;border:1px solid #666;background:#2a2a2a;color:#fff}.cad-fill .fc button.on{background:#ffd23f;color:#111;border-color:#ffd23f}.cad-fill .fc button.on.g{background:transparent;color:#ffd23f;border:2px dashed #ffd23f}.cad-fill .fb{display:flex;align-items:center;gap:8px}.cad-fill .fb span{flex:1;font-size:13px;color:#ccc}.cad-fill .fb .ok{font:inherit;font-size:17px;font-weight:900;padding:10px 14px;border-radius:12px;border:0;background:#ffd23f;color:#111}@keyframes fpb{50%{opacity:.25}}.fillpt.blink{animation:fpb 1s infinite}.rsc .c{position:relative}.rsc .c em.q{position:absolute;top:-3px;right:-1px;font-style:normal;font-size:10px;font-weight:900;color:#e3a800}.cad-sheet .areas{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:8px 0}.cad-sheet .areas button{font:inherit;padding:14px 8px;border-radius:14px;border:1px solid var(--axis);background:var(--surface);color:var(--ink);display:flex;flex-direction:column;gap:4px;align-items:center;cursor:pointer}.cad-sheet .areas button b{font-size:26px;font-weight:900}.cad-sheet .areas button span{font-size:12px;color:var(--muted)}.cad-sheet .areas button.near{border:2px solid #e6b400}.cad-sheet .skip{font:inherit;width:100%;padding:12px;border-radius:12px;border:1px dashed var(--axis);background:transparent;color:var(--ink2);margin-top:4px;cursor:pointer}.cad-again{flex:0 0 92px;border:0;border-radius:12px;background:#2a2a2a;color:#ffd23f;font:inherit;display:flex;flex-direction:column;align-items:center;justify-content:center;line-height:1.1;cursor:pointer;border:1.5px solid #e6b400}.cad-again span{font-size:13px;color:#ddd}.cad-again b{font-size:20px;font-weight:900}.cad-view .cad-again{display:none}.hud.br .r.dsum{display:flex;align-items:center;gap:6px;width:100%;min-height:0!important;padding:3px 0;font:inherit;background:transparent;border:0;border-bottom:1px solid rgba(255,255,255,.25);color:#b6ff9c;cursor:pointer;text-align:left}.hud.br .r.dsum span{font-size:12px;color:#cfcfcf;white-space:nowrap}.hud.br .r.dsum b{font-size:14px;font-weight:800;min-width:0!important;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.hud.br .r.dsum em{font-style:normal;font-size:13px;color:#cfcfcf}.hud .r.wait{font-size:15px!important;color:#bbb!important;font-weight:600!important;min-height:0!important}.cad-hole{flex:1 1 0}.cad-hole b{font-size:22px}.cad-course{font-size:18px}.cad-map .cad-ph{position:absolute;top:64px;right:8px;z-index:3;font:inherit;font-size:13px;font-weight:700;padding:6px 12px;border-radius:16px;border:1px solid rgba(255,255,255,.5);background:rgba(0,0,0,.55);color:#fff}.cad-map .cad-attr{position:absolute;left:50%;transform:translateX(-50%);bottom:2px;font-size:9px;color:rgba(255,255,255,.75);text-shadow:0 0 2px #000;pointer-events:none;z-index:2}.cad-foot .cad-msg{top:0;bottom:auto;min-height:64px;box-sizing:border-box}.cad-map .cad-msg{position:absolute;top:auto;left:8px;right:146px;bottom:8px;min-height:0;z-index:30;font-size:14px;padding:7px 10px;max-height:55%;overflow:hidden;flex-wrap:wrap}.cad-map .cad-msg>span{flex:1 1 100%}.cad-again[hidden]{display:none!important}@media(min-width:900px){.cad-map .cad-msg{right:8px;max-width:520px}}
.cad-sheet .lies{display:flex;flex-wrap:wrap;gap:7px}.cad-sheet .lies button{font:inherit;font-size:16px;font-weight:700;height:44px;padding:0 13px;border-radius:22px;border:1px solid var(--border);background:var(--page);color:var(--ink);cursor:pointer}.cad-sheet .lies button.pen{color:#e03131;border-color:rgba(224,49,49,.55)}.cad-sheet .lies button.on{background:var(--ink);color:var(--page);border-color:var(--ink)}
.cad-sheet .clubs3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.cad-sheet .clubs3 button{font:inherit;height:60px;border-radius:12px;border:1px solid var(--border);background:var(--page);color:var(--ink);cursor:pointer;display:flex;flex-direction:column;align-items:center;justify-content:center;line-height:1.1;gap:2px}.cad-sheet .clubs3 b{font-size:25px;font-weight:800}.cad-sheet .clubs3 span{font-size:12px;color:#b88a00;font-weight:800}.cad-sheet .clubs3 .sg{border:3px solid #e6b400}.cad-sheet .clubs3 .on{background:var(--ink);color:var(--page);border-color:var(--ink)}.cad-sheet .del{font:inherit;font-size:17px;font-weight:800;width:100%;height:50px;margin-top:14px;border-radius:12px;border:2px solid #e03131;background:transparent;color:#e03131;cursor:pointer}
.cad-sheet .clist .cr{display:flex;align-items:center;gap:10px;padding:8px 0;border-top:1px solid var(--grid)}.cad-sheet .clist .n{font-size:18px;font-weight:800;min-width:86px}.cad-sheet .clist .bs{display:flex;flex-wrap:wrap;gap:6px}.cad-sheet .clist button{font:inherit;font-size:16px;font-weight:700;height:44px;min-width:56px;padding:0 12px;border-radius:10px;border:1px solid var(--border);background:var(--page);color:var(--ink);cursor:pointer}.cad-sheet .clist button.on{background:var(--ink);color:var(--page);border-color:var(--ink)}
.cad-sheet .owe{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;margin:8px 0;border-radius:10px;background:rgba(230,180,0,.16);font-weight:700}.cad-sheet .owe button,.cad-sheet .acts button{font:inherit;font-size:15px;font-weight:700;padding:9px 13px;border-radius:10px;border:1px solid var(--border);background:var(--page);color:var(--ink);cursor:pointer}.cad-sheet .acts{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.cad-sheet .shots{display:flex;flex-direction:column}.cad-sheet .shots button{font:inherit;display:flex;align-items:center;gap:10px;padding:11px 4px;border:0;border-top:1px solid var(--grid);background:none;color:var(--ink);text-align:left;cursor:pointer}.cad-sheet .shots i{font-style:normal;font-size:18px;color:#2fbf5b}.cad-sheet .shots b{font-size:18px;min-width:40px}.cad-sheet .shots .yd{font-weight:800;min-width:66px}.cad-sheet .shots .lie{flex:1;color:var(--muted);font-size:14px}.cad-sheet .shots .ed{color:var(--muted)}.cad-sheet .none{color:var(--muted);padding:6px 0}.cad-sheet table.cad-t{width:100%}.cad-sheet table.cad-t tr{cursor:pointer}@media(max-width:375px){.cad-step.nx{font-size:14px;padding:0 6px}.cad-top select{font-size:15px!important;padding-left:6px!important}.cad-hole b{font-size:18px}}.cad-sheet .hgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-top:10px}.cad-sheet .hgrid button{font:inherit;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;height:64px;border-radius:13px;border:1px solid var(--border);background:var(--page);color:var(--ink);cursor:pointer}.cad-sheet .hgrid button b{font-size:28px;font-weight:800;line-height:1}.cad-sheet .hgrid button span{font-size:13px;color:var(--muted)}.cad-sheet .hgrid button.done{border-color:#2fbf5b}.cad-sheet .hgrid button.done span{color:#2fbf5b;font-weight:700}.cad-sheet .hgrid button.on{background:var(--ink);color:var(--page);border-color:var(--ink)}.cad-sheet .hgrid button.on span{color:var(--page);opacity:.85}.cad-map .cad-run{position:absolute;right:10px;top:8px;z-index:3;height:auto;background:rgba(0,0,0,.6);border-radius:10px;padding:5px 10px;gap:10px;flex:none}.cad-map .cad-run:empty{display:none}.cad-map .cad-run u{color:#cfcfcf}.cad-map .cad-run b{color:#fff;font-size:17px}.cad-map .cad-run .t b{font-size:18px}.cad-top select{font:inherit;font-size:16px;font-weight:700;height:40px;padding:0 8px;border-radius:9px;border:1px solid var(--axis);background:var(--surface);color:var(--ink);min-width:0;flex:0 1 auto;max-width:46%}.cad-holes{display:flex;gap:6px;overflow-x:auto;padding-bottom:6px;-webkit-overflow-scrolling:touch}.cad-holes .fchip{flex:none;min-width:46px;text-align:center;font-size:16px;padding-top:8px;padding-bottom:8px}.cad-map{background:#0c0c0b;border-radius:10px;padding:6px;touch-action:pan-y pinch-zoom;will-change:transform;position:relative}.cad-peek{position:absolute;top:0;bottom:0;width:100%;box-sizing:border-box;background:#0c0c0b;border-radius:10px;padding:6px;pointer-events:none}.cad-peek.l{right:calc(100% + 8px)}.cad-peek.r{left:calc(100% + 8px)}.cad-wrap{overflow-x:clip}html{overscroll-behavior-x:none}.cad-map svg{display:block;width:100%;cursor:crosshair;overflow:hidden}.cad-map{overflow:hidden}.cad-cap{font-size:12px;color:#c3c2b7;text-align:center;padding:6px 0 2px}.cad-side .filters{margin:10px 0 0}.cad-res{border:1px solid var(--border);background:var(--surface);border-radius:12px;padding:14px;margin-top:10px}.cad-res .big{font-size:28px;font-weight:700;margin:2px 0 4px}.cad-res .ln{font-size:14px;color:var(--ink2);margin-top:4px;line-height:1.5}.cad-res .ln b{color:var(--ink);font-variant-numeric:tabular-nums}.cad-say{font-size:16px;font-weight:600;line-height:1.55;margin:6px 0 12px}.cad-big{border:2px solid var(--ink);border-radius:14px;padding:10px 14px;margin:6px 0 8px;background:var(--surface);color:var(--ink)}.cb-d{font-size:32px;font-weight:800;line-height:1.15;padding-bottom:4px}.cb-d small{font-size:16px;font-weight:600;color:var(--ink2);margin-left:10px}.cb-row{display:flex;align-items:center;gap:10px;font-size:23px;font-weight:700;padding:5px 0;border-top:1px solid var(--grid)}.cb-row i{font-style:normal;flex:none;width:28px;height:28px;border-radius:50%;background:#ffd23f;color:#111;font-size:16px;font-weight:800;display:inline-flex;align-items:center;justify-content:center}.cb-row span{margin-left:auto;font-variant-numeric:tabular-nums}.cb-tot{font-size:23px;font-weight:800;padding-top:6px;border-top:2px solid var(--ink);text-align:right}.cad-more{margin-top:10px}.cad-more>summary{font-size:14px;color:var(--ink2);cursor:pointer;padding:6px 2px}.cad-steps{display:grid;grid-template-columns:22px max-content max-content minmax(0,1fr);gap:8px 12px;align-items:center;font-size:15px;margin:4px 0 10px}.cad-steps i{font-style:normal;width:22px;height:22px;border-radius:50%;background:#ffd23f;color:#111;font-size:12px;font-weight:700;display:inline-flex;align-items:center;justify-content:center}.cad-steps i.pt{background:var(--grid);color:var(--ink2)}.cad-steps span{font-variant-numeric:tabular-nums}.cad-steps .br{grid-column:2 / -1;font-size:13px;color:var(--ink2);line-height:1.6;margin:-4px 0 4px;padding-left:10px;border-left:2px solid var(--grid)}.cad-steps .br b{font-weight:600;color:var(--ink)}.cad-steps .rem{color:var(--ink2);font-size:13px;min-width:0}.cad-det{margin-top:10px}.cad-doc{display:block;margin:14px 2px 0;font-size:13px;color:var(--ink2)}.cad-det summary{font-size:13px;color:var(--ink2);cursor:pointer}.cad-res .ln.warn{color:var(--trend)}table.cad-t{width:100%;border-collapse:collapse;margin-top:12px;font-size:13px;font-variant-numeric:tabular-nums}table.cad-t th{font-weight:400;color:var(--muted);text-align:right;padding:4px 4px;border-bottom:1px solid var(--axis)}table.cad-t td{text-align:right;padding:6px 4px;border-bottom:1px solid var(--grid)}table.cad-t th:first-child,table.cad-t td:first-child{text-align:left}table.cad-t tr.on td{font-weight:600}@media(min-width:900px){.cad-wrap{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:20px;align-items:start}body:has(.cad-wrap) .wrap{max-width:1500px}.cad-wrap>.cad-side{display:flex;flex-direction:column;justify-content:flex-end;gap:12px}.cad-wrap>.cad-side>.cad-foot{margin-top:auto}.cad-wrap>.cad-side>.cad-gps{margin:0}.cad-bar.pc{display:none}.cad-wrap{grid-template-columns:minmax(0,1fr) 380px}.cad-wrap>.cad-side{gap:0}.cad-wrap>.cad-side>.cad-plan::before,.cad-wrap>.cad-side>.cad-foot::before{content:attr(data-lb);display:block;font-size:12px;font-weight:700;letter-spacing:2px;color:var(--muted);margin:0 0 6px 2px;width:100%}.cad-wrap>.cad-side>.cad-foot{flex-wrap:wrap;margin:auto 0 12px}.cad-wrap>.cad-side>.cad-foot>.cad-card{flex:1 1 0}}.cad-plan .hud.br{max-height:calc(100% - 76px);overflow-y:auto;overscroll-behavior:contain;right:8px;bottom:8px;background:rgba(0,0,0,.66);border-radius:12px;padding:7px 10px;min-width:118px;max-width:62%;pointer-events:auto}.cad-plan .hud.br .hd{display:flex;flex-direction:column;line-height:1.15;margin-bottom:3px}.cad-plan .hud.br .hd span{font-size:12px;color:#d8d8d8}.cad-plan .hud.br .hd b{font-size:19px;color:#ffd23f;font-weight:900}.cad-plan .hud.br .alt{font-size:12px;color:#cfcfcf;margin-bottom:3px}.cad-plan .hud.br .alt.go{color:#ff8a5c}.cad-plan .hud.br .r{display:flex;align-items:baseline;gap:6px;font:inherit;width:100%;background:none;border:0;padding:2px 0;text-align:left;font-size:18px;line-height:1.25}.cad-plan .hud.br button.r{cursor:pointer}.cad-plan .hud.br .r i{font-style:normal;font-size:15px;width:18px}.cad-plan .hud.br .r b{font-weight:900;min-width:30px}.cad-plan .hud.br .r span{font-weight:900;font-variant-numeric:tabular-nums}.cad-plan .hud.br .r em{font-style:normal;font-size:12px;font-weight:700}.cad-plan .hud.br .done{color:#b6ff9c}.cad-plan .hud.br .done em{color:#ff9a9a}.cad-plan .hud.br .plan{color:#ffd23f}.cad-plan .hud.br .plan em{color:#ff8a5c}.cad-plan .hud.br button.r.done{min-height:40px;font-size:21px;padding:4px 2px;align-items:center;border-bottom:1px solid rgba(255,255,255,.14)}.cad-plan .hud.br button.r.done:last-of-type{border-bottom:0}.cad-plan .hud.br .hd.fin{margin:6px 0 0;padding-top:5px;border-top:1px solid rgba(255,255,255,.3)}.cad-plan .hud.br .hd.fin span{color:#d8d8d8}.cad-plan .hud.br .hd.fin b{color:#b6ff9c}.cad-plan .hud.br .lb{font-size:11px;color:#9a9a9a;margin:4px 0 0;letter-spacing:1px}.cad-plan .hud.br .r small{font-size:11px;font-weight:700;color:#cfcfcf}.cad-plan .hud.br{max-height:calc(100% - max(102px,calc(50% - 64px)) - 144px)}.cad-plan .hud.br{position:static;min-height:180px;max-height:none;max-width:none;min-width:0;width:100%;box-sizing:border-box;border-radius:14px;padding:16px 18px;background:#141412;color:#fff;overflow:visible}.cad-plan .hud.br .hd span{font-size:15px}.cad-plan .hud.br .hd b{font-size:30px}.cad-plan .hud.br .alt{font-size:15px}.cad-plan .hud.br .r{font-size:26px;padding:5px 0}.cad-plan .hud.br .r i{font-size:20px;width:26px}.cad-plan .hud.br .r b{min-width:48px}.cad-plan .hud.br button.r.done{font-size:26px;min-height:50px}.cad-plan .hud.br .lb{font-size:13px}@media(min-width:900px){.cad-wrap>.cad-side>.cad-top{margin:0 0 18px;height:56px}.cad-wrap>.cad-side .cad-add{height:72px;font-size:22px}}.pmeta{margin:28px 0 0;color:var(--muted);font-size:11px;text-align:center}section.solo>h2{display:none}
/*r4*/.cad-gps[hidden]{display:none!important}.cad-map .cad-here{top:8px;right:8px;width:78px;height:44px;padding:0;justify-content:center;border-radius:12px;font-size:15px;gap:6px}.cad-map .cad-ph{top:60px;right:8px;width:78px;height:44px;padding:0;border-radius:12px;font-size:15px;font-weight:800;display:flex;align-items:center;justify-content:center;border:1.5px solid rgba(255,255,255,.55);background:rgba(0,0,0,.66);color:#fff}.cad-map .cad-side.nx{top:112px;right:8px;width:78px;height:70px;flex-direction:column;gap:3px;border-radius:12px;background:rgba(0,0,0,.72);border-color:rgba(255,255,255,.75)}.cad-map .cad-side.nx span{writing-mode:horizontal-tb;letter-spacing:0;font-size:17px}.cad-map .cad-side.nx b{font-size:17px}.cad-map .cad-side.pv{top:112px;height:96px}.cad-map .hud.br{max-height:none}.cad-map .hud.br .r span.w{font-size:15px;font-weight:800}.cad-map .hud.br .r em.hz{font-size:16px;font-weight:900;color:#ff7b7b;font-style:normal}.cad-top{height:44px;gap:6px;margin:0 0 6px}.cad-top .cad-mode{flex:0 0 54px;font:inherit;font-size:15px;font-weight:800;border-radius:11px;border:1px solid var(--axis);background:var(--surface);color:var(--ink);cursor:pointer;padding:0}.cad-top .cad-mode.sim{border:2px solid #2f8cff;color:#2f8cff;background:rgba(47,140,255,.1)}.cad-top .cad-course{flex:2 1 0;padding:0 6px}.cad-top .cad-course b{font-size:16px}.cad-top .cad-hole{flex:1.35 1 0;padding:0 6px;border-width:1px}.cad-top .cad-hole b{font-size:17px}.cad-sheet .atabs{display:flex;gap:6px;margin:6px 0 4px;flex-wrap:wrap}.cad-sheet .atabs button{font:inherit;font-size:16px;font-weight:800;height:40px;padding:0 16px;border-radius:20px;border:1px solid var(--border);background:var(--page);color:var(--ink);cursor:pointer}.cad-sheet .atabs button.on{background:var(--ink);color:var(--page);border-color:var(--ink)}.cad-sheet .ok.ghost{background:transparent;color:var(--ink);border:1px solid var(--axis);margin-top:8px}.cad-sheet .simrow{background:rgba(47,140,255,.14)}.cad-sim .cad-map{-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}.cad-sheet .hd{align-items:center}.cad-sheet .hd .x{font-size:30px;line-height:1;width:52px;height:52px;padding:0;margin:-8px -8px -8px 0;border-radius:26px;color:var(--ink2);flex:none}.cad-sheet .lies{margin-bottom:7px}.cad-sheet .lies button{height:42px;padding:0 14px}.cad-sheet .clubs3{grid-template-columns:repeat(6,1fr);gap:6px}.cad-sheet .clubs3 button{height:50px;border-radius:10px;padding:0}.cad-sheet .clubs3 b{font-size:19px}.cad-sheet .clubs3 span{font-size:10px}.cad-sheet .acts3{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:14px}.cad-sheet .acts3 button{font:inherit;font-size:15px;font-weight:800;height:50px;border-radius:12px;border:1px solid var(--axis);background:var(--page);color:var(--ink);cursor:pointer;padding:0 4px}.cad-sheet .del{margin-top:9px;height:46px}.cad-fill .fb .no{font:inherit;font-size:17px;font-weight:800;padding:10px 14px;border-radius:12px;border:1px solid #888;background:transparent;color:#eee}.cad-sheet .shots .yd.hz{color:#e5484d}.cad-fill .fc{display:grid;grid-template-columns:repeat(6,1fr);gap:5px;overflow:visible}.cad-fill .fc button{min-width:0;height:38px;font-size:15px;padding:0}.cad-sheet .lies{flex-wrap:nowrap;gap:6px}.cad-sheet .lies button{flex:1 1 auto;padding:0 6px;font-size:15px;white-space:nowrap;min-width:0}.cad-sheet .acts3{grid-template-columns:1fr 1fr 1fr .8fr;gap:6px}.cad-sheet .acts3 button{font-size:14.5px;padding:0 2px}.cad-sheet .acts3 button.dl{border:1.5px solid #e03131;color:#e03131;background:transparent}.cad-map .cad-here{top:auto;bottom:60px;left:8px;right:auto}.cad-map .cad-ph{top:auto;bottom:8px;left:8px;right:auto}.cad-map .cad-side.pv{top:8px;left:auto;right:8px;width:78px;height:48px;flex-direction:row;gap:4px;opacity:1;border-radius:12px;background:rgba(0,0,0,.6)}.cad-map .cad-side.pv b{font-size:12px}.cad-map .cad-side.pv span{writing-mode:horizontal-tb;letter-spacing:0;font-size:14px;font-weight:700}.cad-map .cad-side.nx{top:64px;height:88px}.cad-map .hud.bl{bottom:112px}.cad-map .cad-rs,.cad-map .hud.bl:not([hidden]) ~ .cad-rs{left:94px;bottom:8px}.cad-map .cad-msg{top:112px;bottom:auto;left:8px;right:94px;max-height:40%}@media(min-width:900px){.cad-map .cad-msg{right:94px}}.cad-top .cad-hole{order:0}.cad-top .cad-course{order:1}.cad-top .cad-mode{order:2}.cad-top{height:42px!important;margin:0 0 6px!important}.cad-course{order:0!important;flex:1 1 0!important}.cad-mode{order:1!important}.cad-nav{display:flex;gap:6px;height:58px;margin:0 0 7px}.cad-nav .cad-hole{flex:1 1 0;order:0;flex-direction:column;gap:1px;border-radius:12px;padding:0 4px}.cad-nav .cad-hole b{font-size:20px;line-height:1.1}.cad-nav .cad-hole small{font-size:14px;font-weight:700;opacity:.8}.cad-nb{flex:0 0 76px;font:inherit;display:flex;align-items:center;justify-content:center;gap:4px;border-radius:12px;border:1px solid var(--axis);background:var(--surface);color:var(--ink);cursor:pointer;padding:0;font-size:15px;font-weight:800}.cad-nb b{font-size:13px}.cad-nav .cad-nb:disabled{opacity:.25;cursor:default}.cad-nb:active{background:#e3b100;color:#111}.cad-nb.pv{opacity:.8}.cad-map .cad-gps{position:absolute;top:8px;right:8px;z-index:4;margin:0;display:flex}.cad-map .cad-gps .cad-add{flex:none;width:86px;height:86px;padding:0;border-radius:16px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;box-shadow:0 2px 8px rgba(0,0,0,.45);white-space:nowrap}.cad-map .cad-gps .cad-add b{font-size:38px;line-height:.9;font-weight:900}.cad-map .cad-gps .cad-add span{font-size:16px;font-weight:800}.cad-nav .cad-hole{flex-direction:row!important;gap:14px!important}.cad-nav .cad-hole .hn{display:flex;align-items:baseline;gap:6px}.cad-nav .cad-hole .hn b{font-size:36px;line-height:1;font-weight:900}.cad-nav .cad-hole .hn i{font-style:normal;font-size:14px;font-weight:800;opacity:.75}.cad-nav .cad-hole .hm{display:flex;flex-direction:column;align-items:flex-start;line-height:1.1;padding-left:14px;border-left:1.5px solid currentColor}.cad-nav .cad-hole .hm b{font-size:18px;font-weight:800}.cad-sheet .clist button.cfg{min-width:96px;font-size:17px}.cad-sheet .combo{padding:6px 0 12px;border-top:1px dashed var(--grid)}.cad-sheet .combo .cbr{display:flex;align-items:center;gap:10px;margin:6px 0}.cad-sheet .combo .cl{flex:0 0 52px;font-size:15px;color:var(--muted);font-weight:700}.cad-sheet .combo .cg{display:flex;gap:8px;flex:1}.cad-sheet .combo .cg button{flex:1;height:46px;font-size:19px;font-weight:800}.cad-sheet .combo .cg button:disabled{opacity:.3}.cad-sheet .combo .now .cg button{height:56px;font-size:18px;border:2px solid #e6b400}.cad-sheet .combo .now .cg button.on{background:#e6b400;color:#111}.cad-top .cad-seg{display:flex;flex:0 0 auto;border:1px solid var(--axis);border-radius:11px;overflow:hidden;order:1}.cad-top .cad-seg[hidden]{display:none}.cad-top .cad-seg button{font:inherit;min-width:50px;padding:0 10px;border:0;background:var(--surface);color:var(--muted);font-size:20px;font-weight:900;cursor:pointer}.cad-top .cad-seg button+button{border-left:1px solid var(--axis)}.cad-top .cad-seg button.on{background:var(--ink);color:var(--page)}.cad-top .cad-seg button.q{color:var(--muted);font-weight:700}.cad-top .cad-mode{order:2!important}.cad-sheet .clist .cr.tap{cursor:pointer}.cad-sheet .combo{padding:4px 0 12px}.cad-sheet .combo .cbr{display:flex;align-items:center;gap:10px;margin:7px 0}.cad-sheet .combo .cl{flex:0 0 44px;font-size:15px;color:var(--muted);font-weight:700}.cad-sheet .combo .cg{display:flex;gap:8px;flex:1}.cad-sheet .combo .cg button{flex:1;min-width:0;height:46px;font-size:19px;font-weight:900;padding:0}.cad-sheet .combo .cg button.later{font-size:14px;font-weight:700}.cad-sheet .combo .cg button:disabled{opacity:.25}.cad-sheet .combo .ok.go{display:block;width:100%;height:52px;margin-top:10px;border:0;border-radius:12px;background:#e6b400;color:#111;font:inherit;font-size:18px;font-weight:900}.cad-sheet .combo .ok.go:disabled{opacity:.35}.cad-sheet .clist .hint{font-size:15px;color:var(--muted);font-weight:700}.cad-msg{cursor:pointer;padding-right:34px!important}.cad-msg:not(:empty)::after{content:"✕";position:absolute;right:10px;top:50%;transform:translateY(-50%);font-size:18px;font-weight:700;color:var(--muted)}.cad-sheet .hd .bk{font:inherit;font-size:30px;line-height:1;border:0;background:none;color:var(--ink);padding:0 10px 0 0;cursor:pointer}.cad-sheet .combo .cname{flex:1;height:50px;display:flex;align-items:center;justify-content:center;gap:8px;font:inherit;font-size:20px;font-weight:900;border-radius:12px;border:1px solid var(--axis);background:var(--surface);color:var(--ink);cursor:pointer}.cad-sheet .combo .cname i{font-style:normal;font-size:14px;color:var(--muted)}.cad-sheet .combo .note{color:var(--muted);font-size:14px;margin:10px 2px}.cad-sheet .clist .cr.tap{justify-content:space-between}.cad-sheet .clist .cr.on .n{color:#e6b400}.cad-sheet .combo .cg button{font:inherit;font-size:19px;font-weight:900;height:46px;border:1px solid var(--axis);background:var(--page);color:var(--ink);border-radius:10px;cursor:pointer}.cad-sheet .combo .cg button.on{background:var(--ink);color:var(--page);border-color:var(--ink)}/*r4end*/
nav{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 8px}
nav a{font-size:13px;color:var(--ink2);text-decoration:none;border:1px solid var(--border);border-radius:999px;padding:4px 12px;background:var(--surface)}
nav a:hover{color:var(--ink)}
section{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px 20px 16px;margin-top:20px;position:relative}
section h2{font-size:18px;font-weight:600;margin:0 0 4px}
.note{color:var(--ink2);font-size:13px;margin:0 0 12px;max-width:70ch}
.chart{position:relative;width:100%}
.chart svg{display:block;width:100%;height:auto}
.tick,.xl,.xn{font-size:12px;fill:var(--muted)}
.xl{fill:var(--ink);font-size:13px}
.tip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--axis);border-radius:8px;padding:8px 10px;font-size:12px;line-height:1.5;min-width:150px;display:none;font-variant-numeric:tabular-nums;box-shadow:0 2px 8px rgba(0,0,0,.12)}
.tip b{font-size:13px;font-weight:600}
.tip div{display:flex;justify-content:space-between;gap:16px;color:var(--ink2)}
.tip div span:last-child{color:var(--ink)}
select{font:inherit;font-size:14px;padding:6px 10px;border:1px solid var(--axis);border-radius:8px;background:var(--surface);color:var(--ink);margin-bottom:8px;max-width:100%}
.rt{width:100%}.rt td,.rt th{text-align:center;padding:6px 8px}.rt td.shots{text-align:left}
.chip{display:inline-block;border:1px solid var(--border);border-radius:6px;padding:1px 7px;margin:2px 4px 2px 0;font-size:12px;white-space:nowrap}.chip b{font-weight:500}.chip span{color:var(--ink2)}.chip.miss{border-style:dashed;color:var(--ink3,#999)}.chip.site{border-color:#3fae5a;border-width:1.5px}.chip.bad{box-shadow:0 0 0 2px #e5484d}b.mist{color:#e5484d;font-weight:800}.chip i.hz{font-style:normal;font-weight:700;margin-left:4px;padding:0 4px;border-radius:4px;background:#2f7fd6;color:#fff;font-size:11px}.chip i.hz.ob{background:#d64545}.chip.miss b{font-weight:700}.chip.pen{border-color:#4a90d9}
.rt td.bad{color:#d03b3b;font-weight:600}.rt td.good{color:#0c8a0c;font-weight:600}.rt tr.tot td{font-weight:600;border-top:1px solid var(--axis)}.rt tr.nine td{border-bottom:2px solid var(--axis);background:color-mix(in srgb,var(--ink) 6%,transparent)}
.rl{width:100%}.rl th,.rl td{text-align:center;padding:8px 8px}.rl td.course,.rl th:nth-child(2){text-align:left}.rl td.strong{font-weight:600}.rl td.zero{color:var(--up);font-weight:600}
.rl tr.row{cursor:pointer}.rl tr.row:hover{background:var(--page)}.rl tr.more{display:none}.rl.all tr.more{display:table-row}.rl td.arrow{color:var(--muted);width:28px}
.rl tr.detail>td{padding:4px 0 16px 24px;background:var(--page)}.tag{white-space:nowrap;font-size:11px;color:var(--ink2);border:1px solid var(--border);border-radius:999px;padding:0 7px;margin-left:8px}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 10px}
.fchip{font:inherit;font-size:13px;padding:4px 12px;border:1px solid var(--axis);border-radius:999px;background:transparent;color:var(--muted);cursor:pointer}
.fchip.on{background:var(--ink);color:var(--surface);border-color:var(--ink)}
.morebtn{font:inherit;font-size:13px;margin-top:10px;padding:6px 14px;border:1px solid var(--axis);border-radius:999px;background:var(--surface);color:var(--ink);cursor:pointer}
.morelink{margin:24px 0 0;font-size:14px}.morelink a{color:var(--series)}
.read{font-size:13px;color:var(--ink2);text-align:right;min-height:20px;font-variant-numeric:tabular-nums}
.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;color:var(--ink2);margin:4px 0 0}
.legend span{display:inline-flex;align-items:center;gap:6px}
details{margin-top:12px;font-size:13px}
summary{cursor:pointer;color:var(--ink2)}
table{border-collapse:collapse;margin-top:8px;font-variant-numeric:tabular-nums}
th,td{padding:4px 12px;text-align:right;border-bottom:1px solid var(--grid)}
th:first-child,td:first-child{text-align:left}
th{color:var(--ink2);font-weight:500}
.scname{font-size:13px;color:var(--ink2);padding:8px 0 0}
.sc{max-width:600px;padding:6px 8px 0 0}
.scg{display:grid;grid-template-columns:repeat(9,1fr) 1.25fr;justify-items:center;align-items:center;row-gap:4px;padding:10px 0 8px;border-bottom:1px solid var(--grid)}
.scg .hn{color:var(--muted);font-size:13px;white-space:nowrap}.scg .pr{color:var(--ink2);font-size:13px;margin-bottom:6px}
.scg .s{display:flex;flex-direction:column;align-items:center}
.m{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;font-size:16px;font-weight:600;font-style:normal;border:2px solid transparent;font-variant-numeric:tabular-nums}
.m.bogey{border-color:#d9a13a}.m.dbl{border-color:var(--down);outline:2px solid var(--down);outline-offset:2px}
.m.blow{background:var(--down);border-color:var(--down);color:#fff;outline:2px solid var(--down);outline-offset:2px}
.m.birdie{border-color:var(--up);border-radius:50%}.m.eagle{border-color:var(--up);border-radius:50%;outline:2px solid var(--up);outline-offset:2px}
.m.tot{width:auto;font-size:17px}
.dots{font-size:12px;line-height:1;height:12px;letter-spacing:1px;color:var(--ink2);margin-top:7px;white-space:nowrap}
.dots.three,.sclg b.three{color:var(--down);font-weight:700}.sclg b.three{letter-spacing:1px}
.sclg{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;color:var(--ink2);padding:10px 0 2px}.sclg span{display:inline-flex;align-items:center;gap:7px}.sclg .m{width:12px;height:12px;border-width:1.5px;outline-width:1.5px;outline-offset:1.5px}
.tabs{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 4px}.dtabs{flex-wrap:nowrap;gap:6px}.dtabs .fchip{flex:1 1 0;min-width:0;padding-left:0;padding-right:0;text-align:center;white-space:nowrap}.chart .legend svg,.chart .sclg svg{display:inline-block;width:auto;height:auto;flex:none}.panel .legend{margin-top:10px}.panel{padding:6px 0 4px}
.maps{display:grid;gap:10px;margin-top:8px}.maps figure{margin:0;background:#0c0c0b;border-radius:10px;padding:6px 4px 4px;min-width:0}.maps figcaption{font-size:12px;color:#c3c2b7;text-align:center;padding-bottom:2px;white-space:nowrap}
.maps svg{display:block;width:100%}.maps figure{position:relative}.maps .mtl{position:absolute;left:10px;top:8px;display:flex;flex-direction:column;color:#fff;text-shadow:0 1px 3px #000,0 0 2px #000;pointer-events:none;line-height:1.1}.maps .mtl .z{font-size:13px;color:#d9d9d9}.maps .mtl b{font-size:28px;font-weight:900}.maps .mtl span{font-size:15px;font-weight:700}.maps .mbr{position:absolute;right:8px;bottom:8px;background:rgba(0,0,0,.66);border-radius:10px;padding:6px 10px;color:#fff;min-width:96px}.maps .mbr .hd{display:flex;flex-direction:column;border-bottom:1px solid rgba(255,255,255,.25);padding-bottom:3px;margin-bottom:3px}.maps .mbr .hd b{font-size:18px;color:#fff}.maps .mbr .hd span{font-size:12px;color:#ddd}.maps .mbr .r{display:flex;align-items:baseline;gap:6px;font-size:17px;font-weight:800;line-height:1.35}.maps .mbr .r i{font-style:normal;font-size:14px;color:#ccc}.maps .mbr .r span{margin-left:auto}.maps .mbr .r.site i{color:#5fd07a}.maps .mbr .r em.hz{font-style:normal;font-size:12px;font-weight:800;padding:1px 5px;border-radius:5px;background:#2f7fd6;color:#fff;margin-left:4px}.maps .mbr .r em.hz.ob{background:#d64545}.maps .mbr .r.miss b{color:#bbb;font-weight:600;font-size:14px}.maps.strip{width:calc(100vw - 46px);display:flex;overflow-x:auto;-webkit-overflow-scrolling:touch;scroll-snap-type:x mandatory;gap:8px;padding-bottom:6px}.maps.strip figure{flex:0 0 calc((100% - (var(--per) - 1) * 8px) / var(--per));scroll-snap-align:start}.sub{display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:12px;color:var(--ink2);margin-top:6px}.sub .fchip{font-size:12px;padding:2px 10px}
.ptabs{display:flex;margin:16px 0 4px;border:1px solid var(--border);border-radius:12px;background:var(--surface);padding:4px;gap:4px}.ptabs a{flex:1;text-align:center;font-size:15px;font-weight:600;color:var(--ink2);text-decoration:none;padding:9px 6px;border-radius:9px;white-space:nowrap}.ptabs a.on{background:var(--ink);color:var(--surface)}
nav{margin:10px 0 0}nav a{font-size:12px;padding:3px 10px}
.morelink{display:block;margin:20px 0 0;font-size:15px}.morelink a{display:inline-block;padding:8px 16px;border:1px solid var(--axis);border-radius:999px;color:var(--ink);text-decoration:none}
.maps.strip.big{width:auto}.maps.strip.big figure{padding:10px 8px 8px}.maps.strip.big figcaption{font-size:15px;font-weight:600;color:#f0efec}
.cstat{display:flex;flex-wrap:wrap;justify-content:center;gap:4px 14px;font-size:12px;color:#c3c2b7;padding:2px 0 6px}.cstat b{color:#f0efec;font-weight:600}
.cplays{width:100%;margin-top:6px;font-size:12px;color:#c3c2b7}.cplays td{padding:3px 4px;text-align:center;border-bottom:1px solid #2c2c2a;white-space:nowrap}.cplays td.bad{color:#f06a6a;font-weight:600}.cplays td.good{color:#2fbf4f;font-weight:600}
.sub.holes .fchip{min-width:34px;padding:2px 6px}
.plan{max-width:560px;margin:0 auto}.plan .lead{color:var(--ink2);font-size:14px;margin:2px 0 14px}.plan h3{font-size:15px;margin:26px 0 10px;color:var(--ink)}
.pgoal{border:1px solid var(--border);border-radius:14px;padding:18px 14px;text-align:center;background:var(--page)}.pgoal .big{font-size:44px;font-weight:700;line-height:1.15;margin:4px 0 6px}.plan .k{font-size:13px;color:var(--ink2)}
.ptiles{display:grid;grid-template-columns:1fr 1fr;gap:10px}.ptile{border:1px solid var(--border);border-radius:12px;padding:12px 10px;text-align:center;background:var(--page)}.ptile .v{font-size:24px;font-weight:700;margin:2px 0}
.ptab{width:100%;margin-top:12px;font-size:14px;font-variant-numeric:tabular-nums}.ptab th{font-size:12px;color:var(--muted);font-weight:500}.ptab th,.ptab td{text-align:center;padding:7px 4px;border-bottom:1px solid var(--grid)}.ptab td.ok{color:var(--up);font-weight:700}.ptab td.no{color:var(--ink2)}.ptab td.strong{font-weight:700;font-size:16px}
.plan .cap,.panel .cap{font-size:12px;color:var(--muted);margin-top:6px;text-align:center}
.pcard{display:flex;gap:12px;border:1px solid var(--border);border-radius:12px;padding:12px 14px;margin-bottom:10px;background:var(--page)}.pcard .num{flex:none;width:28px;height:28px;border-radius:50%;background:var(--ink);color:var(--surface);display:flex;align-items:center;justify-content:center;font-size:15px}
.pcard .t{font-size:17px;font-weight:700}.pcard .f{font-size:13px;color:var(--trend);margin:2px 0 4px}.pcard .b{font-size:14px;color:var(--ink2);line-height:1.6}.pcard.rule{border-color:var(--series)}.pcard .tg{color:var(--up)}.pcard .f{color:var(--muted)}.pcard ol{margin:0;padding-left:22px;font-size:17px;font-weight:600;line-height:2}
.plater{font-size:15px;color:var(--ink2);line-height:1.9;margin:0;padding-left:22px}.plan .lead+h3{margin-top:8px}

.ptabs{position:sticky;top:0;z-index:30}
.rhd{display:flex;justify-content:space-between;align-items:baseline;font-size:13px;color:var(--ink2);margin:2px 2px 8px}.rhd b{font-size:24px;color:var(--ink)}.r5{display:flex;align-items:center;gap:10px;margin:2px 2px 12px;font-size:13px;color:var(--ink2);white-space:nowrap}.r5::before,.r5::after{content:"";flex:1;height:1px;background:var(--axis)}.r5 b{font-size:20px;color:var(--ink);font-variant-numeric:tabular-nums}
.r2{display:grid;grid-template-columns:372px minmax(0,1fr);gap:20px;align-items:start}.r2 .rlist{position:sticky;top:64px;max-height:calc(100vh - 84px);overflow-y:auto;padding-right:6px}.rpane{min-width:0;border:1px solid var(--border);border-radius:14px;padding:6px 18px 16px;background:var(--surface)}
.rmonth{font-size:12px;color:var(--muted);padding:6px 4px;position:sticky;top:0;background:var(--surface);z-index:5}
.rcard{border:1px solid var(--border);border-radius:14px;padding:12px 14px 10px;margin-bottom:10px;background:var(--page);cursor:pointer;outline:none}.rcard:hover,.rcard:focus-visible{border-color:var(--axis)}.rcard.on{border-color:var(--ink);box-shadow:inset 0 0 0 1px var(--ink)}
.rc1{display:flex;justify-content:space-between;align-items:center;gap:10px}.rttl{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 8px;min-width:0}.rdate{font-size:14px;color:var(--muted);font-variant-numeric:tabular-nums}.rname{font-size:19px;font-weight:700;line-height:1.25}.rsub{font-size:12px;color:var(--muted)}
.rtot{white-space:nowrap;line-height:1}.rtot b{font-size:36px;font-weight:700;font-variant-numeric:tabular-nums}.rttl{flex:1}.rstk{flex:none;font-size:12px;color:var(--muted);line-height:1.45;white-space:nowrap;font-variant-numeric:tabular-nums}.rstk b{font-size:15px;color:var(--ink)}.rstk{text-align:right}.rtot{min-width:88px;text-align:right}.rtot i{display:inline-block;width:16px;font-style:normal;font-size:13px;text-align:right;vertical-align:9px}.rtot i.dn,.rtot b.dn,.rlg b.dn{color:var(--up)}.rlg b.p3,.rsum span.p3{color:var(--down)}.rtot i.up,.rtot b.up,.rlg b.up{color:var(--down)}
.rc2{display:flex;gap:18px;margin-top:2px;font-size:13px;color:var(--muted)}.rc2 b{font-size:16px;color:var(--ink);font-variant-numeric:tabular-nums}.rcard b.ok{color:var(--up)}
.rsc{display:flex;gap:2px;margin-top:10px}.rsc .c{flex:1;min-width:0;text-align:center}.rsc .c.gap{margin-left:8px}
.rsc b{display:block;box-sizing:border-box;width:100%;max-width:21px;aspect-ratio:1;margin:0 auto;font-size:12px;font-weight:700;line-height:1;display:flex;align-items:center;justify-content:center;font-variant-numeric:tabular-nums;border:1.5px solid transparent;border-radius:2px;color:var(--ink)}
.rsc b{font-size:13px}.rsc .lb{flex:0 0 11px;margin-right:1px}.rsc .lb b{color:var(--muted);font-weight:700;font-size:14px}.rsc b.p{color:var(--up);font-weight:800;font-size:13px}.rsc b.b{color:#eda100;font-weight:800;font-size:13px}.rsc b.g{color:var(--ink2)}
.rsc b.d{color:#ef9f27;font-weight:800}.rsc b.x{background:var(--down);border-color:var(--down);color:var(--page);font-weight:800}
.rsc i{display:block;font-style:normal;font-size:9px;line-height:9px;height:9px;letter-spacing:-1.5px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:visible;text-indent:-1.5px}.rsc i.t{color:var(--down)}.rsc u{display:block;text-decoration:none;font-size:10px;line-height:12px;color:var(--muted);opacity:.8;font-variant-numeric:tabular-nums;margin-bottom:2px}
.rlg i.p{background:none}.rlg span.k{display:inline-flex;align-items:center;justify-content:center;width:17px;height:17px;letter-spacing:-.6px;box-sizing:border-box;font-size:10px;font-weight:700;margin-right:5px;border:1.5px solid transparent;border-radius:2px;vertical-align:-3px;color:var(--ink)}
.rlg span.k.p{color:var(--up)}.rlg span.k.b{color:#eda100}.rlg span.k.g{color:var(--ink2)}.rlg span.k.d{color:#ef9f27}.rlg span.k.x{background:var(--down);border-color:var(--down);color:var(--page)}
.rbars{display:flex;gap:3px;align-items:flex-end;height:46px;padding-bottom:8px;margin-top:6px;box-sizing:border-box}.rbars i,.rdots i{flex:1;display:block;min-width:0}.rbars i.gap,.rdots i.gap{margin-left:9px}
.rbars i{border-radius:2px 2px 0 0}.rbars i.p{background:none}.rbars i.p::after{content:"";display:block;width:min(100%,13px);aspect-ratio:1;border-radius:50%;background:var(--up);margin:0 auto}.rlg i.p{background:var(--up);border-radius:50%}.rbars i.g,.rlg i.g{background:var(--muted)}.rbars i.d,.rlg i.d{background:#ef9f27}.rbars i.x,.rlg i.x{background:var(--down)}.rbars i.x{font-style:normal;font-size:10px;font-weight:700;line-height:15px;text-align:center;color:var(--page);font-variant-numeric:tabular-nums;letter-spacing:-.5px;overflow:visible;white-space:nowrap}.rbars i.b,.rlg i.b{background:none;font-style:normal;color:#eda100;text-align:center;overflow:visible}.rbars i.b{font-size:19px;line-height:17px;white-space:nowrap;text-indent:-3px}.rlg i.b{font-size:14px;line-height:10px;width:auto;height:auto;vertical-align:0}
.rdots{display:flex;gap:3px;height:13px;margin-top:2px}.rdots i{font-style:normal;font-size:11px;line-height:13px;text-align:center;color:var(--muted);font-variant-numeric:tabular-nums;opacity:.75}.rdots i.d{color:var(--down);font-weight:800;opacity:1}
.rsum{display:flex;align-items:baseline;margin-top:6px;font-size:12px;color:var(--muted)}.rsum b{font-size:15px;color:var(--ink);font-variant-numeric:tabular-nums}.rsum .rs{flex:1;white-space:nowrap}.rsum .rs:last-child{text-align:right}.rsum .mid{display:flex;gap:14px;white-space:nowrap}.rsum .mid i{display:inline-block;margin-right:4px;background:var(--down)}.mid i.pd{width:8px;height:8px;border-radius:50%;background:var(--up)}.mid b.bd{color:#eda100}.mid i.mx{width:9px;height:9px;border-radius:3px;background:none;box-shadow:inset 0 0 0 2px #e5484d;vertical-align:0}.rsum i.dot{width:7px;height:7px;border-radius:50%}.rsum i.sq{width:7px;height:10px;border-radius:1.5px 1.5px 0 0;vertical-align:-1px}
.rlg{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--muted);margin:10px 2px 0}.rlg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}.rlg i.dot{width:7px;height:7px;border-radius:50%;background:var(--down)}
.maps figcaption{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rdet{margin:-4px 0 14px;padding:0 2px 8px;border-bottom:1px solid var(--grid)}.rdh{position:sticky;top:0;z-index:8;display:flex;justify-content:space-between;align-items:center;background:var(--surface);padding:8px 2px;border-bottom:1px solid var(--grid);font-size:14px;cursor:pointer}.rdh span{font-size:12px;color:var(--muted)}
@media (max-width:640px){body{padding-bottom:calc(64px + env(safe-area-inset-bottom))}.ptabs{position:fixed;top:auto;bottom:0;left:0;right:0;margin:0;border-radius:0;border-width:1px 0 0;padding:6px 8px calc(6px + env(safe-area-inset-bottom))}.ptabs a{font-size:15px;padding:10px 2px}.rname{font-size:20px}}
.cmap{height:360px;border-radius:12px;margin:4px 0 12px;overflow:hidden;background:var(--page);z-index:0}
.leaflet-tooltip.ctip{background:rgba(17,17,16,.9);color:#f0efec;border:0;border-radius:6px;padding:2px 7px;font-size:12px;font-weight:600;box-shadow:none;white-space:nowrap}.leaflet-tooltip.ctip:before{display:none}.cmap .leaflet-interactive{cursor:pointer}.cmap .tdark{filter:invert(1) hue-rotate(180deg) brightness(.9) contrast(.9)}
.hint{display:none;font-size:12px;color:var(--muted);margin:0 0 4px}
.chart.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}.chart.scroll svg{width:var(--w);max-width:none}
details{overflow-x:auto}
@media (max-width:640px){
.wrap{padding:10px 10px 48px}header h1{font-size:16px;margin:0 2px 2px}header p{font-size:12px}
nav{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:4px}nav a{flex:none;white-space:nowrap}
section{padding:14px 12px 12px;margin-top:14px;border-radius:10px}section h2{font-size:16px}section.solo{background:none;border:0;padding:0;margin-top:6px}section.solo .rmonth{background:var(--page)}
.hint{display:block}
.cmap{height:300px}
.filters,.sub.holes{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:4px}.filters .fchip,.sub.holes .fchip{flex:none}
.rl{font-size:14px}.rl th,.rl td{padding:9px 4px;white-space:nowrap}.rl td.course{white-space:normal;line-height:1.35}.rl td.arrow,.rl>tr>th:last-child{display:none}
.rl tr.detail>td{padding:4px 0 12px 0;white-space:normal}.sc{padding:4px 2px 0}.m{width:23px;height:23px;font-size:14px;border-width:1.5px}.m.dbl,.m.blow,.m.eagle{outline-width:1.5px;outline-offset:1.5px}.scg .hn,.scg .pr{font-size:12px}.dots{font-size:11px;letter-spacing:0}.rt{font-size:12px}.rt td,.rt th{padding:5px 3px}.rt td.shots{white-space:normal}.chip{padding:0 5px;margin:1px 3px 1px 0;font-size:11px}
.legend{gap:6px 14px}th,td{padding:4px 8px}
}
</style></head><body><div class="wrap">
<header><h1 id="h1"></h1><div class="pthm" id="thm"></div></header><div class="ptabs" id="ptabs"></div><svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<pattern id="pTree3" width="22" height="22" patternUnits="userSpaceOnUse"><rect width="22" height="22" fill="#0f2a14"/><circle cx="6" cy="6" r="5.6" fill="url(#gCan)"/><circle cx="17" cy="15" r="6" fill="url(#gCan)"/><circle cx="17" cy="4" r="3.6" fill="url(#gCan)"/><circle cx="5" cy="17" r="4" fill="url(#gCan)"/><circle cx="11" cy="11" r="3" fill="url(#gCan)"/></pattern><radialGradient id="gCan" cx="38%" cy="34%" r="65%"><stop offset="0" stop-color="#4f8f45"/><stop offset=".55" stop-color="#25592a"/><stop offset="1" stop-color="#0e2712"/></radialGradient><pattern id="pFw3" width="28" height="28" patternUnits="userSpaceOnUse" patternTransform="rotate(38)"><rect width="28" height="28" fill="#6fbf47"/><rect width="14" height="28" fill="#80cf56"/></pattern><radialGradient id="gGreen" cx="45%" cy="40%" r="70%"><stop offset="0" stop-color="#d4fba8"/><stop offset="1" stop-color="#93d965"/></radialGradient><linearGradient id="gWater" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4aa6ff"/><stop offset="1" stop-color="#0b5fd6"/></linearGradient><radialGradient id="gSand" cx="50%" cy="40%" r="70%"><stop offset="0" stop-color="#fff4cf"/><stop offset="1" stop-color="#e3cd8e"/></radialGradient><filter id="fTree" x="-10%" y="-10%" width="130%" height="130%"><feDropShadow dx="1.6" dy="2.6" stdDeviation="1.6" flood-color="#000" flood-opacity=".6"/></filter><filter id="fLift" x="-10%" y="-10%" width="130%" height="130%"><feDropShadow dx="0" dy="1.6" stdDeviation="1.4" flood-color="#000" flood-opacity=".4"/></filter><filter id="fSink" x="-10%" y="-10%" width="130%" height="130%"><feDropShadow dx="0" dy="-1.2" stdDeviation="1" flood-color="#000" flood-opacity=".45"/></filter>
<pattern id="pFw2" width="16" height="16" patternUnits="userSpaceOnUse" patternTransform="rotate(40)"><rect width="16" height="16" fill="#79c64c"/><rect width="8" height="16" fill="#8ad85c"/></pattern><pattern id="pTree2" width="18" height="18" patternUnits="userSpaceOnUse"><rect width="18" height="18" fill="#163a1b"/><circle cx="5" cy="5" r="4.2" fill="#1d4723"/><circle cx="14" cy="13" r="4.6" fill="#1b4320"/><circle cx="14" cy="3.5" r="2.6" fill="#1a401f"/><circle cx="4" cy="14" r="2.8" fill="#1c4421"/></pattern>
<pattern id="pFw" width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(40)"><rect width="14" height="14" fill="#7fc95a"/><rect width="7" height="14" fill="#8ed667"/></pattern>
<pattern id="pTree" width="22" height="22" patternUnits="userSpaceOnUse"><rect width="22" height="22" fill="#14351b"/><circle cx="6" cy="6" r="5.2" fill="#1f5128"/><circle cx="4.8" cy="4.8" r="2.4" fill="#2c6a35"/><circle cx="17" cy="16" r="5.6" fill="#1b4723"/><circle cx="15.8" cy="14.8" r="2.6" fill="#2a6332"/><circle cx="17" cy="4" r="3.2" fill="#193f20"/><circle cx="5" cy="17" r="3.4" fill="#1d4a25"/></pattern>
</defs></svg><nav id="nav"></nav><main id="main"></main><p class="morelink"><a id="link"></a></p><p class="pmeta" id="meta"></p></div>
<script>
const REPORT = __DATA__;
const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs, parent, text) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; };
const h = (tag, cls, parent, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; };

const BIN = 10;   // 色條每格 10 碼
const OK = REPORT.success;   // 成功門檻：眾數的幾成
const THEME = document.documentElement.dataset.theme || "", DARK = THEME ? THEME === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
const STOPS = DARK ? [[57, 135, 229], [138, 138, 132], [235, 96, 96]] : [[42, 120, 214], [226, 224, 216], [227, 73, 72]];   // 少=藍 → 中性 → 多=紅
const heat = t => { const u = t * 2, a = STOPS[u < 1 ? 0 : 1], b = STOPS[u < 1 ? 1 : 2], f = u < 1 ? u : u - 1;
  return "rgb(" + a.map((v, i) => Math.round(v + (b[i] - v) * f)).join(",") + ")"; };
// 球桿距離圖專用的機率色階：藍（少）→ 紫 → 紅（多），中間不經過灰色，才不會和「沒成功的灰點」混淆
const PSTOPS = DARK ? [[57, 135, 229], [170, 105, 215], [235, 96, 96]] : [[42, 120, 214], [150, 90, 200], [227, 73, 72]];
const prob = t => { const u = t * 2, a = PSTOPS[u < 1 ? 0 : 1], b = PSTOPS[u < 1 ? 1 : 2], f = u < 1 ? u : u - 1;
  return "rgb(" + a.map((v, i) => Math.round(v + (b[i] - v) * f)).join(",") + ")"; };
const WIDE_R = () => innerWidth >= 900;
const NARROW = () => innerWidth <= 640;
// 寬度不夠時：圖照 min 寬度畫（字不縮小），外框改成可以左右滑動
const wide = (host, min, mobile) => { const cw = host.clientWidth; if (cw >= min) return cw;
  const w = mobile || min; host.classList.add("scroll"); host.style.setProperty("--w", w + "px"); return w; };
// 球場示意圖的畫法：長草是底色，樹林用深色加樹冠的圓點，球道用淺色斜條紋加亮邊，果嶺最亮，沙坑米色，水藍色
const COURSE_STYLE = { outline: { fill: "#4a8a3c" }, tree: { fill: "url(#pTree)", stroke: "#14351b", "stroke-width": 3, "stroke-linejoin": "round" }, water: { fill: "#3d8fe6", stroke: "#8cc4ff", "stroke-width": 1.5 },
  fairway: { fill: "url(#pFw)", stroke: "#c9f0a2", "stroke-width": 1.6, "stroke-linejoin": "round" }, sand: { fill: "#f1e2b4", stroke: "#b9a36a", "stroke-width": 1 }, green: { fill: "#b5ec83", stroke: "#e6ffc9", "stroke-width": 1.4, "stroke-linejoin": "round" } };
// 桿弟頁的球場配色：長草與樹林收暗，球道、果嶺、沙坑、水要一眼分得出來；邊線不隨放大變粗
const NS2 = { "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" };
const CADDIE_STYLE = { outline: { fill: "#2f5f28" }, tree: Object.assign({ fill: "url(#pTree2)", stroke: "#0f2814", "stroke-width": 1 }, NS2), water: Object.assign({ fill: "#1683ff", stroke: "#c3e4ff", "stroke-width": 2.2 }, NS2),
  fairway: Object.assign({ fill: "url(#pFw2)", stroke: "#dcf8bd", "stroke-width": 1.6 }, NS2), sand: Object.assign({ fill: "#f7e8b8", stroke: "#b9924a", "stroke-width": 1.8 }, NS2), green: Object.assign({ fill: "#bff58e", stroke: "#ffffff", "stroke-width": 1.8 }, NS2) };
const CADDIE_STYLE2 = { outline: { fill: "#2c5a27" }, tree: Object.assign({ fill: "url(#pTree3)", stroke: "#0b2010", "stroke-width": 1 }, NS2), water: Object.assign({ fill: "url(#gWater)", stroke: "#bfe2ff", "stroke-width": 2 }, NS2), fairway: Object.assign({ fill: "url(#pFw3)", stroke: "#d9f7b6", "stroke-width": 1.4 }, NS2), sand: Object.assign({ fill: "url(#gSand)", stroke: "#b48c45", "stroke-width": 1.6 }, NS2), green: Object.assign({ fill: "url(#gGreen)", stroke: "#ffffff", "stroke-width": 1.8 }, NS2) };
const drawCourse2 = (svg, geo) => ["outline", "tree", "fairway", "water", "sand", "green"].forEach(key => { const g = el("g", key === "tree" ? { filter: "url(#fTree)" } : key === "fairway" || key === "green" ? { filter: "url(#fLift)" } : key === "water" || key === "sand" ? { filter: "url(#fSink)" } : {}, svg); (geo[key] || []).forEach(p => el("polygon", Object.assign({ points: p.map(q => q.join(",")).join(" ") }, CADDIE_STYLE2[key]), g)); });
const drawCourse = (svg, geo, style) => ["outline", "tree", "fairway", "water", "sand", "green"].forEach(key => (geo[key] || []).forEach(p => el("polygon", Object.assign({ points: p.map(q => q.join(",")).join(" ") }, (style || COURSE_STYLE)[key]), svg)));
const DRAW = {
  box(sec, host) {
    const d = sec.data, W = wide(host, 560, 1000), H = 600, m = { l: 104, r: 34, t: 12, b: 136 };
    const all = d.flatMap(x => x.pts).sort((p, q) => p - q);
    const top = Math.ceil(all[Math.floor(all.length * 0.997)] * 1.02 / 50) * 50;   // 極端離群值不拉長座標軸（滑鼠提示仍顯示最遠值）
    const y = v => m.t + (H - m.t - m.b) * (1 - v / top), band = (W - m.l - m.r) / d.length, bw = Math.min(22, band * 0.24);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": sec.title }, host);
    for (let v = 0; v <= top; v += 50) {
      el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v ? "var(--grid)" : "var(--axis)", "stroke-width": 1 }, svg);
      el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" }, svg, v);
    }
    const tip = h("div", "tip", host);
    [["使用率", 38], ["近 10 場使用率", 55], ["成功率", 76], ["近 10 場成功率", 93]].forEach(([t, dy]) => el("text", { x: m.l - 12, y: H - m.b + dy, "text-anchor": "end", class: "xn" }, svg, t));   // 列標題只寫一次，放最左邊
    const arrowed = (xx, yy, now, base, good) => { const tx = el("text", { x: xx, y: yy, "text-anchor": "middle", class: "xn" }, svg, now == null ? "—" : now + "%");
      if (now != null && Math.abs(now - base) >= 3) { const up = now > base, sp = el("tspan", { dx: 3, fill: good ? (up ? "var(--up)" : "var(--down)") : "var(--muted)" }, tx); sp.textContent = up ? "▲" : "▼"; } };
    const groups = []; d.forEach((c, i) => { const g = groups[groups.length - 1]; if (g && g.name === c.group) g.to = i; else groups.push({ name: c.group, from: i, to: i }); });
    groups.forEach((g, gi) => { const x1 = m.l + band * g.from + 8, x2 = m.l + band * (g.to + 1) - 8, yy = H - m.b + 108;
      const c0 = d[g.from]; if (c0.grouped) { const gx = (x1 + x2) / 2;   // 切球桿：整組共用「一次上果嶺」的比例
        el("text", { x: gx, y: H - m.b + 76, "text-anchor": "middle", class: "xl", "font-weight": 600 }, svg, c0.success + "%");
        const tx = el("text", { x: gx, y: H - m.b + 93, "text-anchor": "middle", class: "xl", "font-weight": 600 }, svg, c0.success10 + "%");
        if (Math.abs(c0.success10 - c0.success) >= 3) { const up = c0.success10 > c0.success, sp = el("tspan", { dx: 3, fill: up ? "var(--up)" : "var(--down)" }, tx); sp.textContent = up ? "▲" : "▼"; } }
      el("line", { x1, x2, y1: yy, y2: yy, stroke: "var(--axis)", "stroke-width": 1.5 }, svg);
      el("text", { x: (x1 + x2) / 2, y: yy + 18, "text-anchor": "middle", class: "xl", "font-weight": 600 }, svg, g.name);
      if (gi) el("line", { x1: x1 - 8, x2: x1 - 8, y1: m.t, y2: H - m.b, stroke: "var(--axis)", "stroke-width": 1, "stroke-dasharray": "3 4" }, svg); });
    d.forEach((c, i) => {
      const mid = m.l + band * (i + 0.5), cx = mid + band * 0.2, g = el("g", {}, svg);
      const jw = band * 0.42, x0 = mid - band * 0.4;
      const bins = {}; c.pts.forEach(v => { const k = Math.floor(v / BIN); bins[k] = (bins[k] || 0) + 1; });
      const peak = Math.max(...Object.values(bins)), modeK = +Object.keys(bins).find(k => bins[k] === peak);
      c.pts.forEach((v, k) => { const r = Math.sin(k * 12.9898 + i * 78.233) * 43758.5453, j = r - Math.floor(r);   // 固定的假亂數，重畫時點不會跳動
        if (v > top) return;
        const inside = v >= OK * c.mode;   // 成功的球（打到眾數的 90% 以上）用右邊色條同一格的顏色，沒成功的用淡灰
        el("circle", { cx: x0 + j * jw, cy: y(v), r: 2.3, fill: inside ? prob(bins[Math.floor(v / BIN)] / peak) : "var(--ink2)", "fill-opacity": inside ? 0.75 : 0.25 }, g); });
      for (const k in bins) { const lo = Math.max(k * BIN, OK * c.mode), hi = Math.min(k * BIN + BIN, c.p95); if (hi - lo < 1) continue;
        el("rect", { x: cx - bw / 2, y: y(hi) + 0.5, width: bw, height: Math.max(y(lo) - y(hi) - 1, 1), rx: 2, fill: prob(bins[k] / peak) }, g); }
      el("line", { x1: cx - bw / 2 - 3, x2: cx + bw / 2 + 3, y1: y(c.med), y2: y(c.med), stroke: "var(--ink)", "stroke-width": 1.5 }, g);
      const ym = y(c.mode), xr = cx + bw / 2;
      el("path", { d: `M${xr + 2},${ym} l8,-5 v10 z`, fill: "var(--ink)" }, g);
      el("text", { x: xr + 13, y: ym + 5, class: "xl", "font-weight": 700, "font-size": 14 }, g, c.mode);
      let yl = y(c.med); if (Math.abs(yl - ym) < 14) yl = ym + (yl >= ym ? 14 : -14);
      el("text", { x: xr + 13, y: yl + 4, class: "xn" }, g, c.med);
      el("text", { x: mid, y: H - m.b + 20, "text-anchor": "middle", class: "xl" }, svg, c.club);
      el("text", { x: mid, y: H - m.b + 38, "text-anchor": "middle", class: "xn" }, svg, c.share + "%");
      arrowed(mid, H - m.b + 55, c.share10, c.share, false);
      if (!c.grouped) { el("text", { x: mid, y: H - m.b + 76, "text-anchor": "middle", class: "xn" }, svg, c.success + "%");
        arrowed(mid, H - m.b + 93, c.success10, c.success, true); }
      const hit = el("rect", { x: mid - band / 2, y: m.t, width: band, height: H - m.t - m.b, fill: "transparent" }, svg);
      hit.addEventListener("mousemove", e => {
        const sb = svg.getBoundingClientRect(), py = (e.clientY - sb.top) * H / sb.height;
        const k = Math.floor(top * (1 - (py - m.t) / (H - m.t - m.b)) / BIN), cnt = bins[k] || 0;
        tip.replaceChildren(); h("b", "", tip, `${c.club}（${c.n} 桿）`);
        const pr = h("div", "", tip); h("span", "", pr, `${k * BIN}–${k * BIN + BIN} 碼的機率`); h("span", "", pr, (100 * cnt / c.n).toFixed(1) + "%");
        [["眾數（最常打出）", c.mode], [c.grouped ? "一次上果嶺（整組）" : "成功率", c.success + "%"], ["95%", c.p95], ["中位數", c.med], ["平均", c.mean], ["5%", c.p5]]
          .forEach(([k, v]) => { const r = h("div", "", tip); h("span", "", r, k); h("span", "", r, String(v).endsWith("%") ? v : v + " 碼"); });
        tip.style.display = "block";
        // 提示框固定在圖的上緣，自動挑最靠右、且下方沒有資料的位置，不蓋到任何一桿
        const f = sb.height / H, tw = tip.offsetWidth, th = tip.offsetHeight + 10;
        const clearBelow = top * (1 - (th / f - m.t) / (H - m.t - m.b));
        let left = sb.width - tw - 8;
        for (let r = d.length; r >= 1; r--) {
          const right = (m.l + band * r) * f, l = right - tw, i0 = Math.max(Math.floor((l / f - m.l) / band), 0);
          if (l < m.l * f) break;
          if (d.slice(i0, r).every(x => Math.min(x.max, top) < clearBelow)) { left = l; break; }
        }
        tip.style.left = left + "px"; tip.style.top = "4px"; tip.style.right = "auto";
      });
      hit.addEventListener("mouseleave", () => (tip.style.display = "none"));
    });
    const lg = h("div", "legend", host.parentNode);
    const sw = [0.1, 0.3, 0.5, 0.75, 1].map(t => `<rect x="${(t === 1 ? 4 : [0.1, 0.3, 0.5, 0.75].indexOf(t)) * 15}" y="1" width="14" height="12" rx="2" fill="${prob(t)}"/>`).join("");
    lg.innerHTML = `<span><svg width="12" height="14"><path d="M1,7 l9,-5 v10 z" fill="var(--ink)"/></svg>眾數：最常打出的距離（粗體）</span><span>機率　低<svg width="75" height="14">${sw}</svg>高</span><span><svg width="16" height="14"><line x1="1" y1="7" x2="15" y2="7" stroke="var(--ink)" stroke-width="3"/></svg>中位數（小字）</span><span>色條與彩色的點：成功的範圍</span><span><svg width="14" height="14"><circle cx="7" cy="7" r="2.5" fill="var(--ink2)" fill-opacity=".4"/></svg>沒成功的球</span><span>成功：打到眾數的 90% 以上；切球桿的成功是切一次就上果嶺</span>`;
    host.after(lg);
  },
};

DRAW.heat = (sec, host) => {
  const d = sec.data, W = wide(host, 560, 640), rh = 50, m = { l: 118, r: 16, t: 34, b: 62 }, H = m.t + rh * d.rows.length + m.b;
  const cw = (W - m.l - m.r) / d.cols.length, peak = Math.max(...d.cols.flatMap(c => c.p));
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": sec.title }, host);
  d.rows.forEach((r, j) => el("text", { x: m.l - 10, y: m.t + rh * (j + 0.5) + 5, "text-anchor": "end", class: "xl" }, svg, r));
  d.cols.forEach((c, i) => { const x = m.l + cw * i;
    el("text", { x: x + cw / 2, y: m.t - 12, "text-anchor": "middle", class: "xn" }, svg, c.n ? c.top : "");
    c.p.forEach((v, j) => { const t = v / peak, mid = t > 0.3 && t < 0.72;
      el("rect", { x: x + 1.5, y: m.t + rh * j + 1.5, width: cw - 3, height: rh - 3, rx: 4, fill: c.n ? heat(t) : "var(--grid)" }, svg);
      if (c.n) el("text", { x: x + cw / 2, y: m.t + rh * (j + 0.5) + 6, "text-anchor": "middle", "font-size": 16, "font-weight": 600,
        fill: mid ? (DARK ? "#fff" : "#0b0b0b") : "#fff", style: "font-variant-numeric:tabular-nums" }, svg, d.unit === "" ? v.toFixed(1) : Math.round(v) + "%"); });
    el("text", { x: x + cw / 2, y: H - m.b + 20, "text-anchor": "middle", class: "xl" }, svg, c.label);
    el("text", { x: x + cw / 2, y: H - m.b + 38, "text-anchor": "middle", class: "xn" }, svg, c.sub); });
  el("text", { x: m.l + (W - m.l - m.r) / 2, y: H - 6, "text-anchor": "middle", class: "xn" }, svg, d.xlabel);
};

DRAW.trend = (sec, host) => {
  const d = sec.data, o = Object.assign({ unit: " 推", step: 5, line: "近 5 場平均", dot: "每一場" }, sec.opts), tr = o.trend, n = d.length;
  const W = Math.max(host.clientWidth, 320), H = 370, m = { l: 44, r: 52, t: 34, b: 40 };
  const read = h("div", "read", host); read.textContent = " ";
  const vals = d.map(p => p.v).concat(tr ? tr.band.flat() : []);
  const lo = Math.floor(Math.min(...vals) / o.step) * o.step, hi = Math.ceil(Math.max(...vals) / o.step) * o.step;
  const x = i => m.l + (W - m.l - m.r) * i / (n - 1), y = v => m.t + (H - m.t - m.b) * (1 - (v - lo) / (hi - lo));
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": sec.title }, host);
  for (let v = lo; v <= hi; v += o.step) { el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v === lo ? "var(--axis)" : "var(--grid)" }, svg);
    el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" }, svg, v); }
  let lastX = -99, lastM = "";
  d.forEach((p, i) => { const mm = p.date.slice(0, 7); if (mm !== lastM && x(i) - lastX > 64) {
    el("text", { x: x(i), y: H - m.b + 20, "text-anchor": "middle", class: "tick" }, svg, `${+mm.slice(0, 4)}/${+mm.slice(5)}`); lastX = x(i); } lastM = mm; });
  if (tr) { el("path", { d: "M" + tr.band.map((b, i) => x(i).toFixed(1) + "," + y(b[1]).toFixed(1)).join("L") + "L" + tr.band.map((b, i) => x(i).toFixed(1) + "," + y(b[0]).toFixed(1)).reverse().join("L") + "Z", fill: "var(--trend)", "fill-opacity": 0.14 }, svg);
    el("line", { x1: x(0), y1: y(tr.y0), x2: x(n - 1), y2: y(tr.y1), stroke: "var(--trend)", "stroke-width": 1.75, "stroke-linecap": "round", "stroke-dasharray": "6 5" }, svg);
    el("text", { x: m.l, y: m.t - 14, class: "xl", "font-weight": 600 }, svg, `趨勢：每 10 場 ${tr.per10 > 0 ? "+" : ""}${tr.per10}${tr.unit}` + (tr.sig ? "" : "（未達統計顯著）")); }
  const pts = d.map((p, i) => [x(i), p.avg]).filter(p => p[1] != null);
  el("path", { d: "M" + pts.map(p => p[0].toFixed(1) + "," + y(p[1]).toFixed(1)).join("L"), fill: "none", stroke: "var(--series)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
  const L = pts[pts.length - 1]; el("text", { x: L[0] + 10, y: y(L[1]) + 5, class: "xl", "font-weight": 600 }, svg, L[1].toFixed(1));
  const say = p => `${p.date}　${p.course}　${p.v}${o.unit}` + (o.plain ? (p.extra ? `（${p.extra}）` : "") : `（${p.measured ? "實際" : "估算"}）`) + (p.avg ? `　${o.line} ${p.avg}` : "");
  d.forEach((p, i) => { const solid = o.plain || p.measured;
    const c = el("circle", { cx: x(i), cy: y(p.v), r: 5, fill: solid ? "var(--ink)" : "var(--surface)", stroke: "var(--ink)", "stroke-width": 1.5, "stroke-opacity": solid ? 1 : 0.55 }, svg);
    const hit = el("circle", { cx: x(i), cy: y(p.v), r: 13, fill: "transparent" }, svg);
    hit.addEventListener("mouseenter", () => { read.textContent = say(p); c.setAttribute("r", 7); });
    hit.addEventListener("mouseleave", () => { read.textContent = " "; c.setAttribute("r", 5); }); });
  const lg = h("div", "legend", null), dot = (f, t) => `<span><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="${f}" stroke="var(--ink2)" stroke-width="1.5"/></svg>${t}</span>`;
  lg.innerHTML = (o.plain ? dot("var(--ink)", o.dot) : dot("var(--ink)", "實際紀錄") + dot("none", "估算")) +
    '<span><svg width="18" height="14"><line x1="1" y1="7" x2="17" y2="7" stroke="var(--series)" stroke-width="2"/></svg>' + o.line + '</span>' +
    (tr ? '<span><svg width="22" height="14"><rect x="1" y="2" width="20" height="10" rx="2" fill="var(--trend)" fill-opacity=".18"/><line x1="1" y1="7" x2="21" y2="7" stroke="var(--trend)" stroke-width="1.75" stroke-dasharray="5 4"/></svg>趨勢線與 95% 信賴帶</span>' : "");
  host.after(lg);
};

// 堆疊直方圖：每一欄是一根 100% 的柱子，由下到上依序是各種結果（好的在下、壞的在上，藍到紅）
DRAW.stack = (sec, host) => {
  const d = sec.data, k = d.rows.length, W = wide(host, 560, 640), H = 400, m = { l: 44, r: 16, t: 34, b: d.xlabel ? 74 : 58 };
  const band = (W - m.l - m.r) / d.cols.length, bw = Math.min(84, band * 0.62), ph = H - m.t - m.b, t = j => (k === 1 ? 1 : j / (k - 1));
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": sec.title }, host);
  [0, 25, 50, 75, 100].forEach(v => { const yy = m.t + ph * (1 - v / 100);
    el("line", { x1: m.l, x2: W - m.r, y1: yy, y2: yy, stroke: v ? "var(--grid)" : "var(--axis)" }, svg);
    el("text", { x: m.l - 8, y: yy + 4, "text-anchor": "end", class: "tick" }, svg, v + "%"); });
  d.cols.forEach((c, i) => { const cx = m.l + band * (i + 0.5); let acc = 0;
    c.p.forEach((v, j) => { const hh = ph * v / 100, y1 = m.t + ph * (1 - acc / 100) - hh; acc += v; if (hh < 1) return;
      el("rect", { x: cx - bw / 2, y: y1 + 1, width: bw, height: Math.max(hh - 2, 1), rx: 3, fill: heat(t(j)) }, svg);
      if (hh >= 20) el("text", { x: cx, y: y1 + hh / 2 + 5, "text-anchor": "middle", "font-size": 14, "font-weight": 600,
        fill: t(j) > 0.3 && t(j) < 0.72 ? (DARK ? "#fff" : "#0b0b0b") : "#fff", style: "font-variant-numeric:tabular-nums" }, svg, Math.round(v) + "%"); });
    el("text", { x: cx, y: m.t - 12, "text-anchor": "middle", class: "xl", "font-weight": 600 }, svg, c.top);
    el("text", { x: cx, y: m.t + ph + 20, "text-anchor": "middle", class: "xl" }, svg, c.label);
    el("text", { x: cx, y: m.t + ph + 38, "text-anchor": "middle", class: "xn" }, svg, c.sub); });
  if (d.xlabel) el("text", { x: m.l + (W - m.l - m.r) / 2, y: H - 8, "text-anchor": "middle", class: "xn" }, svg, d.xlabel);
  const lg = h("div", "legend", null);
  lg.innerHTML = d.rows.map((r, j) => `<span><svg width="14" height="14"><rect x="1" y="1" width="12" height="12" rx="3" fill="${heat(t(j))}"/></svg>${r}</span>`).reverse().join("");
  host.after(lg);
};

// 比率直方圖：每一欄一根柱子，附 95% 信賴區間
DRAW.rate = (sec, host) => {
  const d = sec.data, W = wide(host, 560, 640), H = 340, m = { l: 44, r: 16, t: 34, b: 58 }, ph = H - m.t - m.b;
  const band = (W - m.l - m.r) / d.cols.length, bw = Math.min(84, band * 0.4), y = v => m.t + ph * (1 - v / 100);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": sec.title }, host);
  [0, 25, 50, 75, 100].forEach(v => { el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v ? "var(--grid)" : "var(--axis)" }, svg);
    el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" }, svg, v + "%"); });
  el("text", { x: m.l, y: m.t - 14, class: "xl", "font-weight": 600 }, svg, d.verdict);
  d.cols.forEach((c, i) => { const cx = m.l + band * (i + 0.5);
    el("path", { d: `M${cx - bw / 2},${y(0)} V${y(c.v) + 4} q0,-4 4,-4 H${cx + bw / 2 - 4} q4,0 4,4 V${y(0)} z`, fill: "var(--series)" }, svg);
    el("line", { x1: cx, x2: cx, y1: y(c.hi), y2: y(c.lo), stroke: "var(--ink)", "stroke-width": 2 }, svg);
    [c.lo, c.hi].forEach(v => el("line", { x1: cx - 9, x2: cx + 9, y1: y(v), y2: y(v), stroke: "var(--ink)", "stroke-width": 2 }, svg));
    el("text", { x: cx + bw / 2 + 10, y: y(c.v) + 5, class: "xl", "font-weight": 700, "font-size": 16 }, svg, Math.round(c.v) + "%");
    el("text", { x: cx, y: m.t + ph + 20, "text-anchor": "middle", class: "xl" }, svg, c.label);
    el("text", { x: cx, y: m.t + ph + 38, "text-anchor": "middle", class: "xn" }, svg, c.sub); });
  const lg = h("div", "legend", null);
  lg.innerHTML = '<span><svg width="14" height="14"><line x1="7" y1="1" x2="7" y2="13" stroke="var(--ink)" stroke-width="2"/><line x1="2" y1="1" x2="12" y2="1" stroke="var(--ink)" stroke-width="2"/><line x1="2" y1="13" x2="12" y2="13" stroke="var(--ink)" stroke-width="2"/></svg>95% 信賴區間</span>';
  host.after(lg);
};

DRAW.compare = (sec, host) => {
  const d = sec.data, W = wide(host, 560, 1000), H = 500, m = { l: 48, r: 20, t: 12, b: 84 };
  const top = Math.ceil(Math.max(...d.flatMap(c => [c.old.p90, c.new.p90])) * 1.08 / 50) * 50;
  const y = v => m.t + (H - m.t - m.b) * (1 - v / top), band = (W - m.l - m.r) / d.length, bw = Math.min(34, band * 0.16);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": sec.title }, host);
  for (let v = 0; v <= top; v += 50) { el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v ? "var(--grid)" : "var(--axis)" }, svg);
    el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" }, svg, v); }
  el("text", { x: m.l - 8, y: H - m.b + 38, "text-anchor": "end", class: "xn" }, svg, "眾數");
  const groups = []; d.forEach((c, i) => { const g = groups[groups.length - 1]; if (g && g.name === c.group) g.to = i; else groups.push({ name: c.group, from: i, to: i }); });
  groups.forEach((g, gi) => { const x1 = m.l + band * g.from + 8, x2 = m.l + band * (g.to + 1) - 8, yy = H - m.b + 54;
    el("line", { x1, x2, y1: yy, y2: yy, stroke: "var(--axis)", "stroke-width": 1.5 }, svg);
    el("text", { x: (x1 + x2) / 2, y: yy + 18, "text-anchor": "middle", class: "xl", "font-weight": 600 }, svg, g.name);
    if (gi) el("line", { x1: x1 - 8, x2: x1 - 8, y1: m.t, y2: H - m.b, stroke: "var(--axis)", "stroke-dasharray": "3 4" }, svg); });
  // 過去：窄而淡；最近 10 場：寬而實。不畫外框。
  const strip = (side, cx, w, recent) => { const bins = {}; side.pts.forEach(v => { const k = Math.floor(v / BIN); bins[k] = (bins[k] || 0) + 1; });
    const peak = Math.max(...Object.values(bins));
    for (const k in bins) { const lo = Math.max(k * BIN, side.p10), hi = Math.min(k * BIN + BIN, side.p90); if (hi - lo < 1) continue;
      el("rect", { x: cx - w / 2, y: y(hi) + 0.5, width: w, height: Math.max(y(lo) - y(hi) - 1, 1), rx: 2, fill: heat(bins[k] / peak), "fill-opacity": recent ? 1 : 0.6 }, svg); }
    const ym = y(side.mode);
    if (recent) { el("path", { d: `M${cx + w / 2 + 3},${ym} l8,-5 v10 z`, fill: "var(--ink)" }, svg);
      el("text", { x: cx + w / 2 + 14, y: ym + 5, class: "xl", "font-weight": 700, "font-size": 14 }, svg, side.mode); }
    else el("path", { d: `M${cx - w / 2 - 3},${ym} l-7,-4 v8 z`, fill: "var(--muted)" }, svg); };
  d.forEach((c, i) => { const mid = m.l + band * (i + 0.5);
    strip(c.old, mid - bw * 0.8, bw * 0.6, false); strip(c.new, mid + bw * 0.25, bw, true);
    el("text", { x: mid, y: H - m.b + 20, "text-anchor": "middle", class: "xl" }, svg, c.club);
    el("text", { x: mid, y: H - m.b + 38, "text-anchor": "middle", class: "xn" }, svg, `${c.old.mode} → ${c.new.mode}`); });
  const lg = h("div", "legend", null);
  lg.innerHTML = '<span><svg width="12" height="16"><rect x="3" y="1" width="6" height="14" rx="2" fill="var(--muted)" fill-opacity=".6"/></svg>過去（窄）</span><span><svg width="14" height="16"><rect x="1" y="1" width="12" height="14" rx="2" fill="var(--muted)"/></svg>最近 10 場（寬）</span><span><svg width="12" height="14"><path d="M1,7 l9,-5 v10 z" fill="var(--ink)"/></svg>眾數</span><span>色條涵蓋 90% 的球</span>';
  host.after(lg);
};

// 成對直方圖：每組兩根（過去、最近 10 場），可選一條目標線。簡單就好，不在柱子裡再切段。
DRAW.pair = (sec, host) => {
  const d = sec.data, over = d.mode === "over", W = Math.max(host.clientWidth, 320), H = 380, nar = host.clientWidth < 560, m = { l: nar ? 76 : 96, r: nar ? 6 : 16, t: 24, b: d.foot.length ? 78 : 44 }, ph = H - m.t - m.b;
  const step = d.step, top = Math.ceil(Math.max(...d.groups.flatMap(g => [g.old, g.new])) * 1.18 / step) * step, y = v => m.t + ph * (1 - v / top);
  const band = (W - m.l - m.r) / d.groups.length, bw = nar ? Math.min(32, band * 0.36) : Math.min(72, band * 0.24), fmt = v => (over ? "+" + v.toFixed(2) : v.toFixed(1));
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": sec.title }, host);
  for (let v = 0; v <= top + 1e-9; v += step) { el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v ? "var(--grid)" : "var(--axis)" }, svg);
    const name = over ? { 0: "Par", 1: "Bogey +1", 2: "Double +2", 3: "Triple +3" }[v] : null;      // 整數刻度用高爾夫的講法
    el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: name ? "xl" : "tick" }, svg, name || (over ? "+" + v.toFixed(1) : v + d.unit)); }
  d.foot.forEach(([t, dy]) => el("text", { x: m.l - 8, y: m.t + ph + dy, "text-anchor": "end", class: "xn" }, svg, t));
  if (d.target != null) el("line", { x1: m.l, x2: W - m.r, y1: y(d.target), y2: y(d.target), stroke: "var(--ink)", "stroke-width": 1.75, "stroke-dasharray": "7 5" }, svg);
  const bar = (cx, v, recent) => { el("path", { d: `M${cx - bw / 2},${y(0)} V${y(v) + 4} q0,-4 4,-4 H${cx + bw / 2 - 4} q4,0 4,4 V${y(0)} z`, fill: "var(--series)", "fill-opacity": recent ? 1 : 0.42 }, svg);
    el("text", { x: cx, y: y(v) - 8, "text-anchor": "middle", class: "xl", "font-weight": recent ? 700 : 400, "font-size": nar ? (recent ? 13 : 11) : (recent ? 17 : 13) }, svg, fmt(v)); };
  d.groups.forEach((g, i) => { const mid = m.l + band * (i + 0.5);
    bar(mid - bw * 0.6, g.old, false); bar(mid + bw * 0.6, g.new, true);
    el("text", { x: mid, y: m.t + ph + 20, "text-anchor": "middle", class: "xl", "font-weight": 600 }, svg, g.label);
    (g.foot || []).forEach((t, k) => el("text", Object.assign({ x: mid, y: m.t + ph + d.foot[k][1], "text-anchor": "middle", class: "xn" }, nar ? { "font-size": 11 } : {}), svg, nar ? t.replace(/ → /, "→") : t)); });
  const lg = h("div", "legend", null), sw = (o, t) => `<span><svg width="14" height="14"><rect x="1" y="1" width="12" height="12" rx="3" fill="var(--series)" fill-opacity="${o}"/></svg>${t}</span>`;
  lg.innerHTML = sw(0.42, "過去") + sw(1, "最近 10 場") + (d.target != null ? `<span><svg width="20" height="14"><line x1="1" y1="7" x2="19" y2="7" stroke="var(--ink)" stroke-width="1.75" stroke-dasharray="5 4"/></svg>${d.targetLabel}</span>` : "") + (d.legendNote ? `<span>${d.legendNote}</span>` : "");
  host.after(lg);
};

// 推桿：每個月三根柱子，18 洞裡一推、兩推、三推以上各幾洞
DRAW.putts = (sec, host) => {
  const d = sec.data, mo = d.months, n = mo.length, W = wide(host, 560, 1000), H = 400, m = { l: 48, r: 16, t: 62, b: 58 }, ph = H - m.t - m.b;
  const top = Math.ceil(Math.max(...mo.flatMap(p => p.c)) / 3) * 3, y = v => m.t + ph * (1 - v / top);
  const band = (W - m.l - m.r) / n, bw = Math.min(22, band * 0.24), col = [heat(0), heat(0.5), heat(1)];
  const read = h("div", "read", host); read.textContent = " ";
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": sec.title }, host);
  for (let v = 0; v <= top; v += 3) { el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v ? "var(--grid)" : "var(--axis)" }, svg);
    el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" }, svg, v + " 洞"); }
  mo.forEach((p, i) => { const mid = m.l + band * (i + 0.5), g = el("g", {}, svg);
    p.c.forEach((v, j) => { const cx = mid + (j - 1) * (bw + 3);
      el("path", { d: `M${cx - bw / 2},${y(0)} V${y(v) + 3} q0,-3 3,-3 H${cx + bw / 2 - 3} q3,0 3,3 V${y(0)} z`, fill: col[j] }, g);
      el("text", { x: cx, y: y(v) - 5, "text-anchor": "middle", class: "xn" }, g, Math.round(v)); });
    el("text", { x: mid, y: m.t + ph + 20, "text-anchor": "middle", class: "xl" }, svg, p.label);
    el("text", { x: mid, y: m.t + ph + 38, "text-anchor": "middle", class: "xn" }, svg, `每洞 ${p.avg.toFixed(2)}`);
    const hit = el("rect", { x: mid - band / 2, y: m.t, width: band, height: ph, fill: "transparent" }, svg);
    hit.addEventListener("mouseenter", () => { g.setAttribute("opacity", 0.8); read.textContent = `${p.label}（${p.rounds} 場）　一推 ${p.c[0]} 洞・兩推 ${p.c[1]} 洞・三推以上 ${p.c[2]} 洞　每洞平均 ${p.avg.toFixed(2)} 推`; });
    hit.addEventListener("mouseleave", () => { g.removeAttribute("opacity"); read.textContent = " "; }); });
  const tr = d.trend;
  el("text", { x: m.l, y: 20, class: "xl", "font-weight": 600 }, svg, `三推以上：每 10 場 ${tr.per10 > 0 ? "+" : ""}${tr.per10} 洞` + (tr.sig ? "" : "（未達統計顯著）"));
  el("text", { x: W - m.r, y: 30, "text-anchor": "end", "font-size": 28, "font-weight": 700, fill: "var(--ink)", style: "font-variant-numeric:tabular-nums" }, svg, d.recent.toFixed(2));
  el("text", { x: W - m.r - 78, y: 18, "text-anchor": "end", class: "xn" }, svg, "最近 10 場每洞平均推桿");
  el("text", { x: W - m.r - 78, y: 35, "text-anchor": "end", class: "xn" }, svg, `目標 ${d.target.toFixed(1)} 以下，還差 ${(d.recent - d.target).toFixed(2)}`);
  const lg = h("div", "legend", null), sw = (c, t) => `<span><svg width="14" height="14"><rect x="1" y="1" width="12" height="12" rx="3" fill="${c}"/></svg>${t}</span>`;
  lg.innerHTML = sw(col[0], "一推") + sw(col[1], "兩推") + sw(col[2], "三推以上") + "<span>柱子高度：換算成 18 洞裡有幾洞</span>";
  host.after(lg);
};

// 場次總表：一列一場，可篩選類別，點一下展開該場 18 洞明細
// 球場紀錄：選球場 → 一洞一張大圖，左右滑動；每一洞疊上歷次的路線
DRAW.courses = (sec, host) => {
  const MAPC = { outline: "#2f5d2a", tree: "#1c3a1e", fairway: "#58963f", water: "#3c6edc", sand: "#e1cdaa", green: "#8ccd6e" }, SHOTC = { d: "#ff5a5a", w: "#ffa51f", i: "#5aa9ff", c: "#ffffff" };
  let ci = 0, li = 0, region = sec.data[0].region, map = null, marks = [];
  const regions = ["北台灣", "中台灣", "南台灣", "越南河內", "越南", "國外", "其他"].filter(g => sec.data.some(c => c.region === g)), s0 = h("div", "filters", host), mapEl = h("div", "cmap", host); mapEl.style.display = "none";
  const fit = () => { if (!map) return; const pts = sec.data.filter(c => c.region === region && c.ll).map(c => c.ll); if (pts.length) map.fitBounds(pts, { padding: [48, 48], maxZoom: 11 }); };
  const paint = () => marks.forEach(([i, m]) => { const on = i === ci; m.setStyle({ radius: on ? 11 : 8, fillColor: on ? "#ff5a5a" : "#3987e5", weight: on ? 3 : 2 }); if (on) m.bringToFront(); });
  const initMap = () => { mapEl.style.display = ""; map = L.map(mapEl, { zoomControl: true, attributionControl: true, scrollWheelZoom: false, preferCanvas: true, zoomSnap: 0.25 }); map.attributionControl.setPrefix(false);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, className: DARK ? "tdark" : "", attribution: "© OpenStreetMap contributors" }).addTo(map);
    sec.data.forEach((c, i) => { if (!c.ll) return; const m = L.circleMarker(c.ll, { color: "#fff", fillOpacity: 1 }).addTo(map);
      m.bindTooltip(`${c.short} ${c.n}`, { permanent: true, direction: "top", offset: [0, -8], className: "ctip", interactive: true }); m.on("click", () => { ci = i; li = 0; region = c.region; draw(); host.querySelector(".sub.holes").scrollIntoView({ behavior: "smooth", block: "start" }); }); marks.push([i, m]); });
    fit(); paint(); };
  { const css = document.createElement("link"); css.rel = "stylesheet"; css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"; document.head.appendChild(css);
    const js = document.createElement("script"); js.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"; js.onload = () => { try { initMap(); } catch (e) { mapEl.style.display = "none"; } }; document.head.appendChild(js); }
  const s1 = h("div", "filters", host), s2 = h("div", "sub", host), s3 = h("div", "sub holes", host), strip = h("div", "maps strip big", host), lg = h("div", "legend", host);
  lg.innerHTML = [["d", "一號木"], ["w", "鐵木桿"], ["i", "鐵桿"]].map(([c, t]) => `<span><svg width="20" height="14"><line x1="1" y1="7" x2="13" y2="7" stroke="${SHOTC[c]}" stroke-width="2.4"/><circle cx="15" cy="7" r="3.6" fill="${SHOTC[c]}"/></svg>${t}</span>`).join("") +
    `<span><svg width="20" height="14"><line x1="1" y1="7" x2="19" y2="7" stroke="var(--ink2)" stroke-width="1.2"/></svg>切球桿</span><span>越淡＝越早的場次</span>`;
  const fig = (L, H) => { const f = document.createElement("figure"), geo = (REPORT.geo[L.cid] || {})[H.h] || {}, P = H.plays, sts = P.map(p => p.st).filter(v => v != null);
    const dr = P.map(p => p.shots[0]).filter(s => s && s[1] === "d"), fw = dr.filter(s => s[7] === "Fairway").length;
    h("figcaption", "", f, `第 ${H.h} 洞　Par ${H.par}` + (H.yd ? `　${H.yd} 碼` : ""));
    const st = h("div", "cstat", f); st.innerHTML = `<span>打過 <b>${P.length}</b> 次</span>` + (sts.length ? `<span>平均 <b>${(sts.reduce((a, b) => a + b, 0) / sts.length).toFixed(1)}</b> 桿</span><span>最好 <b>${Math.min(...sts)}</b></span>` : "") + (dr.length ? `<span>一號木上球道 <b>${fw}/${dr.length}</b></span>` : "");
    const pts = []; (geo.outline || []).forEach(p => pts.push(...p)); P.forEach(p => { p.shots.forEach(s => pts.push([s[2], s[3]], [s[4], s[5]])); if (p.pin) pts.push(p.pin); }); const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    if (pts.length) { const pad = geo.outline ? 10 : 40, x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad, w = Math.max(...xs) - x0 + pad, hh = Math.max(...ys) - y0 + pad, k = Math.max(w, hh) / 330;
      const svg = el("svg", { viewBox: `${x0} ${y0} ${w} ${hh}`, style: `height:${NARROW() ? 470 : 520}px`, preserveAspectRatio: "xMidYMid meet" }, f);
      drawCourse(svg, geo);
      P.forEach((p, i) => { const op = P.length === 1 ? 1 : 0.3 + 0.7 * i / (P.length - 1);      // 越新的越清楚
        p.shots.forEach(s => { const c = SHOTC[s[1]], thin = s[1] === "c";
          el("line", { x1: s[2], y1: s[3], x2: s[4], y2: s[5], stroke: c, "stroke-width": (thin ? 1.1 : 2.2) * k, "stroke-opacity": (thin ? 0.6 : 1) * op, "stroke-linecap": "round" }, svg);
          if (!thin) el("circle", { cx: s[4], cy: s[5], r: 3.8 * k, fill: c, "fill-opacity": op, stroke: "#111", "stroke-width": 0.8 * k, "stroke-opacity": op }, svg).appendChild(el("title", {}, null, `${p.date}　${s[0]} ${s[6]} 碼`)); }); });
      const pin = (P[P.length - 1] || {}).pin; if (pin) { const [px, py] = pin; el("line", { x1: px, y1: py, x2: px, y2: py - 16 * k, stroke: "#fff", "stroke-width": 1.5 * k }, svg); el("polygon", { points: `${px},${py - 16 * k} ${px + 9 * k},${py - 12.5 * k} ${px},${py - 9 * k}`, fill: "#ff3b3b" }, svg); } }
    const tb = h("table", "cplays", f); P.slice().reverse().forEach(p => { const tr = h("tr", "", tb), d0 = p.shots[0], dv = p.st == null ? null : p.st - H.par;
      h("td", "", tr, p.date.slice(2).replace(/-/g, "/")); h("td", dv == null ? "" : dv >= 3 ? "bad" : dv <= 0 ? "good" : "", tr, p.st == null ? "" : p.st + " 桿"); h("td", p.putts >= 3 ? "bad" : "", tr, p.putts == null ? "" : p.putts + " 推");
      h("td", "", tr, d0 ? `${d0[0].replace("桿", "").replace("杆", "")} ${d0[6]} 碼` : ""); h("td", d0 && d0[7] === "Fairway" ? "good" : "", tr, d0 ? ({ Fairway: "球道", Rough: "長草", Bunker: "沙坑", Green: "果嶺", Waste: "荒地" }[d0[7]] || "") : ""); });
    return f; };
  const draw = () => { s0.replaceChildren(); s1.replaceChildren(); s2.replaceChildren(); s3.replaceChildren(); strip.replaceChildren();
    if (regions.length > 1) regions.forEach(g => { const b = h("button", "fchip" + (g === region ? " on" : ""), s0, `${g} ${sec.data.filter(c => c.region === g).length}`);
      b.addEventListener("click", () => { region = g; if (sec.data[ci].region !== g) { ci = sec.data.findIndex(c => c.region === g); li = 0; } draw(); fit(); }); });
    paint(); const C = sec.data[ci], L = C.layouts[li] || C.layouts[0];
    sec.data.forEach((c, i) => { if (c.region !== region) return; const b = h("button", "fchip" + (i === ci ? " on" : ""), s1, `${c.short} ${c.n}`); b.addEventListener("click", () => { ci = i; li = 0; draw(); }); });
    if (C.layouts.length > 1) C.layouts.forEach((l, i) => { const b = h("button", "fchip" + (i === li ? " on" : ""), s2, l.label || `區 ${i + 1}`); b.addEventListener("click", () => { li = i; draw(); }); });
    strip.style.setProperty("--per", NARROW() ? 1 : Math.min(3, L.holes.length));
    L.holes.forEach((H, i) => { const f = fig(L, H); strip.appendChild(f); const b = h("button", "fchip", s3, H.h); b.addEventListener("click", () => strip.scrollTo({ left: f.offsetLeft - strip.offsetLeft, behavior: "smooth" })); });
    strip.scrollLeft = 0; };
  draw();
};

DRAW.caddie = (sec, host) => { const CL = REPORT.clubs || {};
  const M = sec.model, LIEZH = { T: "發球台", F: "球道", R: "長草", B: "沙坑", G: "果嶺", W: "水" };
  const nowT = () => { let k = 0; try { k = +localStorage.getItem("__cad_skew") || 0; } catch (e) {} return Date.now() + k; };
  let ci = 0, li = 0, hi = 0, pos = null, posSrc = "def", lie = "T", lieAuto = true, shots = [], meta = { pen: 0, putts: null, on: null, steps: null }, sugClub = null, allKey = "", pend = null, fix = null, browse = false, play = null, manual = false, perm = "", watchId = null, watchT0 = 0, watchMode = "", watchStop = 0, gotFix = false, lastLogT = 0, lastKind = "", saidFix = false, gpsPt = null, moving = false, stale = false, view = null, viewKey = "", teeSeen = null, stillT = 0, lastFixPt = null, curFull = null, curV = null, curCh = 400, teeSug = null, stopT = 0, sim = false, simMan = false, simDec = false;
  const SAME = 10, MOVE = 4, ACC = 45, HOLE = 120, TEE = 40, TEE_FAR = 70, TEE_WAIT = 150000, NUM = "①②③④⑤⑥⑦⑧⑨⑩";
  const TK = "mi-caddie-shots", today = (() => { const d = new Date(), p2 = v => String(v).padStart(2, "0"); return d.getFullYear() + "-" + p2(d.getMonth() + 1) + "-" + p2(d.getDate()); })(), tkey = () => { const { C, H } = cur(); return `${C.short}|${li}|${H.h}`; };
  try { localStorage.removeItem("mi-caddie-track"); } catch (e) {}
  const DK = "mi-caddie-days", daysRead = () => { try { return JSON.parse(localStorage.getItem(DK) || "{}") || {}; } catch (e) { return {}; } };
  try { const o0 = JSON.parse(localStorage.getItem(TK) || "null"); if (o0 && o0.day && o0.day !== today && o0.holes && Object.keys(o0.holes).length) { const A = daysRead(); A[o0.day] = o0; const ks = Object.keys(A).sort(); while (ks.length > 14) delete A[ks.shift()]; localStorage.setItem(DK, JSON.stringify(A)); localStorage.setItem(TK, JSON.stringify({ day: today, holes: {} })); } } catch (e) {}
  const TKN = () => sim ? "mi-caddie-sim" : TK, tstoreReal = () => { try { const o = JSON.parse(localStorage.getItem(TK) || "{}"); return o && o.day === today && o.holes ? o : { day: today, holes: {} }; } catch (e) { return { day: today, holes: {} }; } }; const tstore = () => { try { const o = JSON.parse(localStorage.getItem(TKN()) || "{}"); return o && o.day === today && o.holes ? o : { day: today, holes: {} }; } catch (e) { return { day: today, holes: {} }; } };
  const twrite = o => { try { localStorage.setItem(TKN(), JSON.stringify(o)); } catch (e) {} };
  const tsave = () => { const o = tstore(), k = tkey(); if (shots.length || meta.pen || meta.putts != null || meta.on != null || meta.steps != null) o.holes[k] = Object.assign({}, o.holes[k] || {}, { s: shots, pen: meta.pen || 0, putts: meta.putts, on: meta.on, steps: meta.steps, st: meta.st || undefined }); else delete o.holes[k]; twrite(o); };
  const loadShots = () => { const r = tstore().holes[tkey()]; shots = r && Array.isArray(r.s) ? r.s.filter(s => s && Array.isArray(s.p) && isFinite(s.p[0]) && isFinite(s.p[1])).map(s => Object.assign({ p: [+s.p[0], +s.p[1]], c: s.c || null, t: s.t || 0 }, s.g ? { g: s.g } : {}, s.f ? { f: s.f } : {}, s.l ? { l: s.l } : {}, s.pen ? { pen: +s.pen } : {}, s.auto ? { auto: 1 } : {}, s.ins ? { ins: 1 } : {}, s.est ? { est: 1 } : {})) : []; meta = { pen: r && r.pen || 0, putts: r && r.putts != null ? r.putts : null, on: r && r.on != null ? r.on : null, steps: r && r.steps != null ? r.steps : null, st: r && r.st || 0 }; pos = shots.length ? shots[shots.length - 1].p.slice() : null; posSrc = "def"; };
  let logUI = null; const LK = "mi-caddie-log", logRead = () => { try { return JSON.parse(localStorage.getItem(LK) || "[]"); } catch (e) { return []; } };
  const clog = (ev, d) => { try { const a = logRead(), c = cur(); a.push(Object.assign({ t: new Date().toISOString(), ev, c: c.C.short, l: c.L.label || li, h: c.H.h || hi + 1 }, sim ? { sim: 1 } : {}, d || {})); if (a.length > 8000) a.splice(0, a.length - 8000); for (let k = 0; k < 6; k++) { try { localStorage.setItem(LK, JSON.stringify(a)); break; } catch (e2) { a.splice(0, Math.min(500, a.length - 1)); } } } catch (e) {} if (logUI) logUI(); };
  const ydH = (H, a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]) * H.k;
  const snap = () => { const H = cur().H, r6 = v => v == null ? null : Math.round(v); return { pos: pos && pos.slice(0, 2).map(r6), src: posSrc, n: shots.length, shots: shots.map(s => [r6(s.p[0]), r6(s.p[1]), s.c || null]), pen: meta.pen, putts: meta.putts, sug: sugClub, toPin: pos ? Math.round(ydH(H, pos, H.pin)) : null, lie, par: H.par, browse, msg: gpsMsg.textContent.slice(0, 120) }; };
  const fixInfo = () => fix ? { lat: fix.lat, lon: fix.lon, acc: fix.acc, age: Math.round((nowT() - fix.t) / 1000), ok: fix.ok } : null;
  const fresh = ms => !!fix && fix.ok && nowT() - fix.t < ms, gpsLive = () => fresh(15000) && !browse && posSrc === "gps";
  const ABR = REPORT.abbr || {}, setClub = (i, c) => { if (!shots[i]) return; shots[i].c = shots[i].c === c ? null : c; tsave(); clog("club", { i: i + 1, club: shots[i].c || null }); };
  const clubChips = (box, i) => { M.bag.forEach(c => { const b = h("button", "ck" + (shots[i].c === c ? " on" : ""), box, ABR[c] || c); b.addEventListener("click", e => { e.stopPropagation(); setClub(i, c); draw(); }); }); };
  const record = (c, why, force, o) => { o = o || {}; const { H } = cur(), last = shots[shots.length - 1], p0 = o.p || (gpsPt && posSrc === "gps" && fresh(15000) ? gpsPt : (pos || H.tee)), REHIT = 45000, gm = last && last.f && fix ? Math.hypot((fix.lat - last.f[0]) * 111200, (fix.lon - last.f[1]) * 111200 * Math.cos(fix.lat * Math.PI / 180)) : 0, same = !force && last && ydH(H, p0, last.p) < SAME && nowT() - (last.t || 0) < REHIT && gm < 12; let st;
    if (same) { if (last.c === c) st = "kept"; else { last.c = c; st = "chg"; } } else { const sh = { p: [Math.round((o.p || p0)[0]), Math.round((o.p || p0)[1])], c, t: nowT() }; if (o.l) sh.l = o.l; if (o.pen) sh.pen = o.pen; if (o.auto) sh.auto = 1; if (o.f) sh.f = o.f; view = null; if (fix) sh.f = [fix.lat, fix.lon, fix.acc, Math.round((nowT() - fix.t) / 1000)]; shots.push(sh); st = "add"; }
    pos = p0.slice(); tsave(); clog("shot", { st, why: why || "tap", i: shots.length, club: c, l: o.l, pen: o.pen, auto: o.auto ? 1 : 0, sug: sugClub, p: shots[shots.length - 1].p, toPin: Math.round(ydH(H, p0, H.pin)), lie, src: posSrc, fix: fixInfo() }); return st; };
  const settlePend = () => { if (!pend) return null; const p0 = pend; pend = null; if (nowT() - p0.t > 60000) return null; const o = tstore(), r = o.holes[p0.key]; if (!r || !Array.isArray(r.s) || !r.s.length || r.s[r.s.length - 1].t !== p0.st) return null; const old = r.s.pop(); if (!r.s.length && !r.pen && r.putts == null && r.on == null && r.steps == null) delete o.holes[p0.key]; twrite(o); return old; };
  let forced = null, bKey = null; const FK = "mi-caddie-force", setForce = () => { if (!browse) return; forced = { day: today, view: [ci, li, hi], gps: bKey }; try { localStorage.setItem(FK, JSON.stringify(forced)); } catch (e) {} clog("force", { gps: bKey }); }, clearForce = () => { if (!forced) return; forced = null; try { localStorage.removeItem(FK); } catch (e) {} };
  const lockHere = () => { forced = { day: today, view: [ci, li, hi], gps: bKey }; browse = true; try { localStorage.setItem(FK, JSON.stringify(forced)); } catch (e) {} };
  const holeGps = key => { if (!navigator.geolocation || !isSecureContext) return; const t0 = Date.now(); try { navigator.geolocation.getCurrentPosition(g => { const gc = g.coords, rec = [+gc.latitude.toFixed(6), +gc.longitude.toFixed(6), Math.round(gc.accuracy * 10) / 10]; clog("holegps", { key, lat: rec[0], lon: rec[1], acc: rec[2], ms: Date.now() - t0 }); try { const o = tstore(), r = o.holes[key]; if (r) { r.hg = rec; twrite(o); } } catch (e) {} }, er => clog("holegps", { key, err: er.code }), { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }); } catch (e) {} };
  const grabGps = (st, key, n) => { if (!navigator.geolocation || !isSecureContext) return; const t0 = Date.now(); try { navigator.geolocation.getCurrentPosition(g => { const gc = g.coords, r1 = v => v == null ? null : Math.round(v * 10) / 10, rec = [+gc.latitude.toFixed(6), +gc.longitude.toFixed(6), r1(gc.accuracy)]; clog("shotgps", { key, i: n, st, lat: rec[0], lon: rec[1], acc: rec[2], alt: r1(gc.altitude), ms: Date.now() - t0 }); try { const o = tstore(), r = o.holes[key], sh = r && Array.isArray(r.s) ? r.s.find(x => x.t === st) : null; if (sh) { sh.g = rec; twrite(o); if (key === tkey()) { const m = shots.find(x => x.t === st); if (m) m.g = rec; } } } catch (e) {} }, er => clog("shotgps", { key, i: n, st, err: er.code, ms: Date.now() - t0 }), { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }); } catch (e) {} };
  const geoOK = () => !!navigator.geolocation && isSecureContext && perm !== "denied";
  let manPos = null, fill = null;
  const pickClub = (c, o) => { setForce(); const last = shots[shots.length - 1]; if (last && last.c === c && nowT() - (last.t || 0) < 5000) { note("kept"); return; } let autoNote = null, teeEst = false; { const { H } = cur(), p0 = gpsPt && posSrc === "gps" && fresh(15000) ? gpsPt : pos; if (!shots.length && meta.putts == null && p0 && ydH(H, p0, H.tee) > 40) { if ((o && o.l === "T") || (H.par >= 4 && c === CL.driver)) { o = Object.assign({}, o || {}, { p: H.tee.map(Math.round), l: "T" }); teeEst = true; } else { const tc = H.par >= 4 ? CL.driver : (teeSug && teeSug.k === keyAt(ci, li, hi) ? teeSug.c : CL.fairway); shots.push({ p: H.tee.map(Math.round), c: tc, t: nowT() - 1, l: "T", est: 1 }); clog("autotee", { club: tc, why: "add" }); autoNote = tc; } } } const stale = posSrc !== "tap" && !fresh(12000) && geoOK() && (!fix || fix.ok), st = record(c, "tap", true, o); pend = stale ? { c, o, t: nowT(), key: tkey(), st: shots[shots.length - 1].t } : null; if (teeEst && shots.length) shots[shots.length - 1].est = 1, tsave(); draw(); note(st, autoNote ? `已自動補開球 ① ${ABR[autoNote] || autoNote}（估）` : teeEst ? "位置放在發球台（估）" : undefined); { const ls = shots[shots.length - 1]; if (ls) grabGps(ls.t, tkey(), shots.length); } if (pend) startWatch("pend"); };
  const undo = () => { if (!shots.length) return; shots.pop(); pend = null; tsave(); draw(); note("undo"); clog("undo", snap()); };
  const say = t => { gpsMsg.replaceChildren(); if (t) h("span", "", gpsMsg, t); };
  const note = (st, extra) => { const { H } = cur(), n = shots.length, last = shots[n - 1], nm = s => ABR[s.c] || s.c || "?", prev = n > 1 ? shots[n - 2] : null; gpsMsg.replaceChildren();
    const pd = prev ? Math.round(ydH(H, prev.p, last.p)) : 0, t = st === "add" ? `已記 ${NUM[n - 1] || n} ${nm(last)}` + (prev ? (pd < SAME ? "・同一個位置再打一桿" : `・上一桿 ${nm(prev)} ${pd} 碼`) : "") : st === "chg" ? `${NUM[n - 1] || n} 改成 ${nm(last)}` : st === "kept" ? `${NUM[n - 1] || n} 已經記了 ${nm(last)}` : st === "undo" ? (n ? `已取消，這一洞剩 ${n} 桿的紀錄` : "已取消，這一洞沒有紀錄") : "";
    h("span", "", gpsMsg, t + (extra ? "　" + extra : "")); if (st === "add") { const b = h("button", "cad-undo", gpsMsg, "取消"); b.addEventListener("click", undo); } };
  const penOf = r => (r.pen || 0) + (Array.isArray(r.s) ? r.s.reduce((a, x) => a + (x && x.pen || 0), 0) : 0), onGuess = r => (Array.isArray(r.s) ? r.s.length : 0) + penOf(r);
  const dvTxt = dv => dv > 0 ? "+" + dv : dv === 0 ? "Par" : "−" + (-dv), scoreTxt = (r, par) => { if (!r || r.putts == null) return ""; const on = r.on != null ? r.on : onGuess(r), tot = on + r.putts; return `${on} ON ${r.putts} 推` + (r.steps != null ? `（${r.steps} 步）` : "") + `＝${tot} 桿（${dvTxt(tot - par)}）`; };
  let edOpen = false, dayOpen = false; const sheet = h("div", "cad-sheet", document.body); sheet.hidden = true;
  const openSheet = (i, j, n) => { const C = sec.data[i], H = C.layouts[j].holes[n], key = `${C.short}|${j}|${H.h}`, isCur = () => key === tkey();
    const get = () => { const r = tstore().holes[key] || {}; return { s: Array.isArray(r.s) ? r.s : [], pen: r.pen || 0, putts: r.putts == null ? null : r.putts, on: r.on == null ? null : r.on, steps: r.steps == null ? null : r.steps }; };
    const set = (f, v) => { if (isCur()) setForce(); const o = tstore(), r = o.holes[key] || { s: [], pen: 0, putts: null }; r[f] = v; let fresh = false; if (!r.st) { r.st = nowT(); fresh = true; if (fix) r.hf = [fix.lat, fix.lon, fix.acc, Math.round((Date.now() - fix.t) / 1000)]; } o.holes[key] = r; twrite(o); if (fresh) holeGps(key); if (isCur()) { meta[f] = v; meta.st = r.st; view = null; } clog("score", { key, on: r.on == null ? null : r.on, putts: r.putts == null ? null : r.putts, steps: r.steps == null ? null : r.steps, n: (r.s || []).length }); };
    const close = () => { sheet.hidden = true; sheet.replaceChildren(); let nAdd = 0; if (i === ci && !fill) { try { nAdd = withHole(j, n, syncEst) || 0; } catch (e) { clog("err", { m: "syncEst " + e.message }); } } draw(); if (nAdd > 0 && !fill) { gpsMsg.replaceChildren(); h("span", "", gpsMsg, `已補上 ${nAdd} 桿，位置是估的（虛線圈）`); const a2 = h("button", "cad-undo", gpsMsg, "現在改"); a2.addEventListener("click", ev => { ev.stopPropagation(); gpsMsg.replaceChildren(); startFill(j, n); }); } };
    const build = () => { sheet.replaceChildren(); const bg = h("div", "bg", sheet), box = h("div", "box", sheet), r = get(), guess = onGuess(r); bg.addEventListener("click", close);
      const hd = h("div", "hd", box); h("b", "", hd, `記錄成績　${C.layouts.length > 1 && C.layouts[j].label ? C.layouts[j].label + " " : ""}第 ${H.h} 洞　Par ${H.par}`); const tot = r.putts != null && (r.on != null || guess) ? (r.on != null ? r.on : guess) + r.putts : null; h("span", "tot", hd, tot == null ? "" : `${tot} 桿（${dvTxt(tot - H.par)}）`);
      const row = (lab, f, vals, sg) => { h("div", "lab", box, lab); const g = h("div", "nums", box); vals.forEach(v => { const b = h("button", (r[f] === v ? "on" : "") + (r[f] == null && sg === v ? " sg" : ""), g, v); b.addEventListener("click", () => { set(f, r[f] === v ? null : v); build(); }); }); };
      row("幾桿上果嶺（ON）" + (guess ? `　已記 ${guess} 桿` : ""), "on", [1, 2, 3, 4, 5, 6, 7, 8, 9], guess || null); row("推桿", "putts", [0, 1, 2, 3, 4, 5]); row("第一推幾步", "steps", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30]);
      const ok = h("button", "ok", box, "完成"); ok.addEventListener("click", close); };
    sheet.hidden = false; build(); };
  const prevOwed = () => { if (meta.putts != null || shots.length) return null; const ow = owing(); return ow.length ? ow[ow.length - 1] : null; };
  const owing = () => { const o = tstore(), out = [], C = sec.data[ci], seen = o.seen || {}; C.layouts.forEach((L, j) => L.holes.forEach((H, n) => { if (j === li && n === hi) return; const k = `${C.short}|${j}|${H.h}`, r = o.holes[k], t = Math.max(0, r && Array.isArray(r.s) && r.s.length ? r.s[r.s.length - 1].t || 1 : 0); if (t && !(r && r.putts != null)) out.push([ci, j, n, (C.layouts.length > 1 && L.label ? L.label + " " : "") + `第 ${H.h} 洞`, t, !!(r && Array.isArray(r.s) && r.s.length)]); })); out.sort((a, b) => a[4] - b[4]); return out.filter((w, x) => w[5] || x === out.length - 1); };
  const dayRows = () => { const o = tstore(), C = sec.data[ci], rows = []; let ord = 0; C.layouts.forEach((L, j) => L.holes.forEach((Hx, n) => { const r = o.holes[`${C.short}|${j}|${Hx.h}`]; ord++; if (!r || r.putts == null) return; const on = r.on != null ? r.on : onGuess(r), ls = (r.s || [])[(r.s || []).length - 1]; if (!on) return; rows.push({ j, n, L, Hx, r, on, tot: on + r.putts, dv: on + r.putts - Hx.par, k: r.st || (ls && ls.t) || 0, o: ord }); })); rows.sort((a, b) => (a.k && b.k ? a.k - b.k : 0) || a.o - b.o); return rows; };
  const ydOf = (Hx, arr, i, isC, rec) => { const st = arr[i], nx = arr[i + 1]; if (!st) return null; if (nx) return { y: Math.round(ydH(Hx, st.p, nx.p)) }; if (!rec && isC && pos && ydH(Hx, pos, st.p) >= SAME) return { y: Math.round(ydH(Hx, st.p, pos)) }; if (rec || !isC) return { y: Math.round(ydH(Hx, st.p, Hx.pin)), est: 1 }; return null; };
  const shotYd = i => ydOf(cur().H, shots, i, true, meta.putts != null);
  const HAZ = { W: "下水", O1: "OB", O2: "OB" }, shotTag = (Hx, arr, i, isC, rec) => { const st = arr[i], nx = arr[i + 1]; if (!st) return { t: "" }; if (nx && HAZ[nx.l]) return { t: "", haz: HAZ[nx.l] }; const yd = ydOf(Hx, arr, i, isC, rec); if (!yd) return { t: "…" }; if (yd.y < 5 && !yd.est) return { t: st.l === "B" && nx && nx.l === "B" ? "沒出沙坑" : "原地", word: 1 }; return { t: String(yd.y), est: !!(yd.est || st.est) }; };
  const groupsAll = C => { const g = []; C.layouts.forEach((L, j) => { for (let a = 0; a < L.holes.length; a += 9) g.push({ j, a, b: Math.min(a + 9, L.holes.length), name: L.holes.length > 9 ? (a === 0 ? "前九" : "後九") : C.layouts.length > 1 ? (L.label || `區 ${j + 1}`) : "" }); }); return g; };
  const holeRec = (o, C, j, n) => { const Hx = C.layouts[j].holes[n], r = o.holes[`${C.short}|${j}|${Hx.h}`]; if (!r) return { Hx }; if (r.putts != null) { const on = r.on != null ? r.on : onGuess(r); if (on) return { Hx, r, tot: on + r.putts, dv: on + r.putts - Hx.par }; } return { Hx, r, open: !!((r.s || []).length) }; };
  const grpSum = (o, C, g) => { let n = 0, d = 0, t = 0; for (let k = g.a; k < g.b; k++) { const x = holeRec(o, C, g.j, k); if (x.dv != null) { n++; d += x.dv; t += x.tot; } } return { n, d, t }; };
  const LV = dv => dv <= -1 ? "b" : dv === 0 ? "p" : dv === 1 ? "g" : dv === 2 ? "d" : "x";
  const scCell = (box, o, C, j, n, isCur, onTap) => { const x = holeRec(o, C, j, n), c = h("div", "c" + (n % 9 === 0 && n ? "" : "") + (isCur ? " cur" : ""), box); h("u", "", c, x.Hx.par); if (x.dv != null) { h("b", LV(x.dv), c, x.dv > 0 ? x.dv : x.dv === 0 ? "●" : "−" + (-x.dv)); h("i", x.r.putts >= 3 ? "t" : "", c, "•".repeat(Math.min(x.r.putts, 4))); } else { h("b", "e" + (x.open ? " o" : ""), c, x.open ? "…" : ""); h("i", "", c, ""); } if (x.r && ((missOf(x.r) > 0 && (x.r.s || []).length) || estOf(x.r))) h("em", "q", c, "?"); if (onTap) c.addEventListener("click", e => { e.stopPropagation(); onTap(j, n); }); return c; };
  const insIdx = q => { const { H } = cur(), n = shots.length; if (!n) return 0; const P = shots.map(z => z.p), end = meta.putts != null ? H.pin : (pos && ydH(H, pos, P[n - 1]) >= SAME ? pos : H.pin), pts = P.concat([end]); let best = null; for (let i = 0; i < pts.length - 1; i++) { const d = segDist(q, pts[i], pts[i + 1]) * H.k; if (!best || d < best.d) best = { d, i }; } if (best && best.d <= 25) return best.i + 1; const tp = ydH(H, q, H.pin), k = shots.findIndex(z => ydH(H, z.p, H.pin) < tp); return k < 0 ? n : k; };
  const askInsert = t => { setTimeout(() => { if (nowT() - swipeT < 700) return; const { H, geo } = cur(), q = [Math.round(t.x), Math.round(t.y)], k = insIdx(q), sv = mapBox.querySelector("svg"); mapBox.querySelectorAll(".cad-ins").forEach(z => z.remove()); if (sv) { const r = 5 / H.k; el("circle", { cx: q[0], cy: q[1], r, fill: "#ffd23f", stroke: "#000", "stroke-width": r / 4, class: "cad-ins" }, sv); } gpsMsg.replaceChildren(); h("span", "", gpsMsg, `在這裡補一桿？（變成第 ${k + 1} 桿）`); const ok = h("button", "cad-undo", gpsMsg, "補一桿"), no = h("button", "cad-undo", gpsMsg, "取消"); no.addEventListener("click", () => { gpsMsg.replaceChildren(); mapBox.querySelectorAll(".cad-ins").forEach(z => z.remove()); }); ok.addEventListener("click", () => { const prev = shots[k - 1], nx = shots[k], l0 = autoLie(q, H, geo), sh = { p: q, c: null, t: prev && nx ? Math.round(((prev.t || 0) + (nx.t || 0)) / 2) : prev ? (prev.t || 0) + 1 : 0, ins: 1 }; if (l0 === "F" || l0 === "R" || l0 === "B") sh.l = l0; if (k === 0) sh.l = "T"; shots.splice(k, 0, sh); tsave(); clog("ins", { i: k + 1, p: q, key: tkey() }); gpsMsg.replaceChildren(); draw(); openClubs(k, { key: tkey(), arr: shots, save: tsave, back: () => openHoleDay(li, hi) }); }); }, 360); };
  const missOf = r => r && r.on != null && r.putts != null ? Math.max(0, r.on - (Array.isArray(r.s) ? r.s.length : 0) - penOf(r)) : 0, estOf = r => r && Array.isArray(r.s) ? r.s.filter(z => z && (z.est || z.cest)).length : 0, medOf = c => (M.clubs[c] && M.clubs[c].med) || 110;
  const genMissing = () => { const { H, geo } = cur(), miss = missOf({ on: meta.on, putts: meta.putts, s: shots, pen: meta.pen }); if (miss <= 0) return 0; const P = pathOf(H, geo), sOf = p => P.proj(p).s, at = s0 => { const a = P.at(Math.max(0, Math.min(P.total, s0))); return [Math.round(a[0]), Math.round(a[1])]; }, clubAt = rem => rem <= 60 ? CL.chip : ((route(Math.max(1, Math.round(rem)), "F")[0] || {}).club || CL.chip); let left = miss; const add = []; if (!shots.length || ydH(H, shots[0].p, H.tee) > 40) { add.push({ p: H.tee.map(Math.round), c: H.par >= 4 ? CL.driver : (teeSug && teeSug.k === keyAt(ci, li, hi) ? teeSug.c : CL.fairway), l: "T", first: 1 }); left--; } const lastP = shots.length ? shots[shots.length - 1].p : H.tee, lastC = shots.length ? shots[shots.length - 1].c : (add[0] && add[0].c), sA = sOf(lastP), end = P.total, lim = end - 15;
    if (left > 0) { const nom = []; if (sA > end - 25) { for (let k = 0; k < left; k++) { const s = sA + (end - 4 - sA) * (k + 1) / (left + 1); nom.push({ s, c: CL.chip }); } } else { let s = sA, c = lastC; for (let k = 0; k < left; k++) { s += medOf(c); c = clubAt(end - s); nom.push({ s, c }); } const zl = nom[nom.length - 1]; if (zl.s > lim || zl.s + medOf(zl.c) < end - 25) nom.forEach((z, k) => { z.s = sA + (lim - sA) * (k + 1) / left; z.c = clubAt(end - z.s); }); } nom.forEach(z => add.push({ p: at(z.s), c: z.c, l: end - z.s <= 30 ? "R" : "F" })); }
    add.forEach(z => { const sh = { p: z.p, c: z.c, t: 0, est: 1, cest: 1, ins: 1, l: z.l }; let idx = z.first ? 0 : shots.length; if (!z.first) { const sz = sOf(z.p); for (let i = 1; i < shots.length; i++) if (sOf(shots[i].p) > sz + 3) { idx = i; break; } } const pv = shots[idx - 1], nx = shots[idx]; sh.t = pv && nx ? Math.round(((pv.t || 0) + (nx.t || 0)) / 2) : pv ? (pv.t || 0) + 1 : nx ? (nx.t || 1) - 1 : nowT(); shots.splice(idx, 0, sh); }); tsave(); clog("genmiss", { key: tkey(), n: add.length }); return add.length; };
  const syncEst = () => { if (meta.putts == null || meta.on == null) return 0; let ex = shots.length + penOf({ s: shots, pen: meta.pen }) - meta.on; if (ex > 0) { for (let k = shots.length - 1; k >= 0 && ex > 0; k--) if (shots[k].est) { shots.splice(k, 1); ex--; } tsave(); } return genMissing(); };
  const withHole = (j, n, fn) => { if (j === li && n === hi) return fn(); const sv = { li, hi, shots, meta, pos, posSrc, lie, lieAuto }; li = j; hi = n; loadShots(); try { return fn(); } finally { li = sv.li; hi = sv.hi; shots = sv.shots; meta = sv.meta; pos = sv.pos; posSrc = sv.posSrc; lie = sv.lie; lieAuto = sv.lieAuto; } };
  const startEdit = (j, n, i, kind) => { if (fill) endFill(); const back = j === li && n === hi ? null : [ci, li, hi]; if (back) { li = j; hi = n; loadShots(); } const { H, geo } = cur(); if (kind === "ins") { const pv = shots[i - 1], nx = shots[i], a = pv ? pv.p : H.tee, b = nx ? nx.p : H.pin, p = i === 0 ? H.tee.map(Math.round) : [Math.round((a[0] + b[0]) / 2), Math.round((a[1] + b[1]) / 2)], l0 = i === 0 ? "T" : autoLie(p, H, geo), sh = { p, c: i === 0 && H.par >= 4 ? CL.driver : (ydH(H, p, H.pin) <= 60 ? CL.chip : ((route(Math.round(ydH(H, p, H.pin)), "F")[0] || {}).club || CL.chip)), t: pv && nx ? Math.round(((pv.t || 0) + (nx.t || 0)) / 2) : pv ? (pv.t || 0) + 1 : nx ? (nx.t || 1) - 1 : nowT(), ins: 1, est: 1, cest: 1 }; if (l0 === "T" || l0 === "F" || l0 === "R" || l0 === "B") sh.l = l0; shots.splice(i, 0, sh); tsave(); clog("ins", { i: i + 1, key: tkey(), via: "edit" }); } if (!shots[i]) { if (back) { li = back[1]; hi = back[2]; loadShots(); } draw(); return; } fill = { kind, back, i, orig: shots[i].p.slice(), origC: shots[i].c, seen: [shots[i].t] }; draw(); zoomFill(); draw(); };
  const zoomFill = () => { if (!fill || !curFull) return; const { H } = cur(), s0 = shots[fill.i]; if (!s0) return; const qs = [s0.p, (shots[fill.i - 1] || {}).p || H.tee, (shots[fill.i + 1] || {}).p || H.pin], mg = 30 / H.k, qx = qs.map(q => q[0]), qy = qs.map(q => q[1]), asp = curFull[2] / curFull[3]; let hg = Math.max(Math.max(...qy) - Math.min(...qy) + 2 * mg, (Math.max(...qx) - Math.min(...qx) + 2 * mg) / asp, 120 / H.k); hg = Math.min(hg * 1.25, curFull[3]); const wd = hg * asp, cx = (Math.max(...qx) + Math.min(...qx)) / 2, cy = (Math.max(...qy) + Math.min(...qy)) / 2 - hg * 0.12; view = [cx - wd / 2, cy - hg / 2, wd, hg]; viewKey = [ci, li, hi].join("|"); };
  const endFill = msg => { const b = fill && fill.back; fill = null; mapBox.classList.remove("filling"); mapBox.querySelectorAll(".cad-fill").forEach(z => z.remove()); if (b) { ci = b[0]; li = b[1]; hi = b[2]; loadShots(); } view = null; draw(); if (msg) say(msg); };
  const startFill = (j, n) => { if (fill) endFill(); const back = j === li && n === hi ? null : [ci, li, hi]; if (back) { li = j; hi = n; loadShots(); } genMissing(); const idx = shots.findIndex(z => z.est || z.cest); if (idx < 0) { if (back) { ci = back[0]; li = back[1]; hi = back[2]; loadShots(); } draw(); say("這一洞沒有要補的桿"); return; } fill = { back, i: idx, seen: [shots[idx].t] }; clog("fill", { key: tkey(), n: estOf({ s: shots }) }); draw(); zoomFill(); draw(); };
  const fillNext = () => { const s0 = shots[fill.i]; if (s0) { if (fill.moved) { delete s0.est; const { H, geo } = cur(), P = pathOf(H, geo), sOf = p => P.proj(p).s; if (fill.i > 0) { shots.splice(fill.i, 1); let idx = shots.length; const sz = sOf(s0.p); for (let i = 1; i < shots.length; i++) if (sOf(shots[i].p) > sz + 3) { idx = i; break; } const pv = shots[idx - 1], nx = shots[idx]; s0.t = pv && nx ? Math.round(((pv.t || 0) + (nx.t || 0)) / 2) : pv ? (pv.t || 0) + 1 : s0.t; shots.splice(idx, 0, s0); fill.seen.push(s0.t); } } clog("fillok", { i: shots.indexOf(s0) + 1, club: s0.c, est: s0.est ? 1 : 0, cest: s0.cest ? 1 : 0, p: s0.p }); } tsave(); fill.moved = false; const nx = shots.findIndex(z => (z.est || z.cest) && !fill.seen.includes(z.t)); if (nx < 0) { endFill("這一洞補完了 ✓"); return; } fill.i = nx; fill.seen.push(shots[nx].t); zoomFill(); draw(); };
  const fillUI = () => { mapBox.querySelectorAll(".cad-fill").forEach(z => z.remove()); mapBox.classList.toggle("filling", !!fill); if (!fill) return; const s0 = shots[fill.i]; if (!s0) { fill = null; mapBox.classList.remove("filling"); return; } const ed = fill.kind === "move" || fill.kind === "ins", left = ed ? 0 : shots.filter(z => (z.est || z.cest) && !fill.seen.includes(z.t)).length, bar = h("div", "cad-fill", mapBox), t1 = h("div", "ft", bar); h("b", "", t1, fill.kind === "move" ? `移動第 ${fill.i + 1} 桿的位置` : fill.kind === "ins" ? `補上的第 ${fill.i + 1} 桿` : `補第 ${fill.i + 1} 桿`); if (!ed) { h("span", "", t1, left ? `之後還有 ${left} 桿` : "最後一桿"); const x = h("button", "x", t1, "之後再說 ✕"); x.addEventListener("click", ev => { ev.stopPropagation(); clog("fillstop", {}); endFill(); }); } else h("span", "", t1, ""); h("div", "fl", bar, ed ? "球桿" : "① 選球桿（虛線框是系統猜的）"); const cs = h("div", "fc", bar); CLUB_ORDER.forEach(cc => { const b = h("button", (s0.c === cc ? "on" : "") + (s0.cest && s0.c === cc ? " g" : ""), cs, ABR[cc] || cc); b.addEventListener("click", ev => { ev.stopPropagation(); s0.c = cc; delete s0.cest; tsave(); fillUI(); }); }); const fb = h("div", "fb", bar); if (ed) { h("span", "", fb, "拖動閃爍的黃點，或點圖上的新位置"); const no = h("button", "no", fb, "取消"), ok = h("button", "ok", fb, "確定"); no.addEventListener("click", ev => { ev.stopPropagation(); const z = shots[fill.i]; if (fill.kind === "ins") shots.splice(fill.i, 1); else if (z) { z.p = fill.orig.slice(); z.c = fill.origC; } tsave(); clog("editcancel", { kind: fill.kind }); endFill("沒有改"); }); ok.addEventListener("click", ev => { ev.stopPropagation(); const z = shots[fill.i]; if (z) { delete z.est; if (fill.kind === "ins" && meta.on != null) { let ex = shots.length + penOf({ s: shots, pen: meta.pen }) - meta.on; for (let k = shots.length - 1; k >= 0 && ex > 0; k--) if (shots[k].est && shots[k] !== z) { shots.splice(k, 1); ex--; } } } tsave(); clog("editok", { kind: fill.kind, i: fill.i + 1, p: z && z.p, club: z && z.c }); endFill(fill.kind === "move" ? `第 ${fill.i + 1} 桿的位置已更新` : `已補上第 ${fill.i + 1} 桿`); }); } else { h("span", "", fb, fill.moved || !s0.est ? "② 位置好了" : "② 點圖上的位置，或拖動閃爍的黃點"); const ok = h("button", "ok", fb, left ? "這一桿好了 →" : "完成 ✓"); ok.addEventListener("click", ev => { ev.stopPropagation(); fillNext(); }); } };
const openHoleDay = (j, n) => { const C = sec.data[ci], Lx = C.layouts[j], Hx = Lx.holes[n], key = `${C.short}|${j}|${Hx.h}`, isC = key === tkey(), o = tstore(), r = o.holes[key] || null, arr = isC ? shots : (r && Array.isArray(r.s) ? r.s : []), rec = r && r.putts != null; sheet.replaceChildren(); sheet.hidden = false; const bg = h("div", "bg", sheet), box = h("div", "box", sheet), close = () => { sheet.hidden = true; sheet.replaceChildren(); }; bg.addEventListener("click", close);
    const hd = h("div", "hd", box); const bk = h("button", "bk", hd, "‹"); bk.addEventListener("click", () => openRound()); h("b", "", hd, `${C.layouts.length > 1 && Lx.label ? Lx.label + " " : ""}第 ${Hx.h} 洞　Par ${Hx.par}`); const x = h("button", "x", hd, "✕"); x.addEventListener("click", close);
    const sr = h("div", "owe", box); h("span", "", sr, rec ? scoreTxt(r, Hx.par) : "還沒記成績"); { const ms = missOf(r), es = estOf(r); if (ms > 0 || es) { const fr = h("div", "owe fillrow", box); h("span", "", fr, ms > 0 ? `漏了 ${ms} 桿` + (es ? `、${es} 桿是估的` : "") : `${es} 桿是估的，可以確認`); const fb = h("button", "", fr, "開始補"); fb.addEventListener("click", () => { sheet.hidden = true; sheet.replaceChildren(); startFill(j, n); }); } } const sb = h("button", "", sr, rec ? "修改成績" : "記錄成績"); sb.addEventListener("click", () => { close(); openSheet(ci, j, n); });
    h("div", "lab", box, "擊球（點一桿可以改球桿、球位、下水）"); const sl = h("div", "shots", box); if (!arr.length) h("div", "none", sl, "這一洞沒有記擊球");
    const tg = { key, arr, save: isC ? tsave : () => { const o2 = tstore(), r2 = o2.holes[key] || { s: [], pen: 0, putts: null }; r2.s = arr; o2.holes[key] = r2; twrite(o2); }, back: () => openHoleDay(j, n) };
    arr.forEach((st, i) => { const b = h("button", "", sl), tg2 = shotTag(Hx, arr, i, isC, rec); h("i", "", b, NUM[i] || i + 1); h("b", "", b, ABR[st.c] || st.c || "?"); h("span", "yd" + (tg2.haz ? " hz" : ""), b, tg2.haz ? tg2.haz : tg2.word ? tg2.t : tg2.t && tg2.t !== "…" ? tg2.t + " 碼" + (tg2.est ? "（估）" : "") : "…"); h("span", "lie", b, (st.l === "W" ? "下水後抛球（罰 1）" : st.l === "O2" ? "OB 特設區（罰 2）" : st.l === "O1" ? "OB 重打（罰 1）" : st.l ? LIEW[st.l] : "") + (st.auto ? "・自動" : "") + (st.est ? "・位置是估的" : "")); h("span", "ed", b, "✎"); b.addEventListener("click", () => { close(); openClubs(i, tg); }); });
    { const row = h("div", "acts", box); if (arr.length && ydH(Hx, arr[0].p, Hx.tee) > 60) { const b = h("button", "", row, "開球沒記到？補一桿"); b.addEventListener("click", () => { arr.unshift({ p: Hx.tee.map(Math.round), c: null, t: 0, l: "T" }); tg.save(); clog("fill", { key }); draw(); openHoleDay(j, n); }); } if (arr.length || rec) { const bx = h("button", "warn", row, "清除這一洞，全部重新記"); let armed = 0; bx.addEventListener("click", () => { if (nowT() - armed < 4000) { const o2 = tstore(); clog("reset", { key, old: o2.holes[key] || null }); arr.splice(0); o2.holes[key] = { s: arr, pen: 0, putts: null, on: null, steps: null }; twrite(o2); if (isC) { pend = null; meta = { pen: 0, putts: null, on: null, steps: null }; view = null; } draw(); openHoleDay(j, n); } else { armed = nowT(); bx.textContent = "再按一次確認清除"; setTimeout(() => { if (bx.isConnected) bx.textContent = "清除這一洞，全部重新記"; }, 4000); } }); } } };
  const runUI = () => { runBox.replaceChildren(); const C = sec.data[ci], o = tstore(), G = groupsAll(C), g0 = G.find(g => g.j === li && hi >= g.a && hi < g.b) || G[0], rows = dayRows();
    const sc = h("div", "rsc csc", runBox); for (let n = g0.a; n < g0.b; n++) scCell(sc, o, C, g0.j, n, g0.j === li && n === hi);
    const R = h("div", "cr", runBox), oth = G.filter(g => g !== g0).map(g => [g, grpSum(o, C, g)]).filter(z => z[1].n).map(z => `${z[0].name || "其他"} ${sgn(z[1].d)}`), sum = rows.reduce((a, r) => a + r.tot, 0), dv = rows.reduce((a, r) => a + r.dv, 0);
    h("span", "o", R, oth.join(" ") || g0.name || "今天"); h("b", "", R, rows.length ? sgn(dv) : "—"); h("span", "t", R, rows.length ? `${rows.length} 洞 ${sum} 桿` : "還沒記"); };
  const openRound = () => { sheet.replaceChildren(); sheet.hidden = false; const bg = h("div", "bg", sheet), box = h("div", "box", sheet), close = () => { sheet.hidden = true; sheet.replaceChildren(); }; if (sim) { const sb0 = h("div", "owe simrow", box); h("span", "", sb0, "模擬模式：這裡的紀錄不算成績"); const cb = h("button", "", sb0, "清空模擬紀錄"); let armed = 0; cb.addEventListener("click", () => { if (nowT() - armed < 4000) { try { localStorage.removeItem("mi-caddie-sim"); } catch (e) {} clog("simclear", {}); close(); fill = null; loadShots(); view = null; draw(); say("模擬紀錄已清空"); } else { armed = nowT(); cb.textContent = "再按一次確認"; } }); } bg.addEventListener("click", close); const rows = dayRows(), { C, L, H } = cur(), o = tstore(), G = groupsAll(C);
    const hd = h("div", "hd", box); h("b", "", hd, (sim ? "模擬・" : "") + (rows.length ? `今天 ${rows.length} 洞　${rows.reduce((a, r) => a + r.tot, 0)} 桿（${sgn(rows.reduce((a, r) => a + r.dv, 0))}）` : "今天的成績")); const x = h("button", "x", hd, "✕"); x.addEventListener("click", close);
    owing().forEach(w => { const row = h("div", "owe", box); h("span", "", row, `${w[3]} 還沒記成績`); const b = h("button", "", row, "補記"); b.addEventListener("click", () => { close(); openSheet(w[0], w[1], w[2]); }); });
    h("div", "lab", box, "每一洞（點一洞可以修改成績和擊球）"); const used = G.filter(g => C.layouts.length === 1 || g.j === li || grpSum(o, C, g).n || Object.keys(o.holes).some(k => k.startsWith(`${C.short}|${g.j}|`))), gT = g => { let t = Infinity; for (let k = g.a; k < g.b; k++) { const x = holeRec(o, C, g.j, k); if (x.r) t = Math.min(t, x.r.st || ((x.r.s || [])[0] || {}).t || Infinity); } return t; }; used.sort((a, b) => gT(a) - gT(b));
    used.forEach(g => { const row = h("div", "fsc", box); h("span", "nm", row, g.name || ""); const sc = h("div", "rsc", row); for (let n = g.a; n < g.b; n++) scCell(sc, o, C, g.j, n, g.j === li && n === hi, (j2, n2) => openHoleDay(j2, n2)); const s2 = grpSum(o, C, g); h("b", "sub", row, s2.n ? String(s2.t) : ""); });
    if (rows.length) { h("div", "lab", box, "照打的順序"); const t = h("table", "cad-t", box), hr = h("tr", "", t); ["洞", "ON", "推", "步", "桿", "±", "累計"].forEach(z => h("th", "", hr, z)); let run = 0; rows.forEach(r => { run += r.dv; const tr = h("tr", "", t); [(C.layouts.length > 1 && r.L.label ? r.L.label + " " : "") + r.Hx.h, r.on, r.r.putts, r.r.steps == null ? "–" : r.r.steps, r.tot, dvTxt(r.dv), sgn(run)].forEach((z, zi) => h("td", zi === 6 ? "cum" : "", tr, z)); tr.addEventListener("click", () => openHoleDay(r.j, r.n)); }); }
    logBox(box); };
  const sgn = v => v > 0 ? "+" + v : v === 0 ? "±0" : "−" + (-v);
  const LIES = [["T", "發球台", 0], ["F", "球道", 0], ["R", "長草", 0], ["B", "沙坑", 0], ["W", "下水 +1", 1], ["O2", "OB 特設區 +2", 2], ["O1", "OB 重打 +1", 1]], penOfLie = l => { const x = LIES.find(z => z[0] === l); return x ? x[2] : 0; }, LIEW = { T: "發球台", F: "球道", R: "長草", B: "沙坑", W: "下水", O2: "OB 特設區", O1: "OB 重打" };
  const CLUB_ORDER = Object.keys(ABR).filter(cc => !/putter|推桿/i.test(cc));
  const lieNow = () => { const { H, geo } = cur(); if (!shots.length) { const g0 = gpsPt && posSrc === "gps" && fresh(20000) ? gpsPt : null; if (!g0 || ydH(H, g0, H.tee) <= 40) return "T"; } const pt = (gpsPt && fresh(20000)) ? gpsPt : pos; if (!pt) return "F"; const a = autoLie(pt, H, geo); return a === "G" ? "F" : a === "T" ? "F" : a; };
  const openClubs = (ei, tg) => { const A = tg ? tg.arr : shots, edit = ei != null && A[ei] ? A[ei] : null; sheet.replaceChildren(); sheet.hidden = false; const bg = h("div", "bg", sheet), box = h("div", "box", sheet), close = () => { sheet.hidden = true; sheet.replaceChildren(); }; bg.addEventListener("click", close);
    const hd = h("div", "hd", box); h("b", "", hd, edit ? (edit.ins && !edit.c ? `補上的第 ${ei + 1} 桿：選球桿` : `修改第 ${ei + 1} 桿`) : `第 ${shots.length + 1} 桿`); const x = h("button", "x", hd, "✕"); x.addEventListener("click", close);
    let lsel = edit ? (edit.l || (ei === 0 ? "T" : "")) : lieNow(); { const pn = edit ? ei : shots.length; h("div", "lab", box, (pn ? `第 ${pn} 桿打到哪裡` : "從哪裡開球") + (edit ? "（點一下就改好）" : "（已幫你選好，不對再點）")); } const lr = h("div", "lies", box), lr2 = h("div", "lies p2", box), showT = (edit ? ei === 0 : !shots.length) || lsel === "T";
    const paint = () => [...lr.children, ...lr2.children].forEach(b => b.classList.toggle("on", b.dataset.l === lsel));
    LIES.filter(z => z[0] !== "T" || showT).forEach(z => { const b = h("button", z[2] ? "pen" : "", z[2] ? lr2 : lr, z[1]); b.dataset.l = z[0]; b.addEventListener("click", () => { lsel = lsel === z[0] ? "" : z[0]; paint(); if (edit) { const pen = penOfLie(lsel); if (lsel) edit.l = lsel; else delete edit.l; if (pen) edit.pen = pen; else delete edit.pen; (tg ? tg.save() : tsave()); clog("edit", { i: ei + 1, l: lsel, pen, key: tg ? tg.key : undefined }); draw(); say(`第 ${ei + 1} 桿改成` + (lsel ? LIEW[lsel] : "沒有球位")); } }); }); paint();
    h("div", "lab", box, edit ? "球桿（點一下就改好）" : "這一桿用（點一下就記下）"); const g = h("div", "clubs3", box);
    CLUB_ORDER.forEach(cc => { const b = h("button", (!edit && cc === sugClub ? "sg" : "") + (edit && edit.c === cc ? " on" : ""), g); b.dataset.c = ABR[cc] || cc; h("b", "", b, ABR[cc] || cc); if (!edit && cc === sugClub) h("span", "", b, "建議");
      b.addEventListener("click", () => { close(); const pen = penOfLie(lsel); if (edit) { edit.c = cc; delete edit.cest; if (lsel) edit.l = lsel; else delete edit.l; if (pen) edit.pen = pen; else delete edit.pen; delete edit.auto; (tg ? tg.save() : tsave()); clog("edit", { i: ei + 1, club: cc, l: lsel, pen, key: tg ? tg.key : undefined }); draw(); if (tg && tg.back) tg.back(); say(`第 ${ei + 1} 桿改成 ${ABR[cc] || cc}` + (lsel ? `・${LIEW[lsel]}` : "")); } else pickClub(cc, { l: lsel || undefined, pen: pen || undefined }); }); });
    if (edit) { const jn = (() => { const [sh, jx, hx] = (tg ? tg.key : tkey()).split("|"), C0 = sec.data[ci], L0 = C0.layouts[+jx]; return [+jx, L0 ? L0.holes.findIndex(z => String(z.h) === hx) : hi]; })(), ac = h("div", "acts3", box), mk = (t, f) => { const b = h("button", "", ac, t); b.addEventListener("click", () => { close(); f(); }); }; mk("調整位置", () => startEdit(jn[0], jn[1], ei, "move")); mk("前面補一桿", () => startEdit(jn[0], jn[1], ei, "ins")); mk("後面補一桿", () => startEdit(jn[0], jn[1], ei + 1, "ins")); const del = h("button", "dl", ac, "刪除"); del.addEventListener("click", () => { close(); A.splice(ei, 1); if (A === shots) pend = null; (tg ? tg.save() : tsave()); clog("del", { i: ei + 1, key: tg ? tg.key : undefined }); draw(); if (tg && tg.back) tg.back(); say(`已刪除第 ${ei + 1} 桿`); }); } };
  const nextRow = h("div", "cad-next"), nextUI = () => { const { H } = cur(), done = meta.putts != null, on = meta.on != null ? meta.on : onGuess({ s: shots, pen: meta.pen }); { const n = shots.length, last = shots[n - 1], { H } = cur(), here = gpsPt && fresh(20000) && posSrc !== "tap" ? gpsPt : pos, atLast = false; editBtn.hidden = true; addBtn.classList.toggle("sm", atLast); if (atLast) { editBtn.replaceChildren(); h("b", "", editBtn, `${NUM[n - 1] || n} ${ABR[last.c] || last.c || "?"}` + (last.l ? `・${LIEW[last.l]}` : "") + (last.auto ? "（自動）" : "")); h("span", "", editBtn, "✎ 修改"); addBtn.textContent = "＋"; addBtn.setAttribute("aria-label", `新增第 ${n + 1} 桿`); } else { addBtn.replaceChildren(); h("b", "", addBtn, "＋"); h("span", "", addBtn, `第 ${n + 1} 桿`); addBtn.setAttribute("aria-label", `新增第 ${n + 1} 桿`); } addBtn.classList.toggle("nx", done); { const la = shots[shots.length - 1], Hn = cur().H, near = !(gpsPt && fresh(15000)) || ydH(Hn, gpsPt, la ? la.p : Hn.tee) < 30; againBtn.hidden = !(la && la.c && !done && nowT() - (la.t || 0) < 180000 && near); if (!againBtn.hidden) { againBtn.replaceChildren(); h("span", "", againBtn, "再一次"); h("b", "", againBtn, ABR[la.c] || la.c); } } gpsRow.hidden = done || !!fill; } finBtn.replaceChildren(); const pw = prevOwed(); h("span", "ic", finBtn, done ? "✎" : "⛳"); h("span", "", finBtn, done ? "修改紀錄" : pw ? "補記 " + pw[3].replace(/第 | 洞/g, "").replace(" ", "") : "記錄成績"); finBtn.classList.toggle("done", done); finBtn.classList.toggle("owe", !!pw); };
  const gpsRow = h("div", "cad-gps", host), gpsBtn = h("button", "cad-here"), editBtn = h("button", "cad-edit", gpsRow), addBtn = h("button", "cad-add", gpsRow), againBtn = h("button", "cad-again", gpsRow), foot = h("div", "cad-foot"), runBox = h("button", "cad-card", foot), finBtn = h("button", "cad-finb", foot), gpsMsg = h("span", "cad-msg", foot), modeBar = null; againBtn.hidden = true; againBtn.addEventListener("click", () => { const la = shots[shots.length - 1]; if (la && la.c) pickClub(la.c, la.l && la.l !== "T" ? { l: la.l } : {}); }); setInterval(() => { try { nextUI(); } catch (e) {} }, 20000); editBtn.hidden = true; editBtn.addEventListener("click", () => openClubs(shots.length - 1)); runBox.addEventListener("click", () => openRound()); h("i", "", gpsBtn); h("span", "", gpsBtn, "定位"); addBtn.addEventListener("click", () => { if (fill) endFill(); if (meta.putts == null) { const H0 = cur().H; if (gpsPt && posSrc === "gps" && fresh(15000) && (shots.length || ydH(H0, gpsPt, H0.tee) > 40) && (!pos || ydH(H0, gpsPt, pos) >= SAME)) { pos = gpsPt.slice(); lieAuto = true; manPos = { k: tkey(), n: shots.length, p: pos.slice() }; draw(); } openClubs(); return; } openHoleDay(li, hi); }); finBtn.addEventListener("click", () => { if (fill) endFill(); const pw = prevOwed(); if (meta.putts != null) openHoleDay(li, hi); else if (pw) openSheet(pw[0], pw[1], pw[2]); else openSheet(ci, li, hi); }); let lastSay = "", msgT = 0; new MutationObserver(() => { clearTimeout(msgT); const t = gpsMsg.textContent; if (t) msgT = setTimeout(() => { gpsMsg.replaceChildren(); mapBox.querySelectorAll(".cad-ins").forEach(z => z.remove()); }, t === "定位中…" ? 5000 : /還沒記|附近？|補一桿？|漏了|已補上/.test(t) ? 25000 : /^已記|改成/.test(t) ? 10000 : /定位|球場|權限|https|出錯|太遠/.test(t) ? 7000 : 5000); }).observe(gpsMsg, { childList: true, characterData: true, subtree: true }); gpsMsg.addEventListener("click", e => { if (e.target.closest("button")) return; clearTimeout(msgT); gpsMsg.replaceChildren(); mapBox.querySelectorAll(".cad-ins").forEach(z => z.remove()); });
  const fromPx = (H, x, y) => { const g = H.ll, X = x - g[4], Y = y - g[7], det = g[2] * g[6] - g[3] * g[5]; return [g[0] + (-g[5] * X + g[2] * Y) / det, g[1] + (g[6] * X - g[3] * Y) / det]; };
  let photoOn = (() => { try { return localStorage.getItem("mi-caddie-photo") !== "0"; } catch (e) { return true; } })(), photoSrc = "";
  const TW = (lat, lon) => lat > 21.7 && lat < 26.5 && lon > 118 && lon < 122.3;
  const tileUrl = (z, x, y, tw) => tw ? `https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/${z}/${y}/${x}` : `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`;
  const drawPhoto = (svg, H, geo, pxPerScreen, zf) => { const pts = (geo.outline || []).flat(); if (!pts.length) [H.tee, H.pin].forEach(p => pts.push(p)); let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity; pts.forEach(p => { x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]); x1 = Math.max(x1, p[0]); y1 = Math.max(y1, p[1]); }); const m = 40 / H.k; x0 -= m; y0 -= m; x1 += m; y1 += m;
    const cs = [[x0, y0], [x1, y0], [x0, y1], [x1, y1]].map(p => fromPx(H, p[0], p[1])), la = cs.map(c => c[0]), lo = cs.map(c => c[1]), latC = (Math.min(...la) + Math.max(...la)) / 2, tw = TW(latC, lo[0]);
    const mPerScreen = pxPerScreen * H.k * 0.9144; let z = zf || Math.round(Math.log2(156543.03 * Math.cos(latC * Math.PI / 180) / Math.max(0.05, mPerScreen / 1.5))); z = Math.max(15, Math.min(tw ? 20 : 19, z));
    const n = 2 ** z, tx = lon => Math.floor((lon + 180) / 360 * n), ty = lat => { const r = lat * Math.PI / 180; return Math.floor((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * n); }, X2 = x => x / n * 360 - 180, Y2 = y => Math.atan(Math.sinh(Math.PI * (1 - 2 * y / n))) * 180 / Math.PI;
    const ax = tx(Math.min(...lo)), bx = tx(Math.max(...lo)), ay = ty(Math.max(...la)), by = ty(Math.min(...la)); if ((bx - ax + 1) * (by - ay + 1) > 90 && z > 15) return drawPhoto(svg, H, geo, pxPerScreen, z - 1);
    const g = el("g", { class: "cad-photo", style: "filter:brightness(.9) saturate(1.08)" }, svg);
    for (let X = ax; X <= bx; X++) for (let Y = ay; Y <= by; Y++) { const p0 = toPx(H, Y2(Y), X2(X)), p1 = toPx(H, Y2(Y), X2(X + 1)), p2 = toPx(H, Y2(Y + 1), X2(X)); const im = el("image", { href: tileUrl(z, X, Y, tw), x: 0, y: 0, width: 1.004, height: 1.004, preserveAspectRatio: "none", transform: `matrix(${p1[0] - p0[0]} ${p1[1] - p0[1]} ${p2[0] - p0[0]} ${p2[1] - p0[1]} ${p0[0]} ${p0[1]})` }, g); im.addEventListener("error", () => im.remove()); im.addEventListener("load", () => { im.classList.add("ok"); const v = svg.querySelector(".cad-vec"); if (v) v.style.display = "none"; const at = mapBox.querySelector(".cad-attr"); if (at) at.hidden = false; }); }
    photoSrc = tw ? "影像：國土測繪中心" : "影像：Esri, Maxar"; return { z, tiles: (bx - ax + 1) * (by - ay + 1) }; };
  const drawSaved = (svg, H) => { const P = (REPORT.photos || {})[`${cur().L.cid}_${H.h}`]; if (!P) return false; const n = 2 ** P.z, X2 = x => x / n * 360 - 180, Y2 = y => Math.atan(Math.sinh(Math.PI * (1 - 2 * y / n))) * 180 / Math.PI, p0 = toPx(H, Y2(P.ay), X2(P.ax)), p1 = toPx(H, Y2(P.ay), X2(P.ax + P.nx)), p2 = toPx(H, Y2(P.ay + P.ny), X2(P.ax)), g = el("g", { class: "cad-photo", style: "filter:brightness(.9) saturate(1.08)" }, svg), im = el("image", { href: `../data/photos/${cur().L.cid}_${H.h}.jpg`, x: 0, y: 0, width: 1, height: 1, preserveAspectRatio: "none", transform: `matrix(${p1[0] - p0[0]} ${p1[1] - p0[1]} ${p2[0] - p0[0]} ${p2[1] - p0[1]} ${p0[0]} ${p0[1]})` }, g); im.addEventListener("error", () => { g.remove(); const v = svg.querySelector(".cad-vec"); if (v) v.style.display = ""; drawPhoto(svg, H, cur().geo, 1); }); im.addEventListener("load", () => { im.classList.add("ok"); const at = mapBox.querySelector(".cad-attr"); if (at) at.hidden = false; }); photoSrc = P.src === "esri" ? "影像：Esri, Maxar" : "影像：國土測繪中心"; return true; };
  const phBtn = h("button", "cad-ph"), rsBtn = h("button", "cad-rs", null, "↺ 回發球台"); let lpT = 0, lpHint = 0; const resetUI = () => { const on = sim && posSrc === "tap"; if (on) mapBox.append(rsBtn); else rsBtn.remove(); }; rsBtn.addEventListener("click", e => { e.stopPropagation(); loadShots(); lieAuto = true; view = null; draw(); clog("tapreset", {}); }); phBtn.addEventListener("click", ev => { ev.stopPropagation(); photoOn = !photoOn; try { localStorage.setItem("mi-caddie-photo", photoOn ? "1" : "0"); } catch (e) {} draw(); });
  const toPx = (H, lat, lon) => { const g = H.ll, dx = lon - g[1], dy = lat - g[0]; return [g[2] * dx + g[3] * dy + g[4], g[5] * dx + g[6] * dy + g[7]]; };
  const segDist = (p, a, b) => { const vx = b[0] - a[0], vy = b[1] - a[1], t = Math.max(0, Math.min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / (vx * vx + vy * vy || 1))); return Math.hypot(p[0] - a[0] - t * vx, p[1] - a[1] - t * vy); };
  const holeDist = (H, p) => { const pp = H.path && H.path.length > 1 ? H.path : [H.tee, H.pin]; let m = Infinity; for (let i = 1; i < pp.length; i++) m = Math.min(m, segDist(p, pp[i - 1], pp[i])); return m * H.k; };
  const locate = (lat, lon) => { let bestH = null; sec.data.forEach((c, i) => { if (!c.ll || Math.abs(c.ll[0] - lat) > 0.05 || Math.abs(c.ll[1] - lon) > 0.05) return; c.layouts.forEach((L, j) => L.holes.forEach((H, n) => { if (!H.ll) return; const p = toPx(H, lat, lon), dd = holeDist(H, p); if (!bestH || dd < bestH.dd) bestH = { dd, i, j, n, p }; })); }); return bestH; };
  const speak = t => { return; try { if (!("speechSynthesis" in window)) return; speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(t.replace(/杆/g, "桿")); u.lang = "zh-TW"; speechSynthesis.speak(u); } catch (e) {} };
  const teeNear = (C, lat, lon, lim, skip) => { let nt = null; C.layouts.forEach((L, j) => L.holes.forEach((H, n) => { if (!H.ll || (skip && j === skip[0] && n === skip[1])) return; const q = toPx(H, lat, lon), t = ydH(H, q, H.tee); if (t < lim && (!nt || t < nt.t)) nt = { t, j, n, p: q }; })); return nt; };
  const autoTee = (H1, p) => { const tk = keyAt(ci, li, hi); if (shots.length || isDone(tk)) return null; const dT = ydH(H1, p, H1.tee); if (dT <= 30) { if (!teeSeen || teeSeen.k !== tk) teeSeen = { k: tk, t: nowT(), f: fix ? [fix.lat, fix.lon, fix.acc] : null }; return null; } if (teeSeen && teeSeen.k === tk && dT >= 40 && ydH(H1, p, H1.pin) < ydH(H1, H1.tee, H1.pin) && nowT() - teeSeen.t >= 20000) { const club = H1.par >= 4 ? CL.driver : (teeSug && teeSug.k === tk ? teeSug.c : CL.fairway); const tf = teeSeen.f; teeSeen = null; record(club, "autotee", true, { p: H1.tee, l: "T", auto: 1, f: tf || undefined }); return club; } return null; };
  const place = (b, p, accYd, man, extra) => { const moved = b.i !== ci || b.j !== li || b.n !== hi, H1 = sec.data[b.i].layouts[b.j].holes[b.n], far = moved || !pos || !gpsPt || posSrc !== "gps" || ydH(H1, p, gpsPt) >= MOVE || stale;
    if (!far && !pend && !man) return { kind: "same", ok: true, changed: false };
    if (fix) fix.ok = true; clearForce(); const pc = settlePend(); ci = b.i; li = b.j; hi = b.n; if (ydH(H1, p, H1.pin) <= 25) markGreen(keyAt(ci, li, hi)); { const o = tstore(); o.seen = o.seen || {}; o.seen[keyAt(ci, li, hi)] = nowT(); twrite(o); } const keepLie = !far && !lieAuto, l0 = lie; loadShots(); gpsPt = p; const ac = null; if (man && (shots.length || ydH(H1, p, H1.tee) > 40)) manPos = { k: tkey(), n: shots.length, p: p.slice() }; if (manPos && manPos.k === tkey() && manPos.n === shots.length) pos = manPos.p.slice(); posSrc = "gps"; play = [ci, li, hi]; browse = false; lieAuto = !keepLie; if (keepLie) lie = l0;
    const st = pc != null ? record(pc.c, "pend", true, { l: pc.l, pen: pc.pen, auto: pc.auto }) : null; draw();
    if (ac) { say(`已自動記開球 ① ${ABR[ac] || ac}・不對可以點右下角清單裡的 ①`); } else if (st) note(st, "位置已更新"); else if (moved) say(`已切到第 ${cur().H.h || hi + 1} 洞` + (extra ? "・" + extra : "")); else if (man) say((extra ? extra + "・" : "已定位・") + `誤差約 ${accYd} 碼`); saidFix = true;
    if (false) { const ow = owing(); if (ow.length) { const w = ow[ow.length - 1]; gpsMsg.replaceChildren(); h("span", "", gpsMsg, `已到第 ${cur().H.h || hi + 1} 洞・${w[3]} 還沒記成績`); const b = h("button", "cad-undo", gpsMsg, "補記"); b.addEventListener("click", () => openSheet(w[0], w[1], w[2])); } }
    if (man) { speak(lastSay); const bb = document.querySelector(".cad-bar"); scrollTo({ top: mapBox.getBoundingClientRect().top + scrollY - (bb ? bb.offsetHeight : 0) - (NARROW() ? 0 : (document.querySelector(".ptabs") || {}).offsetHeight || 0), behavior: "smooth" }); }
    return { kind: moved ? "hole" : "pos", ok: true, changed: true }; };
  const herePin = (lat, lon, accYd, man) => { const yd = H => { const dy = (lat - H.pll[0]) * 121605, dx = (lon - H.pll[1]) * 121605 * Math.cos(lat * Math.PI / 180); return Math.hypot(dx, dy); }; let best = null; const c0 = cur(); if (c0.C.preview && c0.H.pll && yd(c0.H) < 700) best = { i: ci, j: li, n: hi, d: yd(c0.H) }; else if (man || !play) sec.data.forEach((c, i) => { if (!c.preview) return; c.layouts.forEach((L, j) => L.holes.forEach((H, n) => { if (!H.pll) return; const d = yd(H); if (d < 700 && (!best || d < best.d)) best = { i, j, n, d }; })); }); if (!best) return null;
    const H1 = sec.data[best.i].layouts[best.j].holes[best.n], P = pathOf(H1, (REPORT.geo[sec.data[best.i].layouts[best.j].cid] || {})[H1.h] || {}), a = P.at(Math.max(0, P.total - best.d)), r = place(best, [Math.round(a[0]), Math.round(a[1])], accYd, man, `沒打過的球場：只知道離旗桿 ${Math.round(best.d)} 碼`); if (r.changed) { lieAuto = false; lie = best.d < 20 ? "G" : P.total - best.d < 14 ? "T" : "F"; draw(); } return r; };
  const keyAt = (i, j, n) => { const C = sec.data[i], H = C.layouts[j].holes[n]; return `${C.short}|${j}|${H.h}`; };
  const markGreen = k => { const o = tstore(); o.green = o.green || {}; if (!o.green[k]) { o.green[k] = 1; twrite(o); } }, isDone = k => { const r = tstore().holes[k]; return !!(r && r.putts != null); };
  const aim = (b, lat, lon, man) => { const c0 = cur(), vp = c0.H.ll ? toPx(c0.H, lat, lon) : null, vd = vp ? holeDist(c0.H, vp) : Infinity, self = { dd: vd, i: ci, j: li, n: hi, p: vp }; let tgt = b, hint = null;
    const isNext = (j, n) => (j === li && n === hi + 1) || (j !== li && hi === c0.L.holes.length - 1 && n === 0);
    const other = lim => { let best = null; c0.C.layouts.forEach((L, j) => L.holes.forEach((H, n) => { if (!H.ll || (j === li && n === hi)) return; const q = toPx(H, lat, lon), dd = holeDist(H, q); if (dd > lim) return; const sc = dd - (isNext(j, n) ? 1000 : 0); if (!best || sc < best.sc) best = { sc, dd, i: ci, j, n, p: q }; })); return best; };
    if (b.i === ci && b.j === li && b.n === hi) tgt = self;
    else if (vd <= HOLE) { const nt = teeNear(c0.C, lat, lon, TEE_FAR, [li, hi]), myTee = ydH(c0.H, vp, c0.H.tee), myPin = ydH(c0.H, vp, c0.H.pin), fin = isDone(keyAt(ci, li, hi)), fresh0 = !shots.length;
      const lastT = shots.length ? shots[shots.length - 1].t || 0 : 0, waited = nowT() - lastT >= TEE_WAIT;
      if (nt && nt.t < myTee && (fin || fresh0 ? true : nt.t < TEE && waited && nt.t < 0.7 * myPin && myPin > 40)) tgt = { dd: 0, i: ci, j: nt.j, n: nt.n, p: nt.p }; else if (fin && myPin > 30) tgt = other(60) || self; else { tgt = self; if (nt && nt.t < myTee && waited && nt.t < myPin) hint = nt; } }
    else if (!man && play && b.i === ci) { const nt = teeNear(c0.C, lat, lon, TEE_FAR, [li, hi]); tgt = nt ? { dd: 0, i: ci, j: nt.j, n: nt.n, p: nt.p } : (other(50) || null); }
    return { tgt, self, vd, hint }; };
  const showHint = (hn, man, lock) => { if (!man) return; const Hn = cur().C.layouts[hn.j].holes[hn.n], kk = "hint" + hn.j + "|" + hn.n; if (man || lastKind !== kk) { gpsMsg.replaceChildren(); h("span", "", gpsMsg, `你在第 ${Hn.h} 洞發球台附近？`); const bq = h("button", "cad-undo", gpsMsg, `切到第 ${Hn.h} 洞`); bq.addEventListener("click", () => { clog("hint", { to: Hn.h, lock }); clearForce(); li = hn.j; hi = hn.n; loadShots(); pos = [Math.round(hn.p[0]), Math.round(hn.p[1])]; posSrc = "gps"; play = [ci, li, hi]; browse = false; lieAuto = true; if (lock) lockHere(); draw(); const ow = []; gpsMsg.replaceChildren(); if (ow.length) { const w = ow[ow.length - 1]; h("span", "", gpsMsg, `已到第 ${Hn.h} 洞・${w[3]} 還沒記成績`); const b = h("button", "cad-undo", gpsMsg, "補記"); b.addEventListener("click", () => openSheet(w[0], w[1], w[2])); } else say(`已切到第 ${Hn.h} 洞`); }); } lastKind = kk; };
  const here = (lat, lon, acc, man) => { if (fill) return { kind: "fill", ok: true, changed: false }; const accYd = Math.round((acc || 0) * 1.09361), co = `${lat.toFixed(5)}, ${lon.toFixed(5)}（誤差約 ${accYd} 碼）`, once = (k, t) => { if (man) say(t); lastKind = k; };
    if (accYd > ACC) { once("acc", `定位還不準（誤差約 ${accYd} 碼），再等幾秒…`); return { kind: "acc", ok: false, changed: false, wait: true }; }
    let b = locate(lat, lon);
    if (!b || b.dd > HOLE) { const rp = herePin(lat, lon, accYd, man); if (rp) { lastKind = "pin"; return rp; } let nb = null; sec.data.forEach(c => { if (!c.ll) return; const km = Math.hypot((lat - c.ll[0]) * 111.2, (lon - c.ll[1]) * 111.2 * Math.cos(lat * Math.PI / 180)); if (!nb || km < nb.km) nb = { km, s: c.short }; });
      once("off", b ? `你在${sec.data[b.i].short}附近，但不在任何一洞上（離最近的球道 ${Math.round(b.dd)} 碼），圖沒有動。` + (man ? `定位：${co}` : "") : `你不在球場上` + (nb ? `，最近的${nb.s}還有 ${nb.km < 10 ? nb.km.toFixed(1) : Math.round(nb.km)} 公里` : "") + (man ? `。圖上是原本選的那一洞。定位：${co}` : "")); return { kind: "off", ok: false, changed: false }; }
    if (man && play && sec.data[play[0]] && (play[0] !== ci || play[1] !== li || play[2] !== hi)) { ci = play[0]; li = play[1]; hi = play[2]; loadShots(); }
    bKey = keyAt(b.i, b.j, b.n);
    if (forced && !man && isDone(keyAt(ci, li, hi)) && teeNear(cur().C, lat, lon, TEE_FAR, [li, hi])) { clearForce(); browse = false; clog("unlock", { why: "done+tee" }); }   /* 鎖定的那一洞記完成績、人走到別洞發球台：解除鎖定，照定位換洞 */
    if (forced && !man) { const c0 = cur(), vp = c0.H.ll ? toPx(c0.H, lat, lon) : null, vd = vp ? holeDist(c0.H, vp) : Infinity, keep = forced; let out = { kind: "lock", ok: true, changed: false };
      if (vp && vd <= HOLE) { out = place({ dd: vd, i: ci, j: li, n: hi, p: vp }, [Math.round(vp[0]), Math.round(vp[1])], accYd, man); forced = keep; browse = true; try { localStorage.setItem(FK, JSON.stringify(forced)); } catch (e) {} }
      const nt = teeNear(c0.C, lat, lon, TEE, [li, hi]), lastT = shots.length ? shots[shots.length - 1].t || 0 : 0;
      if (nt && (!vp || nt.t < ydH(c0.H, vp, c0.H.tee)) && (!shots.length || isDone(keyAt(ci, li, hi)) || nowT() - lastT >= TEE_WAIT)) showHint(nt, man, true); else lastKind = "lock";
      return out; }
    if (man) clearForce();
    const a = aim(b, lat, lon, man);
    if (browse && !man) { if (a.tgt === a.self) { lastKind = "ok"; return place(a.self, [Math.round(a.self.p[0]), Math.round(a.self.p[1])], accYd, man); } lastKind = "browse"; return { kind: "browse", ok: true, changed: false }; }
    if (!a.tgt) { once("far", `離第 ${cur().H.h || hi + 1} 洞太遠（${Math.round(a.vd)} 碼），圖沒有動。換洞請點洞號，或按「定位」。`); return { kind: "far", ok: true, changed: false }; }
    b = a.tgt;
    lastKind = a.hint ? lastKind : "ok"; const out = place(b, [Math.round(b.p[0]), Math.round(b.p[1])], accYd, man);
    if (a.hint && a.tgt === a.self) showHint(a.hint, man, false);
    return out; };
  const stopWatch = () => { if (watchId != null) { try { navigator.geolocation.clearWatch(watchId); } catch (e) {} watchId = null; } clearTimeout(watchStop); gpsBtn.classList.remove("busy"); if (gpsMsg.textContent === "定位中…") say(""); };
  const onPos = g => { const gc = g.coords, now = nowT(), r1 = v => v == null ? null : Math.round(v * 10) / 10, first = !gotFix, man = manual; gotFix = true; gpsBtn.classList.remove("busy"); if (gpsMsg.textContent === "定位中…") say("");
    fix = { t: now, lat: +gc.latitude.toFixed(6), lon: +gc.longitude.toFixed(6), acc: r1(gc.accuracy), ok: false }; let out, movedStop = false; { const lf = lastFixPt, dm = lf ? Math.hypot((fix.lat - lf.lat) * 111200, (fix.lon - lf.lon) * 111200 * Math.cos(fix.lat * Math.PI / 180)) : 0, dt = lf ? (now - lf.t) / 1000 : 0, spd = gc.speed != null && gc.speed >= 0 ? gc.speed : dt > 0.5 ? dm / dt : 0, wasMoving = moving; if (spd > 1.8 && (gc.accuracy || 99) < 40) { moving = true; stillT = 0; } else if (moving) { if (!stillT) stillT = now; if (now - stillT > 3000) { moving = false; stillT = 0; } } lastFixPt = { lat: fix.lat, lon: fix.lon, t: now }; if (wasMoving !== moving) clog("move", { moving, spd: r1(spd) }); movedStop = wasMoving && !moving; clearTimeout(stopT); if (moving) stopT = setTimeout(() => { if (moving && lastFixPt && nowT() - lastFixPt.t >= 2500) { moving = false; stillT = 0; clog("move", { moving: false, why: "nofix" }); draw(); } }, 3500); } const wasStale = stale; stale = false;
    { const b0 = (gc.accuracy || 999) < 150 ? locate(gc.latitude, gc.longitude) : null, onC = !!(b0 && b0.dd <= 150); if (!simMan && (gc.accuracy || 999) < 150) { if (onC && sim) { setSim(false, false); say("到球場了，切到正式"); } else if (!onC && !sim && !simDec && !Object.keys(tstoreReal().holes || {}).length) { setSim(true, false); say("不在球場，切到模擬"); } else simDec = true; } if (sim && !onC && !man) { if (wasStale) draw(); return; } }
    try { out = here(gc.latitude, gc.longitude, gc.accuracy, man); } catch (e) { say("計算時出錯：" + e.message); clog("err", { m: e.message, s: String(e.stack || "").slice(0, 500) }); manual = false; return; }
    fix.ok = !!out.ok; if (!out.wait) manual = false; gpsBtn.classList.toggle("live", gpsLive()); if (movedStop) draw(); else if (wasStale && !out.changed) draw();
    if (first || out.changed || man || now - lastLogT > 10000) { lastLogT = now; clog("gps", { lat: fix.lat, lon: fix.lon, acc: fix.acc, alt: r1(gc.altitude), ms: now - watchT0, m: watchMode, k: out.kind }); if (out.changed || man) clog("res", snap()); } };
  const onErr = e => { if (stale) { stale = false; draw(); } clog("gpserr", { code: e.code, m: e.message, ms: nowT() - watchT0, mode: watchMode }); const loud = manual || watchMode === "btn"; if (e.code === 1) { perm = "denied"; stopWatch(); manual = false; if (loud) say("沒有定位權限：請到手機的設定打開瀏覽器的「定位服務」，再重新整理這一頁"); return; } gpsBtn.classList.remove("busy"); if (loud || (!gotFix && lastKind !== "err")) say(e.code === 3 ? "定位逾時（20 秒內沒有訊號），還在繼續試…" : "定位失敗：手機目前取不到位置"); lastKind = "err"; };
  const startWatch = mode => { if (!navigator.geolocation) { if (mode === "btn") say("這個瀏覽器不支援定位"); return; } if (!isSecureContext) { if (mode === "btn") say("這個網址不是 https，瀏覽器不給定位"); return; } if (mode !== "btn" && perm === "denied") return;
    if (mode === "btn") { manual = true; perm = perm === "denied" ? "" : perm; try { if ("speechSynthesis" in window) speechSynthesis.speak(new SpeechSynthesisUtterance("")); } catch (e) {} }
    if (watchId != null) { if (mode !== "btn") return; stopWatch(); }
    watchMode = mode; watchT0 = nowT(); gotFix = false; lastKind = ""; saidFix = false; gpsBtn.classList.add("busy"); if (mode === "btn") say("定位中…");
    try { watchId = navigator.geolocation.watchPosition(onPos, onErr, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }); } catch (e) { gpsBtn.classList.remove("busy"); say("定位失敗：" + e.message); return; }
    clearTimeout(watchStop); watchStop = setTimeout(() => { stopWatch(); clog("gpsoff", { why: "idle" }); }, 180000); };
  gpsBtn.addEventListener("click", () => startWatch("btn")); setInterval(() => gpsBtn.classList.toggle("live", gpsLive()), 3000);
  const pickHole = () => { fill = null; mapBox.classList.remove("filling"); clearForce(); loadShots(); lieAuto = true; pend = null; const c = cur(); let adopt = false; if (fresh(30000) && c.H.ll) { const b0 = locate(fix.lat, fix.lon), a = b0 && b0.dd <= HOLE ? aim(b0, fix.lat, fix.lon, false) : null; if (a && a.tgt === a.self) { gpsPt = [Math.round(a.self.p[0]), Math.round(a.self.p[1])]; if (shots.length) pos = gpsPt.slice(); posSrc = "gps"; play = [ci, li, hi]; adopt = true; } } if (!adopt) gpsPt = null; lockHere(); draw(); say(fresh(30000) ? `鎖定第 ${c.H.h || hi + 1} 洞・按「定位」恢復自動換洞` : ""); gpsBtn.classList.toggle("live", gpsLive()); };
  const RK = "mi-caddie-round", rsel = () => { try { const o = JSON.parse(localStorage.getItem(RK) || "null"); return o && o.day === today && o.c === sec.data[ci].short ? o : null; } catch (e) { return null; } }, rset = seq => { try { localStorage.setItem(RK, JSON.stringify({ day: today, c: sec.data[ci].short, seq })); } catch (e) {} clog("roundsel", { seq }); };
  const isNine = C => C.layouts.length > 1 && C.layouts.every(Lx => Lx.holes.length <= 9);
  const AREA_ZH = { Arnold: "藍天", Palmer: "黃金", Mountain: "青山", Lake: "綠水", "Hole 19": "右" }, areaShort = (Lx, j) => { const s = String((Lx && Lx.label) || "").trim(); if (AREA_ZH[s]) return AREA_ZH[s]; if (!s) return `區 ${j + 1}`; if (/[\u3400-\u9fff]/.test(s) && s.length <= 3) return s; if (/^[A-Za-z0-9]{1,4}$/.test(s)) return s; return `區 ${j + 1}`; }, holeLabel = (C, Lx, j, Hx) => `第 ${Hx.h} 洞`, courseLabel = (C, j) => C.short + (C.layouts.length > 1 ? " " + areaShort(C.layouts[j], j) : "");
  const courseToday = C => Object.keys(tstore().holes || {}).some(k => k.startsWith(C.short + "|"));
  const CBK = "mi-caddie-combo", comboOf = C => { try { const o = JSON.parse(localStorage.getItem(CBK) || "null"); if (o && o.day === today && o.sh === C.short) return { f: o.f, b: o.b }; } catch (e) {} const o = tstore(), first = {}; Object.keys(o.holes || {}).forEach(k => { const [sh, j] = k.split("|"); if (sh !== C.short) return; const r = o.holes[k]; if (!hasRec(r)) return; const ts = [(r.s || []).map(x => x.t || 0), r.st || 0].flat().filter(Boolean), t = ts.length ? Math.min(...ts) : 9e15; first[j] = Math.min(first[j] ?? 9e15, t); }); const js = Object.keys(first).map(Number).sort((a, b) => first[a] - first[b]); return { f: js.length ? js[0] : null, b: js.length > 1 ? js[1] : null }; }, comboSet = (C, f, b) => { try { localStorage.setItem(CBK, JSON.stringify({ day: today, sh: C.short, f, b })); } catch (e) {} clog("combo", { c: C.short, f, b }); };
  const segUI = () => { const C = sec.data[ci]; segB.replaceChildren(); if (!isNine(C)) { segB.hidden = true; return; } segB.hidden = false; const cb = comboOf(C), js = [cb.f, cb.b].filter(j => j != null); if (!js.includes(li)) js.unshift(li); js.forEach(j => { const b = h("button", j === li ? "on" : "", segB, areaShort(C.layouts[j], j)); b.addEventListener("click", () => { if (j === li) return; goArea(j); lockHere(); say(`${areaShort(C.layouts[j], j)} 第 1 洞`); }); }); if (js.length < 2) h("button", "q", segB, "?").addEventListener("click", () => courseBtn.click()); };
  const goArea = j => { { const C = sec.data[ci]; if (isNine(C) && j !== li) { const cb = comboOf(C); if (cb.f == null) comboSet(C, li, j); else if (cb.b == null && j !== cb.f) comboSet(C, cb.f, j); } } li = j; hi = 0; play = null; fill = null; clearForce(); browse = false; loadShots(); lieAuto = true; view = null; draw(); };
  let askedArea = false;
  const hasRec = r => !!(r && ((r.s || []).length || r.putts != null)), areasLeft = () => { const C = sec.data[ci]; return C.layouts.map((Lx, j) => j).filter(j => j !== li); }, areaPlayed = j => { const C = sec.data[ci], o = tstore(); return Object.keys(o.holes || {}).some(k => k.startsWith(`${C.short}|${j}|`) && hasRec(o.holes[k])); }, areaNext = () => { const p = areasLeft().filter(areaPlayed); return p.length === 1 ? p[0] : null; };
  const askArea = () => { const C = sec.data[ci], left = areasLeft(); if (!left.length) return; { const dj = areaNext(); if (dj != null) { clog("area", { to: dj, auto: 1 }); goArea(dj); lockHere(); say(`${areaShort(C.layouts[dj], dj)} 第 1 洞`); return; } } sheet.replaceChildren(); sheet.hidden = false; const bg = h("div", "bg", sheet), box = h("div", "box", sheet), close = () => { sheet.hidden = true; sheet.replaceChildren(); }; bg.addEventListener("click", close); const hd = h("div", "hd", box); h("b", "", hd, "換到哪一區？"); const x = h("button", "x", hd, "✕"); x.addEventListener("click", close); const fx = fix && nowT() - fix.t < 600000 ? fix : null, dOf = Lx => { const H0 = Lx.holes[0]; if (!fx || !H0 || !H0.ll) return null; const q = toPx(H0, fx.lat, fx.lon); return ydH(H0, q, H0.tee); }; const opts = left.map(j => ({ Lx: C.layouts[j], j, d: dOf(C.layouts[j]), pl: areaPlayed(j) })); opts.sort((a, b) => (b.pl - a.pl) || (a.d != null && b.d != null ? a.d - b.d : 0) || String(a.Lx.label || "").localeCompare(String(b.Lx.label || ""), "zh-Hant")); const g = h("div", "areas", box); opts.forEach((o, k) => { const b = h("button", k === 0 && o.d != null && o.d < 400 ? "near" : "", g); h("b", "", b, areaShort(o.Lx, o.j)); h("span", "", b, o.pl ? "今天打過" : o.d != null && o.d < 2000 ? `離第 1 洞發球台 ${Math.round(o.d)} 碼` : `${o.Lx.holes.length} 洞`); b.addEventListener("click", () => { close(); clog("area", { to: o.j }); goArea(o.j); lockHere(); say(`${areaShort(o.Lx, o.j)} 第 1 洞`); }); }); };
  const maybeAskArea = () => {};
let holeBtn = null, prevB = null, nextB = null; const top = h("div", "cad-top"), modeBtn = h("button", "cad-mode", top), courseBtn = h("button", "cad-course", top), segB = h("div", "cad-seg", top), selC = h("select", "", top), selL = h("select", "", top), holes = h("div", "cad-holes", host), wrap = h("div", "cad-wrap", host), mapBox = h("div", "cad-map", wrap), side = h("div", "cad-side", wrap), big = h("div", "cad-big"), lieRow = h("div", "filters"), edBox = h("div", "cad-ed"), more = h("details", "cad-more"), res = (h("summary", "", more, "更多：打得好／打不好、左右偏差、障礙物、各桿比較"), h("div", "cad-res", more)); { side.append(foot); const bar = h("div", "cad-bar"); host.prepend(bar); holeBtn = h("button", "cad-hole", top); prevB = h("button", "cad-nb pv", null); nextB = h("button", "cad-nb nx", null); h("b", "", prevB, "◀"); h("span", "", prevB, "上一洞"); h("span", "", nextB, "下一洞"); h("b", "", nextB, "▶"); const hnav = h("div", "cad-nav", null); hnav.append(prevB, holeBtn, nextB); prevB.setAttribute("aria-label", "上一洞"); nextB.setAttribute("aria-label", "下一洞"); holes.style.display = "none"; selC.style.display = "none"; bar.append(top, hnav, holes, gpsRow); if (innerWidth >= 900) { side.prepend(top); top.after(hnav); side.append(gpsRow); bar.classList.add("pc"); }
    courseBtn.addEventListener("click", () => { sheet.replaceChildren(); sheet.hidden = false; const bg = h("div", "bg", sheet), box = h("div", "box", sheet), close = () => { sheet.hidden = true; sheet.replaceChildren(); }; bg.addEventListener("click", close); const to = tstore(), played = new Set(Object.keys(to.holes).filter(k => { const r = to.holes[k]; return r && ((r.s || []).length || r.putts != null); }).map(k => k.split("|")[0])), fx = fix && nowT() - fix.t < 600000 ? fix : null, kmOf = c => fx && c.ll ? Math.hypot((fx.lat - c.ll[0]) * 111.2, (fx.lon - c.ll[1]) * 111.2 * Math.cos(fx.lat * Math.PI / 180)) : null, ord = sec.data.map((c, i) => ({ c, i, p: played.has(c.short) ? 0 : 1, km: kmOf(c) })); ord.sort((a, b) => a.p - b.p || (a.km != null && b.km != null ? a.km - b.km : 0) || a.i - b.i);
      let pi = ci, draft = null; const srtOf = c => c.layouts.map((Lx, j) => [Lx, j]).sort((a, b) => { const A = areaShort(a[0], a[1]), B = areaShort(b[0], b[1]), lr = t => t === "左" ? 0 : t === "右" ? 1 : 2; return lr(A) - lr(B) || String(a[0].label || "").localeCompare(String(b[0].label || ""), "zh-Hant"); });
      const head = (t, back) => { box.replaceChildren(); const hd = h("div", "hd", box); if (back) { const bk = h("button", "bk", hd, "‹"); bk.addEventListener("click", back); } h("b", "", hd, t); const x = h("button", "x", hd, "✕"); x.addEventListener("click", close); };
      const pickList = () => { head("選球場", main); const list = h("div", "clist", box); ord.forEach(({ c, i, p, km }) => { const row = h("div", "cr tap" + (i === pi ? " on" : ""), list), nm = h("span", "n", row, c.short + (c.preview ? "（新）" : "")); h("small", "", nm, !p ? "今天" : km != null ? (km < 10 ? km.toFixed(1) : String(Math.round(km))) + " 公里" : ""); h("span", "hint", row, isNine(c) ? `${c.layouts.length} 區` : c.layouts.length > 1 ? (c.layouts.every((Lx, j) => /^[左右]$/.test(areaShort(Lx, j))) ? "左右" : `${c.layouts.length} 組 18 洞`) : `${c.layouts[0].holes.length} 洞`); row.addEventListener("click", () => { if (!isNine(c) && c.layouts.length === 1) { close(); ci = i; li = 0; hi = 0; play = null; pickHole(); return; } pi = i; draft = null; main(); }); }); };
      const main = () => { const c = sec.data[pi], i = pi; head("設定球場"); const pn = h("div", "combo", box), r0 = h("div", "cbr", pn); h("span", "cl", r0, "球場"); const cb0 = h("button", "cname", r0); h("b", "", cb0, c.short); h("i", "", cb0, "▾"); cb0.addEventListener("click", pickList); const srt = srtOf(c);
        if (isNine(c)) { if (!draft) draft = comboOf(c); const rowOf = (lab, sel, dis, on) => { const r = h("div", "cbr", pn); h("span", "cl", r, lab); const g = h("span", "cg", r); srt.forEach(([Lx, j]) => { const b = h("button", sel === j ? "on" : "", g, areaShort(Lx, j)); if (dis === j) b.disabled = true; b.addEventListener("click", () => on(j)); }); }; const apply = () => { const d = draft; comboSet(c, d.f, d.b); if (d.f == null) return; if (!(i === ci && (li === d.f || li === d.b))) { ci = i; li = d.f; hi = 0; } play = null; pickHole(); }; rowOf("前九", draft.f, null, j => { draft = { f: j, b: draft.b === j ? null : draft.b }; apply(); main(); }); rowOf("後九", draft.b, draft.f, j => { if (draft.f == null) return; draft = { f: draft.f, b: draft.b === j ? null : j }; apply(); main(); }); return; }
        if (c.layouts.length > 1) { const nm = srt.map(([Lx, j]) => areaShort(Lx, j)), lr = nm.every(t => t === "左" || t === "右"), r = h("div", "cbr", pn); h("span", "cl", r, lr ? "左右" : "哪一組"); const g = h("span", "cg", r); (lr ? srt.slice().sort((a, b) => (areaShort(a[0], a[1]) === "左" ? 0 : 1) - (areaShort(b[0], b[1]) === "左" ? 0 : 1)) : srt).forEach(([Lx, j]) => { const b = h("button", i === ci && j === li ? "on" : "", g, areaShort(Lx, j)); b.addEventListener("click", () => { close(); ci = i; li = j; hi = 0; play = null; pickHole(); }); }); return; }
        h("div", "note", pn, `${c.layouts[0].holes.length} 洞，不用設定前後九`); };
      main(); });
    const stepHole = dn => { const { C } = cur(), n = cur().L.holes.length, to = hi + dn; if (isNine(C)) { const cb = comboOf(C); if (to >= n && dn > 0) { if (li === cb.f && cb.b != null) { goArea(cb.b); lockHere(); say(`後九 ${areaShort(C.layouts[cb.b], cb.b)} 第 1 洞`); } return; } if (to < 0 && dn < 0) { if (li === cb.b && cb.f != null) { li = cb.f; hi = C.layouts[cb.f].holes.length - 1; pickHole(); } return; } } if (to < 0 || to >= n) return; hi = to; pickHole(); }; prevB.addEventListener("click", () => stepHole(-1)); nextB.addEventListener("click", () => stepHole(1));
    holeBtn.addEventListener("click", () => { sheet.replaceChildren(); sheet.hidden = false; const bg = h("div", "bg", sheet), box = h("div", "box", sheet), close = () => { sheet.hidden = true; sheet.replaceChildren(); }; bg.addEventListener("click", close); const { C, L } = cur(), o = tstore();
      const hd = h("div", "hd", box); h("b", "", hd, "選哪一洞？"); const x = h("button", "x", hd, "✕"); x.addEventListener("click", close);
      const g = h("div", "hgrid", box); C.layouts[li].holes.forEach((Hx, n) => { const b = h("button", n === hi ? "on" : "", g); h("b", "", b, Hx.h); b.addEventListener("click", () => { close(); hi = n; pickHole(); }); }); });
      modeBtn.addEventListener("click", () => { sheet.replaceChildren(); sheet.hidden = false; const bg = h("div", "bg", sheet), box = h("div", "box", sheet), close = () => { sheet.hidden = true; sheet.replaceChildren(); }; bg.addEventListener("click", close); const hd = h("div", "hd", box); h("b", "", hd, sim ? "現在是模擬模式" : "現在是正式模式"); const x = h("button", "x", hd, "✕"); x.addEventListener("click", close); h("div", "lab", box, sim ? "模擬的紀錄另外存，不算成績，也不會匯出。切到正式就回到真正上場的紀錄。" : "正式的紀錄就是今天的成績。切到模擬可以隨便練習操作，模擬的紀錄另外存、不會混進成績。"); const ok = h("button", "ok", box, sim ? "切到正式" : "切到模擬"); ok.addEventListener("click", () => { close(); setSim(!sim, true); say(sim ? "已切到模擬：紀錄另外存，不算成績" : "已切到正式"); }); const no = h("button", "ok ghost", box, "不要切換"); no.addEventListener("click", close); }); const nav = document.querySelector(".ptabs"); if (!NARROW() && nav) bar.style.top = nav.offsetHeight + "px"; } try { const sv = JSON.parse(localStorage.getItem("mi-caddie") || "null"); if (sv && sec.data[sv[0]] && sec.data[sv[0]].short === sv[2]) { ci = sv[0]; li = sec.data[ci].layouts[sv[1]] ? sv[1] : 0; if (sv[4] === today && sec.data[ci].layouts[li].holes[sv[3]]) hi = sv[3]; } } catch (e) {}
  sec.data.forEach((c, i) => { const o = h("option", "", selC, c.preview ? `${c.short}（新）` : c.short); o.value = i; });
  const interp = (tab, d) => { if (d <= tab[0][0]) return tab[0][1]; const n = tab.length; if (d >= tab[n - 1][0]) return tab[n - 1][1]; for (let i = 1; i < n; i++) if (d <= tab[i][0]) { const a = tab[i - 1], b = tab[i]; return a[1] + (b[1] - a[1]) * (d - a[0]) / (b[0] - a[0]); } return tab[n - 1][1]; };
  const tabOf = l => M.E[l === "F" || l === "T" ? "F" : "R"], after = (l, d) => l === "G" ? M.green : interp(tabOf(l), d) + (l === "B" ? Math.max(0.1, M.bunker) : 0), afterSe = (l, d) => l === "G" ? 0.05 : interp(tabOf(l).map(r => [r[0], r[3]]), d);
  const q = (a, f) => a[Math.min(a.length - 1, Math.floor(a.length * f))];
  const evalClub = (club, d, l) => { const C = M.clubs[club], S = C.s, same = S.filter(x => x[6] === l), pool = same.length >= 12 ? same : S;
    let use = null, mode = "near";
    if (d <= C.reach + 10) { use = pool.filter(x => x[0] >= d * 0.75 && x[0] <= d * 1.25); if (use.length < 10) use = S.filter(x => x[0] >= d * 0.75 && x[0] <= d * 1.25); if (use.length < 10) use = null; }
    if (!use) { mode = "full"; use = pool.filter(x => x[0] >= C.med * 1.2); if (use.length < 10) use = S.filter(x => x[0] >= C.med * 1.2); if (use.length < 8) return null; }
    const rows = use.map(x => { if (mode === "near") return { adv: x[1], end: x[2], g: x[3] === "G", v: x[4] ? x[5] : after(x[3], x[2]), e: x[4] ? 0 : afterSe(x[3], x[2]) };
      const raw = d - x[1], nd = Math.abs(raw), l2 = nd <= 12 ? "G" : x[3] === "G" ? (raw < 0 ? "R" : "F") : x[3]; return { adv: x[1], end: nd, g: l2 === "G", v: after(l2, nd), e: afterSe(l2, nd) }; });
    const n = rows.length, mean = rows.reduce((a, r) => a + r.v, 0) / n, sd = Math.sqrt(rows.reduce((a, r) => a + (r.v - mean) ** 2, 0) / Math.max(1, n - 1));
    const adv = rows.map(r => r.adv).sort((a, b) => a - b), end = rows.map(r => r.end).sort((a, b) => a - b);
    const tabSe = rows.reduce((a, r) => a + r.e, 0) / n; return { club, mode, n, exp: 1 + mean, se: Math.hypot(sd / Math.sqrt(n), tabSe), lo: q(adv, 0.1), med: q(adv, 0.5), hi: q(adv, 0.9), left: q(end, 0.5), green: rows.filter(r => r.g).length / n, lieAll: pool === S }; };
  const evaluate = (d, l) => M.bag.filter(c => (c !== CL.driver || (l === "T" && d >= 230)) && (c !== CL.sand || CL.sand === CL.chip || l === "B")).map(c => evalClub(c, d, l === "W" ? "R" : l)).filter(Boolean).sort((a, b) => a.exp - b.exp);
  const pathOf = (H, geo) => { const L = Math.hypot(H.pin[0] - H.tee[0], H.pin[1] - H.tee[1]) || 1, ux = (H.pin[0] - H.tee[0]) / L, uy = (H.pin[1] - H.tee[1]) / L, Ly = L * H.k, bins = {};
    (geo.fairway || []).forEach(poly => { const xs = poly.map(q2 => q2[0]), ys = poly.map(q2 => q2[1]), x1 = Math.min(...xs), x2 = Math.max(...xs), y1 = Math.min(...ys), y2 = Math.max(...ys);
      for (let x = x1; x <= x2; x += 5) for (let y = y1; y <= y2; y += 5) { const dx = x - H.tee[0], dy = y - H.tee[1], t = (dx * ux + dy * uy) * H.k, lat = (dx * uy - dy * ux) * H.k; if (t < 70 || t > Ly - 40 || Math.abs(lat) > 70 || !inside([x, y], poly)) continue; const b = Math.floor(t / 40); (bins[b] = bins[b] || []).push([lat, x, y]); } });
    let mid = [], prevLat = 0; const avg = (g, i) => g.reduce((a, q2) => a + q2[i], 0) / g.length;
    Object.keys(bins).map(Number).sort((a, b) => b - a).forEach(b => { const v = bins[b].slice().sort((a, c) => a[0] - c[0]); let gs = [[v[0]]]; for (let i = 1; i < v.length; i++) { if (v[i][0] - v[i - 1][0] > 12) gs.push([]); gs[gs.length - 1].push(v[i]); } gs = gs.filter(g => g.length >= 6); if (!gs.length) return; const g = gs.reduce((a, c) => Math.abs(avg(c, 0) - prevLat) < Math.abs(avg(a, 0) - prevLat) ? c : a); prevLat = avg(g, 0); mid.unshift([avg(g, 1), avg(g, 2)]); });
    { const len = pp => pp.reduce((a, q2, i) => i ? a + Math.hypot(q2[0] - pp[i - 1][0], q2[1] - pp[i - 1][1]) : 0, 0); if (len([H.tee, ...mid, H.pin]) > L * 1.12) mid = []; }
    const pts = H.path && H.path.length > 1 ? H.path : [H.tee, ...mid, H.pin], cum = [0]; for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) * H.k);
    const total = cum[cum.length - 1], at = sy => { const v = Math.max(0, Math.min(total, sy)); for (let i = 1; i < pts.length; i++) if (v <= cum[i]) { const f = (v - cum[i - 1]) / (cum[i] - cum[i - 1] || 1); return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f]; } return pts[pts.length - 1]; };
    const proj = pt => { let best = { dist: 1e9, s: 0 }; for (let i = 1; i < pts.length; i++) { const a = pts[i - 1], b = pts[i], vx = b[0] - a[0], vy = b[1] - a[1], t = Math.max(0, Math.min(1, ((pt[0] - a[0]) * vx + (pt[1] - a[1]) * vy) / (vx * vx + vy * vy || 1))), dd = Math.hypot(pt[0] - a[0] - t * vx, pt[1] - a[1] - t * vy) * H.k; if (dd < best.dist) best = { dist: dd, s: cum[i - 1] + t * (cum[i] - cum[i - 1]) }; } return best; };
    return { pts, cum, total, at, proj }; };
  const latOf = (club, adv) => M.lat[club] || M.latr.map(v => Math.round(v * adv));
  const pick = (d, l) => { const list = evaluate(d, l); if (!list.length) return null; let b = list[0]; const s2 = list[1]; if (s2 && s2.exp - b.exp < Math.hypot(b.se, s2.se) && s2.n >= 3 * b.n) b = s2; return { b, list }; };
  const route = (d, l, aggr) => { const steps = []; let cd = d, cl = l;
    for (let n = 0; n < 6 && cd > 0 && cl !== "G"; n++) { if (cd <= 15) { steps.push({ club: CL.chip, from: cd, adv: cd, left: 0, on: true, chance: false, r: (pick(cd, cl) || {}).b || { exp: 1 + M.green, se: 0, lo: 0, hi: cd, med: cd }, list: [] }); break; }
      const p = pick(cd, cl); if (!p) break; const r = p.b; let left, on; if (r.mode === "near") { on = r.green >= 0.5 || r.left <= 12; left = on ? 0 : r.left; } else { left = Math.abs(cd - r.med); on = left <= 12; if (on) left = 0; }
      let go = !on && cd <= r.hi + 5; if (go && (left <= 20 || r.club === CL.chip || r.club === CL.sand)) { on = true; left = 0; go = false; } if (go && aggr) { on = true; left = 0; } if (!on && left >= cd) break; steps.push({ club: r.club, from: cd, adv: cd - left, left, on, go, chance: go && !aggr, r, list: p.list }); cd = left; cl = on ? "G" : "F"; }
    if (steps.length && !steps[steps.length - 1].on && steps.length < 6) steps.push({ club: CL.chip, from: cd, adv: cd, left: 0, on: true, chance: false, r: steps[0].r, list: [] });
    return steps; };
  const TERR = {}, TCOST = [1.5, 0.35, 1.0, 0, 2.2, 0.5, 0], terrain = (key, geo) => { if (TERR[key]) return TERR[key]; const S = 0.2, O = 40, N = Math.ceil((730 + 2 * O) * S), cv = document.createElement("canvas"); cv.width = cv.height = N; const c = cv.getContext("2d"); c.fillStyle = "rgb(0,0,0)"; c.fillRect(0, 0, N, N); ["outline", "tree", "fairway", "water", "sand", "green"].forEach((kk, ix) => { c.fillStyle = `rgb(${(ix + 1) * 36},0,0)`; (geo[kk] || []).forEach(poly => { c.beginPath(); poly.forEach((q, n) => n ? c.lineTo((q[0] + O) * S, (q[1] + O) * S) : c.moveTo((q[0] + O) * S, (q[1] + O) * S)); c.closePath(); c.fill(); }); }); const d = c.getImageData(0, 0, N, N).data, g = new Uint8Array(N * N); for (let n = 0; n < N * N; n++) g[n] = Math.min(6, Math.round(d[n * 4] / 36)); return TERR[key] = pt => { const x = Math.round((pt[0] + O) * S), y = Math.round((pt[1] + O) * S); return x < 0 || y < 0 || x >= N || y >= N ? 0 : g[y * N + x]; }; };
  const inside = (pt, poly) => { let ins = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const [x1, y1] = poly[i], [x2, y2] = poly[j]; if ((y1 > pt[1]) !== (y2 > pt[1]) && pt[0] < (x2 - x1) * (pt[1] - y1) / (y2 - y1) + x1) ins = !ins; } return ins; };
  const cur = () => { const C = sec.data[ci], L = C.layouts[li] || C.layouts[0], H = L.holes[hi] || L.holes[0]; return { C, L, H, geo: (REPORT.geo[L.cid] || {})[H.h] || {} }; };
  const autoLie = (pt, H, geo) => { const any = k => (geo[k] || []).some(p => inside(pt, p)); { const dt = Math.hypot(pt[0] - H.tee[0], pt[1] - H.tee[1]) * H.k; if (dt < 14 || (dt < 35 && (!shots.length || (shots.length === 1 && ydH(H, pt, shots[0].p) < SAME)))) return "T"; } { const dp = Math.hypot(pt[0] - H.pin[0], pt[1] - H.pin[1]) * H.k; if ((any("green") && dp <= 45) || (dp <= 15 && !any("sand") && !any("water"))) return "G"; } return any("sand") ? "B" : any("water") ? "W" : any("fairway") ? "F" : "R"; };
  const hazards = (pt, H, geo, d, aim) => { const al = Math.hypot(aim[0] - pt[0], aim[1] - pt[1]) || 1, ux = (aim[0] - pt[0]) / al, uy = (aim[1] - pt[1]) / al, out = [];
    [["sand", "沙坑"], ["water", "水"]].forEach(([k, name]) => (geo[k] || []).forEach(poly => { const cx = poly.reduce((a, q2) => a + q2[0], 0) / poly.length, cy = poly.reduce((a, q2) => a + q2[1], 0) / poly.length, dx = cx - pt[0], dy = cy - pt[1], along = (dx * ux + dy * uy) * H.k, lat = (dx * uy - dy * ux) * H.k;
      if (along > 15 && along < d + 25 && Math.abs(lat) < 35) out.push({ name, along: Math.round(along), side: Math.abs(lat) < 8 ? "正前方" : lat > 0 ? "左" : "右" }); }));
    return out.sort((a, b) => a.along - b.along); };
  const peekL = h("div", "cad-peek l"), peekR = h("div", "cad-peek r"); let peekKey = "", inPrep = false, prepT = 0;
  const prep = () => { const key = [ci, li, hi].join("|"); if (inPrep || key === peekKey || !NARROW()) return; if (true) return; inPrep = true; const sv = { hi, pos, posSrc, lie, lieAuto, shots, meta }; try { [[-1, peekL], [1, peekR]].forEach(([dn, box]) => { const to = sv.hi + dn; box.replaceChildren(); if (to < 0 || to >= cur().L.holes.length) return; hi = to; loadShots(); lieAuto = true; drawCore(); const g = mapBox.querySelector("svg"); if (g) box.appendChild(g.cloneNode(true)); }); } finally { hi = sv.hi; pos = sv.pos; lie = sv.lie; lieAuto = sv.lieAuto; shots = sv.shots; meta = sv.meta; posSrc = sv.posSrc; drawCore(); mapBox.append(peekL, peekR); peekKey = key; inPrep = false; } };
  const fitSide = () => { if (!nextB) return; nextB.style.top = ""; nextB.style.height = ""; const br = mapBox.querySelector(":scope > .hud.br"); if (!br) return; br.style.maxHeight = ""; if (innerWidth >= 900) return; const top = 102; br.style.maxHeight = Math.max(110, mapBox.clientHeight - top - 8) + "px"; };
  let chAdj = 0, brW = 150; const draw = () => { drawCore(); if (NARROW()) { const b0 = mapBox.querySelector(":scope > .hud.br"), bw = b0 ? b0.offsetWidth : 0; if (Math.abs(bw - brW) > 16) { brW = bw; drawCore(); } } if (innerWidth >= 900) { side.style.minHeight = mapBox.offsetHeight + "px"; let ps = side.querySelector(".cad-plan"); if (!ps) { ps = document.createElement("div"); ps.className = "cad-plan"; ps.dataset.lb = "這一洞怎麼打"; foot.dataset.lb = "今天的成績"; const tp = side.querySelector(".cad-top"); tp ? tp.after(ps) : side.prepend(ps); } const hb = mapBox.querySelector(".hud.br"); ps.replaceChildren(); if (hb) ps.append(hb); } mapBox.append(gpsBtn, gpsMsg); phBtn.textContent = photoOn ? "線圖" : "照片"; mapBox.append(phBtn); if (NARROW()) mapBox.append(gpsRow); fitSide(); resetUI(); if (photoOn && photoSrc) { const at = h("div", "cad-attr", mapBox, photoSrc); at.hidden = !mapBox.querySelector(".cad-photo image.ok"); } if (NARROW()) { const nv2 = document.querySelector(".tabgrip") || document.querySelector(".ptabs"), over = nv2 ? Math.round(foot.getBoundingClientRect().bottom + scrollY + 8 - nv2.getBoundingClientRect().top) : 0, na = Math.max(-300, Math.min(400, chAdj + over)); if (Math.abs(na - chAdj) > 1) { chAdj = na; drawCore(); mapBox.append(gpsBtn, gpsMsg); phBtn.textContent = photoOn ? "線圖" : "照片"; mapBox.append(phBtn); if (NARROW()) mapBox.append(gpsRow); fitSide(); resetUI(); if (photoOn && photoSrc) { const at = h("div", "cad-attr", mapBox, photoSrc); at.hidden = !mapBox.querySelector(".cad-photo image.ok"); } } } { const brE = mapBox.querySelector(":scope > .hud.br"); gpsMsg.style.right = ""; } fillUI(); clearTimeout(prepT); prepT = setTimeout(prep, 250); };
  const MK = "mi-caddie-mode", setSim = (v, man) => { if (fill) endFill(); sim = !!v; simMan = simMan || !!man; simDec = true; try { localStorage.setItem(MK, JSON.stringify({ day: today, sim, man: simMan })); } catch (e) {} host.classList.toggle("cad-sim", sim); clog("mode", { sim, man: !!man }); loadShots(); posSrc = "def"; view = null; lieAuto = true; draw(); };
  const MINI_STYLE = { outline: { fill: "#356b2c" }, tree: { fill: "#1d4422" }, fairway: { fill: "#8cd65e" }, water: { fill: "#1e88ff" }, sand: { fill: "#f6e7b9" }, green: { fill: "#c4f59a" } };
  const hud = o => { const { H, d, P, steps, dec, decide, full, V } = o, n = shots.length, tee0 = !n && pos[0] === H.tee[0] && pos[1] === H.tee[1], fin = meta.putts != null;
    { const hs = holeBtn && holeBtn.querySelector(".yd"); if (hs) hs.textContent = `${Math.round(P.total)} 碼`; } if (!tee0 && !fin) { const tl = h("div", "hud tl" + (stale ? " stale" : ""), mapBox); h("b", "", tl, `${d} 碼`); h("span", "", tl, lie === "G" ? "在果嶺上" : `離旗桿・${LIEZH[lie] || ""}`); }
    const br = h("div", "hud br" + (fin ? " fin" : ""), mapBox), pr = v => v === 0 ? "Par" : v > 0 ? "+" + v : "−" + (-v);
    if (!fin) { const dp = tee0 ? dec : decide(Math.round(P.total), "T", 0), plan = dp.steps || []; if (plan.length) { const hd = h("div", "hd", br); h("span", "", hd, `建議 ${plan.length} ON 2 推` + (dp.mode === "go" ? "（拼）" : "")); h("b", "", hd, `${plan.length + 2} 桿（${pr(plan.length + 2 - H.par)}）`); if (dp.mode) h("div", "alt" + (dp.mode === "safe" ? " go" : ""), br, dp.mode === "safe" ? `拼：${dp.n} ON・有${dp.why}` : `保守：${dp.n} ON`); } }
    const atLast = !!(shots[n - 1] && pos && ydH(H, pos, shots[n - 1].p) < SAME);
    if (n && !fin) { const ds = h("button", "r dsum", br); h("span", "", ds, `已打 ${n} 桿`); h("b", "", ds, shots.map(z => ABR[z.c] || z.c || "?").join(" ")); h("em", "", ds, "✎"); ds.addEventListener("click", ev => { ev.stopPropagation(); openHoleDay(li, hi); }); }
    if (fin) shots.forEach((st, i) => { const r = h("button", "r done", br), tg = shotTag(H, shots, i, true, true); h("i", "", r, NUM[i] || i + 1); h("b", "", r, ABR[st.c] || st.c || "?"); if (tg.haz) h("em", "hz", r, tg.haz); else { h("span", tg.word ? "w" : "", r, tg.t); if (tg.est) h("small", "", r, "估"); } r.addEventListener("click", ev => { ev.stopPropagation(); openClubs(i); }); });
    if (fin) { const on = meta.on != null ? meta.on : onGuess({ s: shots, pen: meta.pen }), ft = h("div", "hd fin", br); h("span", "", ft, `實際 ${on} ON ${meta.putts} 推`); h("b", "", ft, `${on + meta.putts} 桿（${pr(on + meta.putts - H.par)}）`); } else { if (n) h("div", "lb", br, "接下來"); if (lie === "G") h("div", "r plan", br, "果嶺・兩推"); else steps.slice(atLast ? 1 : 0).forEach((st, k2) => { const no = n + 1 + k2, r = h("div", "r plan", br); h("i", "", r, NUM[no - 1] || no); h("b", "", r, ABR[st.club] || st.club); h("span", "", r, String(st.on && !st.go ? Math.round(st.from) : st.adv)); if (st.go) h("em", "", r, "拼"); }); }
    if (V !== full && V[3] < full[3] * 0.95) { const bl = h("div", "hud bl", mapBox), ms = el("svg", { viewBox: full.join(" ") }, bl); drawCourse(ms, o.geo, MINI_STYLE); el("rect", { x: V[0], y: V[1], width: V[2], height: V[3], fill: "rgba(255,210,63,.18)", stroke: "#ffd23f", "stroke-width": full[3] / 70 }, ms); el("circle", { cx: H.pin[0], cy: H.pin[1], r: full[3] / 60, fill: "#ff2d2d" }, ms); bl.addEventListener("click", ev => { ev.stopPropagation(); view = full; viewKey = [ci, li, hi].join("|"); draw(); }); } };
  const zoomUI = (() => { const P2 = new Map(); let g0 = null, tmp = null, lastTap = 0; const sv = () => mapBox.querySelector(":scope > svg"), vb = v => { const e = sv(); if (e) e.setAttribute("viewBox", v.map(z => z.toFixed(1)).join(" ")); };
    const clamp = v => { const f = curFull; if (!f) return v; const a = v[2] / v[3], mh = 70 / (cur().H.k || 1); let hg = Math.max(mh, Math.min(f[3] * 1.05, v[3])), wd = hg * a; let x = v[0] + (v[2] - wd) / 2, y = v[1] + (v[3] - hg) / 2; x = Math.max(f[0] - wd * 0.3, Math.min(f[0] + f[2] - wd * 0.7, x)); y = Math.max(f[1] - hg * 0.3, Math.min(f[1] + f[3] - hg * 0.7, y)); return [x, y, wd, hg]; };
    const scale = (e, v) => { const r = e.getBoundingClientRect(); return { r, s: Math.max(v[2] / r.width, v[3] / r.height) }; };
    const skip = t => t.closest && t.closest(".hud, .cad-here, button");
    mapBox.addEventListener("pointerdown", e => { if (fill && e.target.closest && e.target.closest(".fillpt")) { P2.set(e.pointerId, { x: e.clientX, y: e.clientY }); g0 = { t: "drag", mv: true }; try { mapBox.setPointerCapture(e.pointerId); } catch (er) {} e.preventDefault(); return; } if (skip(e.target) || !curV) return; const s2 = sv(); if (!s2) return; P2.set(e.pointerId, { x: e.clientX, y: e.clientY }); if (P2.size > 1) for (const id of P2.keys()) { try { mapBox.setPointerCapture(id); } catch (er) {} } const ps = [...P2.values()]; if (ps.length === 1) g0 = { t: "pan", x: e.clientX, y: e.clientY, v: curV.slice(), mv: false }; else if (ps.length === 2) { const [a, b] = ps, { r, s } = scale(s2, curV); g0 = { t: "pinch", d: Math.hypot(a.x - b.x, a.y - b.y) || 1, mx: (a.x + b.x) / 2 - r.left, my: (a.y + b.y) / 2 - r.top, v: curV.slice(), s, mv: true }; } });
    mapBox.addEventListener("pointermove", e => { if (!g0 || !P2.has(e.pointerId)) return; if (g0.t === "drag") { const s2 = sv(), s0 = fill && shots[fill.i]; if (!s2 || !s0) return; const pt = s2.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY; const q = pt.matrixTransform(s2.getScreenCTM().inverse()); s0.p = [Math.round(q.x), Math.round(q.y)]; fill.moved = true; s2.querySelectorAll(".fillpt").forEach(c => { c.setAttribute("cx", q.x); c.setAttribute("cy", q.y); }); return; } P2.set(e.pointerId, { x: e.clientX, y: e.clientY }); const s2 = sv(); if (!s2) return; const v = g0.v;
      if (g0.t === "pan" && P2.size === 1) { const dx = e.clientX - g0.x, dy = e.clientY - g0.y; if (!g0.mv && Math.hypot(dx, dy) < 7) return; if (!g0.mv) { try { mapBox.setPointerCapture(e.pointerId); } catch (er) {} } g0.mv = true; const { s } = scale(s2, v); tmp = clamp([v[0] - dx * s, v[1] - dy * s, v[2], v[3]]); vb(tmp); }
      else if (g0.t === "pinch" && P2.size >= 2) { const [a, b] = [...P2.values()], dd = Math.hypot(a.x - b.x, a.y - b.y) || 1, f = g0.d / dd, nw = v[2] * f, nh = v[3] * f, ux = v[0] + g0.mx * g0.s, uy = v[1] + g0.my * g0.s, ns = g0.s * f; tmp = clamp([ux - g0.mx * ns, uy - g0.my * ns, nw, nh]); vb(tmp); } });
    const end = e => { if (!P2.has(e.pointerId)) return; P2.delete(e.pointerId); if (P2.size) return; const g = g0; g0 = null; if (!g) return; if (g.t === "drag") { tsave(); swipeT = nowT(); draw(); return; } if (g.mv && tmp) { view = tmp; viewKey = [ci, li, hi].join("|"); tmp = null; swipeT = nowT(); draw(); return; } const t = nowT(); if (t - lastTap < 320) { lastTap = 0; view = curFull; viewKey = [ci, li, hi].join("|"); swipeT = t; draw(); } else lastTap = t; };
    mapBox.addEventListener("pointerup", end); mapBox.addEventListener("pointercancel", end);
    mapBox.addEventListener("wheel", e => { if (!curV) return; e.preventDefault(); const s2 = sv(); if (!s2) return; const { r, s } = scale(s2, curV), f = e.deltaY > 0 ? 1.15 : 1 / 1.15, mx = e.clientX - r.left, my = e.clientY - r.top, ux = curV[0] + mx * s, uy = curV[1] + my * s, ns = s * f; view = clamp([ux - mx * ns, uy - my * ns, curV[2] * f, curV[3] * f]); viewKey = [ci, li, hi].join("|"); draw(); }, { passive: false }); return 1; })();
  const drawCore = () => { const { C, L, H, geo } = cur(); selC.value = ci; courseBtn.replaceChildren(); h("b", "", courseBtn, isNine(C) ? C.short : courseLabel(C, li)); if (modeBtn) { modeBtn.textContent = sim ? "模擬" : "正式"; modeBtn.classList.toggle("sim", sim); } h("i", "", courseBtn, "▾"); try { localStorage.setItem("mi-caddie", JSON.stringify([ci, li, C.short, hi, today])); } catch (e) {} selL.replaceChildren(); C.layouts.forEach((l, i) => { const o = h("option", "", selL, l.label || `區 ${i + 1}`); o.value = i; }); selL.value = li; selL.style.display = "none"; if (holeBtn) { holeBtn.replaceChildren(); const hn = h("span", "hn", holeBtn); h("i", "", hn, "第"); h("b", "", hn, H.h); h("i", "", hn, "洞"); const hm = h("span", "hm", holeBtn); h("b", "par", hm, `Par ${H.par}`); h("b", "yd", hm, ""); { const nine = isNine(C), cb = nine ? comboOf(C) : {}, last = hi >= L.holes.length - 1, back = nine && hi <= 0 && li === cb.b && cb.f != null; prevB.disabled = hi <= 0 && !back; nextB.disabled = last && !(nine && li === cb.f && cb.b != null); const sp = nextB.querySelector("span"), sp0 = prevB.querySelector("span"); if (sp0) sp0.textContent = "上一洞"; if (sp) sp.textContent = "下一洞"; segUI(); } }
    holes.replaceChildren(); L.holes.forEach((x, i) => { const b = h("button", "fchip" + (i === hi ? " on" : ""), holes, x.h); b.addEventListener("click", () => { hi = i; pickHole(); }); });
    if (!pos) { pos = H.tee.slice(); lie = "T"; }
    const noOff = shots.length - (shots.length && ydH(H, pos, shots[shots.length - 1].p) < SAME ? 1 : 0);
    const P = pathOf(H, geo), pj = P.proj(pos), straight = Math.hypot(H.pin[0] - pos[0], H.pin[1] - pos[1]) * H.k, onPath = pj.dist < 30 && P.total - pj.s > 20, d = Math.round(onPath ? pj.dist + P.total - pj.s : straight); if (lieAuto) lie = autoLie(pos, H, geo);
    const pts = []; (geo.outline || []).forEach(p => pts.push(...p)); pts.push(H.tee, H.pin); const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]), pad = 14, x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad, w = Math.max(...xs) - x0 + pad, hh = Math.max(...ys) - y0 + pad;
    const danger = sFrom => { let water = false, block = false, near = 0; ["water", "sand"].forEach(kk => (geo[kk] || []).forEach(poly => { let hit = false, blk = false, nr = false; poly.forEach(q => { const j2 = P.proj(q); if (j2.s >= sFrom && j2.s <= P.total + 15) { if (j2.dist <= 25) hit = true; if (j2.dist <= 12) blk = true; } if (Math.hypot(q[0] - H.pin[0], q[1] - H.pin[1]) * H.k <= 25) nr = true; }); if (kk === "water" && hit) water = true; if (kk === "sand") { if (blk) block = true; if (nr) near++; } })); return water ? "水" : block || near >= 2 ? "沙坑" : ""; };
    const decide = (d0, l0, s0) => { const safe = route(d0, l0), gi = safe.findIndex(st => st.go); if (gi < 0) return { steps: safe }; const sGo = s0 + safe.slice(0, gi).reduce((a, st) => a + st.adv, 0), dz = danger(sGo + Math.max(0, safe[gi].r.lo)); return dz ? { steps: safe, mode: "safe", why: dz, n: gi + 1 } : { steps: route(d0, l0, true), mode: "go", n: safe.length }; };
    const finH = meta.putts != null, dec = lie === "G" ? { steps: [] } : decide(d, lie, P.total - d), hold = !!fill || finH, steps = hold ? [] : dec.steps, first = steps[0], best = first && first.r; sugClub = first ? first.club : null; if (!shots.length) teeSug = { k: keyAt(ci, li, hi), c: sugClub }; nextUI(); runUI();
    const sux = (H.pin[0] - pos[0]) / (straight / H.k || 1), suy = (H.pin[1] - pos[1]) / (straight / H.k || 1), at = yd => onPath ? P.at(pj.s + Math.max(0, yd - pj.dist)) : [pos[0] + sux * yd / H.k, pos[1] + suy * yd / H.k];
    const barEl = document.querySelector(".cad-bar"), navEl = document.querySelector(".tabgrip") || document.querySelector(".ptabs"), cw = (mapBox.clientWidth || 360) - 12, ch = NARROW() ? Math.max(280, Math.min(760, (navEl ? navEl.getBoundingClientRect().top : innerHeight - 60) - (mapBox.getBoundingClientRect().top + scrollY) - (foot.offsetHeight || 64) - 16 - chAdj)) : Math.max(460, Math.min(1400, innerHeight - (mapBox.getBoundingClientRect().top + scrollY) - 20)), u0 = hh / ch;
    const E = []; { let cum = 0; steps.forEach((st, n) => { if (st.on && !st.go) return; const lt = latOf(st.club, st.adv), lo = Math.min(st.r.lo, st.from), hi = Math.min(st.r.hi, st.from), c0 = at(cum + (lo + hi) / 2), c1 = at(cum + (lo + hi) / 2 + 4), tl = Math.hypot(c1[0] - c0[0], c1[1] - c0[1]) || 1, tx = (c1[0] - c0[0]) / tl, ty = (c1[1] - c0[1]) / tl, rx = -ty, ry = tx, off = (lt[0] + lt[2]) / 2 / H.k, a = Math.max(6, (hi - lo) / 2) / H.k, b = Math.max(6, (lt[2] - lt[0]) / 2) / H.k;
      const mid = at(cum + st.adv / 2); cum += st.adv; const m0 = at(cum); E.push({ n, st, lo, hi, mid, cx: c0[0] + rx * off, cy: c0[1] + ry * off, a, b, ang: Math.atan2(ty, tx) * 180 / Math.PI, xh: Math.hypot(a * tx, b * ty), m: [m0[0] + rx * lt[1] / H.k, m0[1] + ry * lt[1] / H.k] }); }); }
    { const tf = terrain(L.cid + "_" + H.h, geo); E.forEach(e => { if (e.st.club === CL.chip || e.st.club === CL.sand) return; const cv = M.cov[e.st.club] || M.covr, A = e.ang * Math.PI / 180, tx = Math.cos(A), ty = Math.sin(A), rx = -ty, ry = tx, smp = [];
      [[0, cv[0], 0.3], [cv[0], cv[1], 0.6]].forEach(([r0, r1, ms]) => { const R = [0.25, 0.75].map(f => Math.sqrt(r0 * r0 + (r1 * r1 - r0 * r0) * f)); R.forEach(rr => { for (let t = 0; t < 16; t++) { const th = (t + (rr === R[0] ? 0 : 0.5)) / 16 * 2 * Math.PI; smp.push([Math.cos(th) * rr * e.a, Math.sin(th) * rr * e.b, ms / 32]); } }); });
      const score = sh => { let c = 0, w = 0; const ox = rx * sh / H.k, oy = ry * sh / H.k; smp.forEach(([u, v, ms]) => { const t2 = tf([e.cx + ox + tx * u + rx * v, e.cy + oy + ty * u + ry * v]); c += ms * TCOST[t2]; if (t2 === 4) w += ms; }); return { c: c + 0.006 * Math.abs(sh), w }; };
      let best = { sh: 0, ...score(0) }; for (let sh = -30; sh <= 30; sh += 3) { if (!sh) continue; const r = score(sh); if (r.c < best.c - 0.01) best = { sh, ...r }; }
      if (best.sh) { const ox = rx * best.sh / H.k, oy = ry * best.sh / H.k; e.cx += ox; e.cy += oy; e.m = [e.m[0] + ox, e.m[1] + oy]; } e.shift = best.sh; e.water = best.w; }); }
    const tee0 = pos[0] === H.tee[0] && pos[1] === H.tee[1], tot = steps.length + 2, dv0 = tot - H.par, nm0 = dv0 <= -1 ? "Birdie" : dv0 === 0 ? "Par" : dv0 === 1 ? "Bogey" : dv0 === 2 ? "Double" : "+" + dv0, labs = [];
    const ab = cl => (REPORT.abbr || {})[cl] || cl.replace("鐵", "i");
    E.forEach(e => labs.push({ y: e.mid[1], ax: e.mid[0], ab: ab(e.st.club), no: e.n + 1, big: e.st.adv, go: e.st.go, hgt: 30, wpx: e.st.go ? 128 : 104 }));
    { let cum = 0; steps.forEach((st, sn) => { if (st.on && !st.go) { const q = at(cum + st.from / 2); labs.push({ y: q[1], ax: q[0], ab: ab(st.club), no: sn + 1, big: Math.round(st.from), hgt: 30, wpx: 90 }); } else cum += st.adv; }); }
    labs.push({ y: pos[1], ax: pos[0] - 6 * u0, big: `${d} 碼`, small: tee0 ? `Par ${H.par}` : lie === "G" ? "在果嶺上" : steps.length ? `再 ${steps.length} 桿上果嶺` : LIEZH[lie], hgt: 44, wpx: 112, ball: 1 });
    const RR = NARROW() ? Math.max(0, Math.min(cw * 0.42, brW + 10)) : 0, cwL = cw - RR, s0 = Math.max(hh / ch, w / cwL), vw = cw * s0, vh = ch * s0, vx0 = x0 - (cwL * s0 - w) / 2, vy0 = y0 - (vh - hh) / 2, vkey = [ci, li, hi].join("|"), asp = cw / ch, full = [vx0, vy0, vw, vh];
    let V = full; if (view && viewKey === vkey) V = view; else if (shots.length && meta.putts == null && ydH(H, pos, H.tee) > 60) { const qs = [pos, H.pin]; E.forEach(e => { const cv = M.cov[e.st.club] || M.covr, r = Math.max(e.a, e.b) * cv[1]; qs.push([e.cx - r, e.cy - r], [e.cx + r, e.cy + r]); }); const mg = 28 / H.k, qx = qs.map(q => q[0]), qy = qs.map(q => q[1]), bx0 = Math.min(...qx) - mg, bx1 = Math.max(...qx) + mg, by0 = Math.min(...qy) - mg * 1.4, by1 = Math.max(...qy) + mg * 1.6; let hg = Math.min(vh, Math.max(by1 - by0, (bx1 - bx0) / asp * cw / cwL, 150 / H.k)), wd = hg * asp; const cx = (bx0 + bx1) / 2, cy = Math.max(y0 + hg / 2, Math.min(y0 + hh - hg / 2, (by0 + by1) / 2)); if (hg < vh * 0.92) V = [cx - wd * (cwL / 2) / cw, cy - hg / 2, wd, hg]; }
    curFull = full; curV = V; curCh = ch; const [VX, VY, VW, VH] = V, u = Math.max(VH / ch, VW / cw), k = 1.35 * u;
    { const dp = tee0 ? dec : decide(Math.round(P.total), "T", 0), plan = dp.steps, pt = plan.length + 2, par = v => v === 0 ? "Par" : v > 0 ? "+" + v : "−" + (-v), line = n => `${n} ON　${n + 2} 桿（${par(n + 2 - H.par)}）`, alt = dp.mode === "safe" ? `拼：${line(dp.n)}・有${dp.why}` : dp.mode === "go" ? `保守：${line(dp.n)}` : ""; labs.push({ y: y0 + (alt ? 36 : 26) * u, tot: 1, big: plan.length ? `${pt} 桿（${par(pt - H.par)}）` : "資料不足", small: plan.length ? `建議打法：${plan.length} ON 2 推` + (dp.mode === "go" ? "（拼）" : "") : "", alt, altc: dp.mode === "go" ? "#d0d0d0" : "#ff8a5c", hgt: alt ? 68 : 46 }); }
    mapBox.replaceChildren(); const svg = el("svg", { viewBox: `${VX} ${VY} ${VW} ${VH}`, style: `height:${ch}px`, preserveAspectRatio: "xMidYMid meet" }, mapBox); el("rect", { x: vx0 - vw, y: vy0 - vh, width: vw * 3, height: vh * 3, fill: "#0c0c0b" }, svg); const vec = el("g", { class: "cad-vec" }, svg); drawCourse2(vec, geo); photoSrc = ""; if (photoOn && H.ll) { if (drawSaved(svg, H)) vec.style.display = "none"; else drawPhoto(svg, H, geo, VW / Math.max(1, mapBox.clientWidth || 360)); }
    { const cc = cur(), g = el("g", {}, svg), tx = vx0 + vw - 12 * u, st = { "text-anchor": "end", stroke: "#000", "stroke-opacity": 0.7, "paint-order": "stroke" }; el("text", { x: tx, y: y0 + 24 * u, "font-size": 16 * u, "font-weight": 700, fill: "#fff", "stroke-width": 3 * u, ...st }, g).textContent = cc.C.short; if (cc.C.layouts.length > 1 && cc.L.label) el("text", { x: tx, y: y0 + 41 * u, "font-size": 12 * u, fill: "#d0d0d0", "stroke-width": 2.4 * u, ...st }, g).textContent = cc.L.label; g.remove(); }
    if (d > 0 && !hold) { const line = onPath ? [pos, ...Array.from({ length: 41 }, (_, i) => P.at(pj.s + (P.total - pj.s) * i / 40))] : [pos, H.pin];
      el("polyline", { points: line.map(q2 => q2.map(v => v.toFixed(1)).join(",")).join(" "), fill: "none", stroke: "#fff", "stroke-width": 1.2 * k, "stroke-dasharray": `${4 * k} ${4 * k}`, "stroke-opacity": 0.8 }, svg);
      let e1 = 0; E.forEach(e => { if (e.st.club === CL.chip || e.st.club === CL.sand || /^P/.test(e.st.club)) return; const n = e1++, cv = M.cov[e.st.club] || M.covr, rot = `rotate(${e.ang} ${e.cx} ${e.cy})`; el("ellipse", { cx: e.cx, cy: e.cy, rx: e.a * cv[1], ry: e.b * cv[1], transform: rot, fill: "#ffd23f", "fill-opacity": 0.13, stroke: "#ffd23f", "stroke-width": 1.2 * k, "stroke-opacity": 0.75 }, svg); el("ellipse", { cx: e.cx, cy: e.cy, rx: e.a * cv[0], ry: e.b * cv[0], transform: rot, fill: "#ffd23f", "fill-opacity": 0.38, stroke: "#ffd23f", "stroke-width": 2 * k }, svg);
        if (e.water >= 0.05) { const lx = e.cx - e.xh * cv[1] - 2 * k, fl = lx - 40 * k < VX + 4 * u; el("text", { x: fl ? e.cx + e.xh * cv[1] + 2 * k : lx, y: e.cy + 12 * k, "text-anchor": fl ? "start" : "end", "font-size": 8 * k, "font-weight": 700, fill: "#9bd6ff", stroke: "#000", "stroke-opacity": 0.6, "stroke-width": 1.6 * k, "paint-order": "stroke" }, svg).textContent = `下水 ${Math.round(e.water * 100)}%`; }
        if (!n) { const ex = e.xh, pc = v => Math.round(M.covp[v] * 100) + "%"; [[cv[1], 1], [cv[0], 0]].forEach(([f, v]) => el("text", { x: e.cx + ex * f + 2 * k, y: e.cy + 2.2 * k, "font-size": 6.5 * k, fill: "#d0d0d0", "fill-opacity": 0.85, stroke: "#000", "stroke-opacity": 0.5, "stroke-width": 1.4 * k, "paint-order": "stroke" }, svg).textContent = pc(v)); } }); }
    { const halo = { stroke: "#000", "stroke-width": 3.4 * u, "paint-order": "stroke", "stroke-linejoin": "round", fill: "#fff" };
      const hz2 = []; if (!hold) [["sand", "#f6e3ae"], ["water", "#9bd6ff"]].forEach(([kk, col]) => (geo[kk] || []).forEach(poly => { let a0 = 1e9, a1 = -1e9, near = 1e9; poly.forEach(q2 => { let al, la; if (onPath) { const pr = P.proj(q2); al = pr.s - pj.s; la = pr.dist; } else { const dx = q2[0] - pos[0], dy = q2[1] - pos[1]; al = (dx * sux + dy * suy) * H.k; la = Math.abs(dx * suy - dy * sux) * H.k; } near = Math.min(near, la); if (la < 45) { a0 = Math.min(a0, al); a1 = Math.max(a1, al); } });
        if (near > 28 || a1 < 25 || a0 > d + 12) return; const xs2 = poly.map(q2 => q2[0]), ys2 = poly.map(q2 => q2[1]), cx = (Math.min(...xs2) + Math.max(...xs2)) / 2, cy = (Math.min(...ys2) + Math.max(...ys2)) / 2, ref = onPath ? P.at(pj.s + (a0 + a1) / 2) : [pos[0] + sux * (a0 + a1) / 2 / H.k, pos[1] + suy * (a0 + a1) / 2 / H.k];
        hz2.push({ near, col, f: Math.max(0, Math.round(a0)), b: Math.round(a1), right: cx >= ref[0], x1: Math.min(...xs2), x2: Math.max(...xs2), y1: Math.min(...ys2), cy }); }));
      const boxes = [[VX, VY, VX + 150 * u, VY + 64 * u], [VX + VW - 175 * u, VY + VH - 150 * u, VX + VW, VY + VH], [VX + VW - 100 * u, VY, VX + VW, VY + 56 * u]]; E.forEach(e => boxes.push([e.m[0] - 9 * k, e.m[1] - 9 * k, e.m[0] + 9 * k, e.m[1] + 9 * k]));
      hz2.sort((a, b) => a.near - b.near).slice(0, 5).forEach(z => { const wide = z.b - z.f >= 8, tw = (String(z.f).length * 10.5 + (wide ? (String(z.b).length + 1) * 7.5 : 0)) * u, th = 17 * u, mx = (z.x1 + z.x2) / 2;
        const cands = [[z.x2 + 4 * u, z.cy + 6 * u, "start"], [z.x1 - 4 * u, z.cy + 6 * u, "end"], [mx, z.y1 - 5 * u, "middle"], [mx, 2 * z.cy - z.y1 + 17 * u, "middle"], [z.x2 + 4 * u, z.y1 - 3 * u, "start"], [z.x1 - 4 * u, z.y1 - 3 * u, "end"]];
        const bb = ([x, y, a]) => { const xa = a === "start" ? x : a === "end" ? x - tw : x - tw / 2; return [xa, y - th, xa + tw, y + 3 * u]; }, ok = q2 => { const r = bb(q2); return r[0] >= VX + 2 * u && r[2] <= VX + VW - 2 * u && r[1] >= VY + 2 * u && r[3] <= VY + VH - 2 * u && !boxes.some(o => r[0] < o[2] && r[2] > o[0] && r[1] < o[3] && r[3] > o[1]); };
        const pick2 = cands.find(ok); if (!pick2) return; boxes.push(bb(pick2)); const [x, y, anc] = pick2;
        const t = el("text", Object.assign({ x, y, "text-anchor": anc, "font-weight": 800 }, halo, { fill: z.col }), svg); el("tspan", { "font-size": 17 * u }, t, z.f); if (z.b - z.f >= 8) el("tspan", { "font-size": 12 * u }, t, "–" + z.b); }); }
    { const [px, py] = H.pin; el("circle", { cx: px, cy: py, r: 2.6 * k, fill: "#fff", stroke: "#000", "stroke-width": 0.8 * k }, svg); el("line", { x1: px, y1: py, x2: px, y2: py - 22 * k, stroke: "#fff", "stroke-width": 1.8 * k, "stroke-linecap": "round" }, svg); el("polygon", { points: `${px},${py - 22 * k} ${px + 13 * k},${py - 17.5 * k} ${px},${py - 13 * k}`, fill: "#ff2d2d", stroke: "#7a0000", "stroke-width": 0.6 * k }, svg); }
    if (shots.length) { const n = shots.length, prov = !finH && ydH(H, pos, shots[n - 1].p) >= SAME, tp = shots.map(s => s.p).concat(prov ? [pos] : finH ? [H.pin] : []), gs = { "font-size": 16 * u, "font-weight": 800, fill: "#b6ff9c", stroke: "#000", "stroke-width": 3.2 * u, "paint-order": "stroke", "stroke-linejoin": "round" };
      if (tp.length > 1) el("polyline", { points: tp.map(q2 => q2.join(",")).join(" "), fill: "none", stroke: "#b6ff9c", "stroke-width": 2.2 * k, "stroke-linejoin": "round", "stroke-opacity": 0.95 }, svg);
      shots.forEach((s, i) => { if (fill && fill.i === i) return; const R0 = 7.2 * k, es = !!s.est; el("circle", { cx: s.p[0], cy: s.p[1], r: R0, fill: es ? "rgba(0,0,0,.6)" : "#b6ff9c", stroke: es ? "#ffd23f" : "#111", "stroke-width": (es ? 1.3 : 0.9) * k, "stroke-dasharray": es ? `${2.2 * k} ${1.6 * k}` : "none" }, svg); el("text", { x: s.p[0], y: s.p[1] + 3.3 * k, "text-anchor": "middle", "font-size": 9.5 * k, "font-weight": 900, fill: es ? "#ffd23f" : "#111" }, svg).textContent = String(i + 1); }); if (fill && shots[fill.i]) { const q = shots[fill.i].p; el("circle", { cx: q[0], cy: q[1], r: 24 * k, fill: "transparent", class: "fillpt" }, svg); el("circle", { cx: q[0], cy: q[1], r: 9 * k, fill: "rgba(255,210,63,.55)", stroke: "#ffd23f", "stroke-width": 2.4 * k, class: "fillpt blink" }, svg); } }
    if (!finH && !fill && !(shots.length && ydH(H, pos, shots[shots.length - 1].p) < SAME)) { el("circle", { cx: pos[0], cy: pos[1], r: 6 * k, fill: "#fff", stroke: "#111", "stroke-width": 1.6 * k }, svg); el("circle", { cx: pos[0], cy: pos[1], r: 2.4 * k, fill: "#e03131" }, svg); }
    if (gpsPt && posSrc === "gps" && fresh(30000) && ydH(H, gpsPt, pos) > 6) { el("circle", { cx: gpsPt[0], cy: gpsPt[1], r: 7 * k, fill: "#2f8cff", "fill-opacity": 0.25 }, svg); el("circle", { cx: gpsPt[0], cy: gpsPt[1], r: 3.6 * k, fill: "#2f8cff", stroke: "#fff", "stroke-width": 1.4 * k }, svg); }
    hud({ C, L, H, geo, d, P, steps, dec, decide, full, V, svg });
    svg.addEventListener("click", e => { if (nowT() - swipeT < 500) return; const p = svg.createSVGPoint(); p.x = e.clientX; p.y = e.clientY; const t = p.matrixTransform(svg.getScreenCTM().inverse()); if (fill) { const s0 = shots[fill.i]; if (s0) { s0.p = [Math.round(t.x), Math.round(t.y)]; fill.moved = true; tsave(); draw(); } return; } if (!localStorage.getItem("__cad_tap")) { if (nowT() - lpT > 700) askInsert(t); return; } if (gpsLive()) { say("現在用 GPS 定位，點圖不會移動球"); return; } pos = [Math.round(t.x), Math.round(t.y)]; posSrc = "tap"; lieAuto = true; pend = null; draw(); clog("tap", snap()); });
    lieRow.replaceChildren(); [["T", "發球台"], ["F", "球道"], ["R", "長草"], ["B", "沙坑"], ["G", "果嶺"]].forEach(([c, t]) => { const b = h("button", "fchip" + ((lie === "W" ? "R" : lie) === c ? " on" : ""), lieRow, t); b.addEventListener("click", () => { lie = c; lieAuto = false; draw(); }); });
    { const b = h("button", "fchip", lieRow, "回發球台"); b.addEventListener("click", () => { if (gpsLive()) { say("現在用 GPS 定位，球的位置跟著你走"); return; } pos = null; posSrc = "tap"; lieAuto = true; draw(); clog("totee"); }); }
    edBox.replaceChildren(); { const n2 = shots.length, edTop = edBox; if (n2) { const sd = h("details", "cad-day cad-shots", edTop); sd.open = edOpen; sd.addEventListener("toggle", () => { edOpen = sd.open; }); h("summary", "", sd, `第 ${H.h || hi + 1} 洞已記 ${n2} 桿（修改）`); const edBox = sd;
      if (n2 && ydH(H, shots[0].p, H.tee) > 60) { const row = h("div", "er", edBox); h("span", "yd", row, "開球沒有記到？"); const b = h("button", "fchip", row, "補一桿"); b.addEventListener("click", () => { shots.unshift({ p: H.tee.map(Math.round), c: null, t: 0 }); tsave(); clog("fill", snap()); draw(); }); }
      shots.forEach((s, i) => { const row = h("div", "er", edBox), nx = shots[i + 1] ? shots[i + 1].p : ydH(H, pos, s.p) >= SAME ? pos : null; h("b", "", row, NUM[i] || i + 1); h("span", "yd", row, nx ? Math.round(ydH(H, s.p, nx)) + " 碼" + (shots[i + 1] ? "" : "?") : "—"); clubChips(h("span", "cks", row), i); const x = h("button", "del", row, "✕"); x.addEventListener("click", () => { shots.splice(i, 1); pend = null; tsave(); clog("del", { i: i + 1 }); draw(); }); });
      { const row = h("div", "ef", edBox), bu = h("button", "fchip", row, "↩ 取消最後一桿"), bx = h("button", "fchip", row, "清除這一洞"); bu.addEventListener("click", undo); let armed = 0; bx.addEventListener("click", () => { if (nowT() - armed < 4000) { shots = []; meta = { pen: 0, putts: null, on: null, steps: null }; pend = null; tsave(); clog("reset"); draw(); } else { armed = nowT(); bx.textContent = "再按一次確認"; setTimeout(() => { if (bx.isConnected) bx.textContent = "清除這一洞"; }, 4000); } }); } } [].forEach(w => { const row = h("div", "ef", edBox); h("span", "", row, `${w[3]} 還沒記成績`); const b = h("button", "fchip", row, "補記"); b.addEventListener("click", () => openSheet(w[0], w[1], w[2])); });
      { const o = tstore(), C = sec.data[ci], rows = []; let sum = 0, par = 0, ord = 0; C.layouts.forEach((L, j) => L.holes.forEach(Hx => { const r = o.holes[`${C.short}|${j}|${Hx.h}`], t = r && scoreTxt(r, Hx.par); ord++; if (t) { const on = r.on != null ? r.on : onGuess(r), ls = (r.s || [])[(r.s || []).length - 1]; sum += on + r.putts; par += Hx.par; rows.push({ k: r.st || (ls && ls.t) || 0, o: ord, dv: on + r.putts - Hx.par, c: [(C.layouts.length > 1 && L.label ? L.label + " " : "") + Hx.h, on, r.putts, r.steps == null ? "–" : r.steps, on + r.putts, dvTxt(on + r.putts - Hx.par)] }); } })); if (rows.length) { rows.sort((a, b) => (a.k && b.k ? a.k - b.k : 0) || a.o - b.o); let run = 0; rows.forEach(rw => { run += rw.dv; rw.c.push(run > 0 ? "+" + run : run === 0 ? "±0" : "−" + (-run)); }); const det = h("details", "cad-day", edBox); det.open = dayOpen; det.addEventListener("toggle", () => { dayOpen = det.open; }); h("summary", "", det, `今天已記 ${rows.length} 洞　${sum} 桿（${dvTxt(sum - par)}）`); const t = h("table", "cad-t", det), hr = h("tr", "", t); ["洞", "ON", "推", "步", "桿", "±", "累計"].forEach(x => h("th", "", hr, x)); rows.forEach(rw => { const tr = h("tr", "", t); rw.c.forEach((x, xi) => h("td", xi === 6 ? "cum" : "", tr, x)); }); } }
      }
    res.replaceChildren(); h("div", "ln", res, `離旗桿 ${d} 碼・${LIEZH[lie]}` + (lie === "W" ? "（照長草算，另加罰桿）" : ""));
    lastSay = "";
    if (lie === "G") { lastSay = "在果嶺上，兩推。"; h("div", "cad-say", res, "在果嶺上，兩推。"); h("div", "ln", res).innerHTML = `果嶺上平均 <b>${M.green.toFixed(2)}</b> 推`; return; }
    if (!steps.length) { h("div", "cad-say", res, "資料不足"); return; }
    const used = (pos[0] === H.tee[0] && pos[1] === H.tee[1]) ? 0 : null, total = steps.length + 2, dv = used === 0 ? total - H.par : null, nm = dv == null ? "" : dv <= -1 ? "Birdie" : dv === 0 ? "Par" : dv === 1 ? "Bogey" : dv === 2 ? "Double" : "+" + dv;
    const parts = []; let prevChance = false; steps.forEach((st, n) => { const act = st.on ? (st.from <= 80 ? "切上果嶺" : "上果嶺") : ` ${st.adv} 碼`; parts.push((prevChance ? "不順利再用 " : n ? "接 " : "") + st.club + (st.on && prevChance ? " 切上去" : act) + (st.chance ? "，順利的話直接上果嶺" : "")); prevChance = st.chance; });
    lastSay = `${d} 碼。${parts.join("，")}，兩推` + (used === 0 ? `，${total} 桿 ${nm}。` : `，再 ${total} 桿進洞。`); h("div", "cad-say", res, lastSay);
    const grid = h("div", "cad-steps", res); steps.forEach((st, n) => { h("i", "", grid, n + 1); h("b", "", grid, st.club); h("span", "", grid, st.on ? "上果嶺" : `${st.adv} 碼`); const lt = st.on ? null : latOf(st.club, st.adv); h("span", "rem", grid, st.on ? "" : `剩 ${st.left}` + (lt && Math.abs(lt[1]) >= 5 ? `・常偏${lt[1] > 0 ? "右" : "左"} ${Math.abs(lt[1])} 碼` : ""));
      if (!st.on) { const br = h("div", "br", grid), alt = (label, adv, l2) => { const rem = Math.max(0, Math.round(st.from - adv)), ln = h("div", "", br); if (rem <= 12) { ln.innerHTML = `<b>${label}</b> ${adv} 碼：上果嶺`; return; } const nx = route(rem, l2); ln.innerHTML = `<b>${label}</b> ${adv} 碼：剩 ${rem}，接 ${nx.length ? nx[0].club : "A杆"}` + (nx.length ? `（再 ${nx.length + 2} 桿）` : ""); };
        alt("打得好", Math.min(st.r.hi, st.from), "F"); alt("打不好", Math.min(st.r.lo, st.from), "R"); } });
    h("i", "pt", grid, "推"); h("b", "", grid, "推桿"); h("span", "", grid, "兩推"); h("span", "rem", grid, "");
    h("div", "ln", res).innerHTML = (used === 0 ? `照常態打 <b>${total}</b> 桿（<b>${nm}</b>）` : `照常態打再 <b>${total}</b> 桿`) + `・你從這裡平均 <b>${best.exp.toFixed(1)}</b> 桿`;
    const hz = hazards(pos, H, geo, d, first.on ? H.pin : at(best.med)); if (hz.length) h("div", "ln", res).innerHTML = "前方：" + hz.slice(0, 3).map(z => `${z.name} <b>${z.along}</b> 碼${z.side}` + (!first.on && z.along >= best.lo - 5 && z.along <= best.hi + 5 ? "（這一桿會到）" : "")).join("、");
    const list = first.list; if (list.length > 1) { const det = h("details", "cad-det", res); h("summary", "", det, "這一桿各球桿的比較"); const t = h("table", "cad-t", det), hr = h("tr", "", t); ["球桿", "常態前進", "打完剩", "預期桿數", "球數"].forEach(x => h("th", "", hr, x));
      list.slice(0, 4).forEach(r => { const tr = h("tr", r === best ? "on" : "", t); h("td", "", tr, r.club); h("td", "", tr, `${r.med}（${r.lo}–${r.hi}）`); h("td", "", tr, r.left); h("td", "", tr, `${r.exp.toFixed(1)} ±${r.se.toFixed(1)}`); h("td", "", tr, r.n); }); } };
  let swipeT = 0, sw = null, sliding = false; const setX = (x, ms) => { mapBox.style.transition = ms ? `transform ${ms}ms cubic-bezier(.2,.8,.2,1), opacity ${ms}ms` : "none"; mapBox.style.transform = x ? `translateX(${x}px)` : ""; };
  const goHole = dn => { const n = cur().L.holes.length, to = hi + dn, w = mapBox.offsetWidth + 8; if (to < 0 || to >= n) { setX(0, 260); return; } sliding = true; setX(dn > 0 ? -w : w, 200);
    setTimeout(() => { hi = to; pickHole(); setX(0, 0); sliding = false; const on = holes.querySelector(".on"); if (on) holes.scrollTo({ left: on.offsetLeft - holes.offsetWidth / 2 + on.offsetWidth / 2, behavior: "smooth" }); }, 200); };
  { let lp = null; const cancel = () => { if (lp) { clearTimeout(lp.t); lp = null; } }; mapBox.addEventListener("pointerdown", e => { cancel(); if (!sim || fill || !e.isPrimary || e.target.closest("button,.hud")) return; const x = e.clientX, y = e.clientY; lp = { x, y, t: setTimeout(() => { lp = null; const sv0 = mapBox.querySelector("svg"); if (!sv0) return; const q = sv0.createSVGPoint(); q.x = x; q.y = y; const t = q.matrixTransform(sv0.getScreenCTM().inverse()); pos = [Math.round(t.x), Math.round(t.y)]; posSrc = "tap"; lieAuto = true; pend = null; lpT = nowT(); try { navigator.vibrate && navigator.vibrate(25); } catch (er) {} draw(); clog("tap", snap()); }, 480) }; }); mapBox.addEventListener("pointermove", e => { if (lp && Math.hypot(e.clientX - lp.x, e.clientY - lp.y) > 10) cancel(); }); ["pointerup", "pointercancel", "pointerleave"].forEach(ev => mapBox.addEventListener(ev, cancel)); mapBox.addEventListener("touchstart", e => { if (e.touches.length > 1) cancel(); }, { passive: true }); mapBox.addEventListener("contextmenu", e => { if (sim) e.preventDefault(); }); }
  const SWIPE = false; mapBox.addEventListener("touchstart", e => { sw = SWIPE && e.touches.length === 1 && !sliding && (!window.visualViewport || visualViewport.scale < 1.05) ? { x: e.touches[0].clientX, y: e.touches[0].clientY, t: nowT(), lock: 0, dx: 0 } : null; }, { passive: true });
  mapBox.addEventListener("touchmove", e => { if (!sw) return; if (e.touches.length > 1) { sw = null; setX(0, 0); return; } const dx = e.touches[0].clientX - sw.x, dy = e.touches[0].clientY - sw.y; if (!sw.lock) { if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return; sw.lock = Math.abs(dx) > 1.2 * Math.abs(dy) ? 1 : 2; if (sw.lock === 1) prep(); } if (sw.lock !== 1) return; const n = cur().L.holes.length, edge = (dx > 0 && hi === 0) || (dx < 0 && hi === n - 1); sw.dx = dx; setX(edge ? dx * 0.25 : dx, 0); }, { passive: true });
  const swEnd = () => { if (!sw) return; const s0 = sw; sw = null; if (s0.lock !== 1) return; swipeT = nowT(); const fast = nowT() - s0.t < 300 && Math.abs(s0.dx) > 25; if (Math.abs(s0.dx) > mapBox.offsetWidth * 0.22 || fast) goHole(s0.dx < 0 ? 1 : -1); else setX(0, 220); };
  mapBox.addEventListener("touchend", swEnd, { passive: true }); mapBox.addEventListener("touchcancel", () => { sw = null; setX(0, 200); }, { passive: true });
  selC.addEventListener("change", () => { ci = +selC.value; li = 0; hi = 0; play = null; pickHole(); });
  selL.addEventListener("change", () => { li = +selL.value; hi = 0; play = null; pickHole(); });
  const logBox = parent => { const row = h("div", "cad-log", parent), lab = h("span", "", row), bS = h("button", "", row, "匯出檔案"), bC = h("button", "", row, "複製"), bX = h("button", "x", null, "清除"), msg = h("div", "lm", row); let armed = 0;
    const pack = () => JSON.stringify({ app: "mi-caddie-log", ver: 1, exported: new Date().toISOString(), built: (document.querySelector(".pmeta") || {}).textContent || "", ua: navigator.userAgent, screen: [innerWidth, innerHeight, devicePixelRatio], day: tstoreReal(), days: daysRead(), cids: (() => { const o = {}; try { const ks = new Set(Object.keys(tstoreReal().holes || {})); Object.values(daysRead()).forEach(d => Object.keys(d.holes || {}).forEach(k => ks.add(k))); ks.forEach(k => { const [sh, j, hh] = k.split("|"), C = sec.data.find(x => x.short === sh), L = C && C.layouts[+j]; if (L) o[k] = L.cid + "_" + hh; }); } catch (e) {} return o; })(), log: logRead() });
    logUI = () => { const a = logRead(); lab.textContent = "測試紀錄 " + a.length + " 筆" + (a.length ? "（" + a[0].t.slice(5, 10) + " 起）" : ""); };
    const fname = () => "caddie_log_" + new Date().toISOString().slice(0, 10) + ".json", say = t => { msg.textContent = t; };
    const down = txt => { const u = URL.createObjectURL(new Blob([txt], { type: "application/json" })), a = document.createElement("a"); a.href = u; a.download = fname(); document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(u), 4000); say("已下載 " + fname()); };
    bS.addEventListener("click", () => { const txt = pack(); if (!logRead().length) { say("還沒有紀錄"); return; } try { const f = new File([txt], fname(), { type: "application/json" }); if (navigator.canShare && navigator.canShare({ files: [f] })) { navigator.share({ files: [f], title: fname() }).then(() => say("已交給分享選單")).catch(e => { if (e && e.name !== "AbortError") down(txt); }); return; } } catch (e) {} down(txt); });
    bC.addEventListener("click", () => { const txt = pack(); if (!logRead().length) { say("還沒有紀錄"); return; } const fb = () => { const ta = document.createElement("textarea"); ta.value = txt; document.body.appendChild(ta); ta.select(); let ok = false; try { ok = document.execCommand("copy"); } catch (e) {} ta.remove(); say(ok ? "已複製，貼到和 Claude 的對話裡" : "複製失敗，請改用「匯出檔案」"); }; if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(() => say("已複製（" + Math.round(txt.length / 1024) + " KB），貼到和 Claude 的對話裡"), fb); else fb(); });
    bX.addEventListener("click", () => { if (nowT() - armed < 4000) { try { localStorage.removeItem(LK); } catch (e) {} armed = 0; bX.textContent = "清除"; logUI(); say("已清除"); } else { armed = nowT(); bX.textContent = "再按一次確認"; setTimeout(() => { if (armed) { armed = 0; bX.textContent = "清除"; } }, 4000); } });
    logUI(); };
  addEventListener("error", e => { clog("jserr", { m: String(e.message).slice(0, 200), at: (e.filename || "").split("/").pop() + ":" + e.lineno }); });
  try { const f0 = JSON.parse(localStorage.getItem(FK) || "null"); if (f0 && f0.day === today && f0.view && f0.view[0] === ci && f0.view[1] === li && f0.view[2] === hi) { forced = f0; browse = true; } } catch (e) {}
  if (NARROW()) document.querySelectorAll(".pmeta, body > header, header").forEach(e => { if (!e.closest(".cad-bar")) e.style.display = "none"; }); if (NARROW()) { const wr = document.querySelector(".wrap"), nv = document.querySelector(".ptabs"); if (wr && nv) wr.style.paddingBottom = "0px"; } { const mb = document.querySelector(".cad-menu"), tp = document.querySelector(".cad-top"); if (mb && tp && mb.parentNode !== tp) tp.prepend(mb); } { let st = null; try { st = JSON.parse(localStorage.getItem(MK) || "null"); } catch (e) {} if (st && st.day === today && st.man) { sim = !!st.sim; simMan = true; } host.classList.toggle("cad-sim", sim); } loadShots(); draw();
  const auto = why => { if (document.visibilityState !== "visible") return; browse = !!forced; if (geoOK()) { stale = true; moving = false; stillT = 0; lastFixPt = null; draw(); setTimeout(() => { if (stale) { stale = false; draw(); } }, 20000); } startWatch(why || "auto"); };
  document.addEventListener("visibilitychange", () => { const v = document.visibilityState; clog("vis", { v }); if (v === "visible") auto("auto"); else stopWatch(); });
  addEventListener("pageshow", e => { if (e.persisted) auto("auto"); });
  { let went = false; const go = () => { if (went) return; went = true; auto("auto"); }; try { navigator.permissions.query({ name: "geolocation" }).then(st => { perm = st.state; st.onchange = () => { perm = st.state; }; go(); }, go); } catch (e) { go(); } setTimeout(go, 800); }
};

DRAW.roundlist = (sec, host) => {
  const cats = [["full", "完整", true], ["short", "小九洞", false], ["partial", "未完成", false]];
  const on = Object.fromEntries(cats.map(c => [c[0], c[2]])); let all = false;

  // 計分卡：一眼看出每一洞的結果。框的畫法照一般計分卡：圓圈 Birdie、單框 Bogey、雙框 Double、+3 以上塗滿；下面的點是推桿數
  const mark = dv => dv <= -2 ? "eagle" : dv === -1 ? "birdie" : dv === 0 ? "" : dv === 1 ? "bogey" : dv === 2 ? "dbl" : "blow";
  const card = r => { const box = document.createElement("div"); box.className = "sc";
    for (let i = 0; i < r.holes.length; i += 9) { const part = r.holes.slice(i, i + 9), g = h("div", "scg", box), pad = cls => { for (let k = part.length; k < 9; k++) h("div", cls, g, ""); };
      const sum = f => part.reduce((a, x) => a + f(x), 0);
      part.forEach(x => h("div", "hn", g, x[0])); pad("hn"); h("div", "hn", g, r.parts && r.parts[i] ? r.parts[i] : r.holes.length > 9 ? (i === 0 ? "前九" : i === 9 ? "後九" : "") : "合計");
      part.forEach(x => h("div", "pr", g, x[1])); pad("pr"); h("div", "pr", g, sum(x => x[1]));
      part.forEach(x => { const c = h("div", "s", g); h("span", "m " + mark(x[6] - x[1]), c, x[6]); h("div", "dots" + (x[4] >= 3 ? " three" : ""), c, x[4] == null ? "" : "•".repeat(x[4])); }); pad("s");
      const t = h("div", "s", g); h("span", "m tot", t, sum(x => x[6])); h("div", "dots", t, part.every(x => x[4] != null) ? sum(x => x[4]) + " 推" : ""); }
    const lg = h("div", "sclg", box);
    [["birdie", "Birdie"], ["bogey", "Bogey"], ["dbl", "Double"], ["blow", "+3 以上"]].forEach(([c, t]) => { const sp = h("span", "", lg); h("i", "m " + c, sp, ""); sp.append(t); });
    h("span", "", lg, "• 推桿數"); { const sp = h("span", "", lg); h("b", "three", sp, "•••"); sp.append("三推以上"); }
    return box; };
  // ----- 一號木開球：落點離球道中線幾碼 -----
  const drivePanel = (r, host) => { const all = r.gps.drive, d = all.filter(p => p.off != null), n = d.length; if (!all.length) { h("p", "note", host, "這一場沒有一號木開球的座標。"); return; }
    if (n < all.length) {      // 這個球場還沒有球道輪廓：只能畫開球距離，顏色是 Garmin 判定在不在球道
      const W0 = Math.max(host.clientWidth, 320), H0 = 96, m0 = { l: 16, r: 16, t: 26, b: 30 }, lo = Math.floor((Math.min(...all.map(p => p.yd)) - 5) / 20) * 20, hi = Math.ceil((Math.max(...all.map(p => p.yd)) + 5) / 20) * 20;
      const X0 = v => m0.l + (W0 - m0.l - m0.r) * (v - lo) / (hi - lo), s0 = el("svg", { viewBox: `0 0 ${W0} ${H0}`, role: "img", "aria-label": "一號木開球距離" }, host);
      el("line", { x1: m0.l, x2: W0 - m0.r, y1: H0 - m0.b, y2: H0 - m0.b, stroke: "var(--axis)" }, s0);
      for (let v = lo; v <= hi; v += 20) el("text", { x: X0(v), y: H0 - m0.b + 18, "text-anchor": "middle", class: "tick" }, s0, v);
      all.forEach((p, i) => { el("circle", { cx: X0(p.yd), cy: H0 - m0.b - 12, r: 7, fill: p.fw ? "var(--series)" : "var(--down)", stroke: "var(--surface)", "stroke-width": 2, "fill-opacity": 0.9 }, s0);
        el("text", { x: X0(p.yd), y: H0 - m0.b - 26 - (i % 2) * 11, "text-anchor": "middle", class: "xn" }, s0, p.hole); });
      const lg0 = h("div", "legend", host), dot0 = (c, t) => `<span><svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="${c}"/></svg>${t}</span>`, f0 = all.filter(p => p.fw).length;
      lg0.innerHTML = dot0("var(--series)", `在球道 ${f0}`) + dot0("var(--down)", `不在球道 ${all.length - f0}`) + `<span>橫軸＝開球距離（碼）</span><span>數字＝洞號</span>`; if (!n) return; }
    const W = Math.max(host.clientWidth, 320), nar = W < 560, rh = 30, m = { l: nar ? 64 : 74, r: nar ? 50 : 64, t: 8, b: 30 }, H = m.t + rh * n + m.b;
    const lim = Math.max(40, Math.ceil(Math.max(...d.map(p => Math.max(Math.abs(p.off), p.width / 2))) / 10) * 10 + 5), x = v => m.l + (W - m.l - m.r) * (v + lim) / (2 * lim);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "一號木開球落點" }, host);
    for (let v = -Math.floor(lim / 10) * 10; v <= lim; v += 10) { el("line", { x1: x(v), x2: x(v), y1: m.t, y2: H - m.b, stroke: v ? "var(--grid)" : "var(--axis)" }, svg);
      if (!nar || v % 20 === 0) el("text", { x: x(v), y: H - m.b + 18, "text-anchor": "middle", class: "tick" }, svg, v === 0 ? "中線" : (v < 0 ? "左 " : "右 ") + Math.abs(v)); }
    d.forEach((p, i) => { const y = m.t + rh * (i + 0.5);
      el("rect", { x: x(-p.width / 2), y: y - 9, width: x(p.width / 2) - x(-p.width / 2), height: 18, rx: 3, fill: "#4f9a48", "fill-opacity": 0.55 }, svg);
      el("circle", { cx: x(p.off), cy: y, r: 7, fill: p.fw ? "var(--series)" : "var(--down)", stroke: "var(--surface)", "stroke-width": 2 }, svg);
      el("text", { x: m.l - 8, y: y + 4, "text-anchor": "end", class: "xl" }, svg, `第 ${p.hole} 洞`); el("text", { x: W - m.r + 8, y: y + 4, class: "tick" }, svg, `${p.yd} 碼`); });
    el("line", { x1: x(0), x2: x(0), y1: m.t, y2: H - m.b, stroke: "var(--ink)", "stroke-dasharray": "4 4", "stroke-opacity": 0.6 }, svg);
    const inF = d.filter(p => p.fw).length, R = d.filter(p => !p.fw && p.off > 0).length, L = d.filter(p => !p.fw && p.off <= 0).length, avg = d.reduce((a, p) => a + p.off, 0) / n;
    const lg = h("div", "legend", host), dot = (c, t) => `<span><svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="${c}"/></svg>${t}</span>`;
    lg.innerHTML = `<span><svg width="20" height="14"><rect x="1" y="3" width="18" height="8" rx="2" fill="#4f9a48" fill-opacity=".55"/></svg>落點處的球道寬度</span>` + dot("var(--series)", `在球道 ${inF}`) + dot("var(--down)", `不在球道：偏右 ${R}、偏左 ${L}`) +
      `<span>${n} 球平均：中線${avg >= 0 ? "右" : "左"} ${Math.abs(avg).toFixed(0)} 碼</span>`;
    // 散布圖：橫軸是偏離中線幾碼，縱軸是開球距離；每個點是一洞，旁邊標洞號
    const H2 = nar ? 340 : 400, m2 = { l: 52, r: 16, t: 22, b: 34 }, aw = d.reduce((a, p) => a + p.width, 0) / n;
    const ylo = Math.floor((Math.min(...d.map(p => p.yd)) - 8) / 20) * 20, yhi = Math.ceil((Math.max(...d.map(p => p.yd)) + 8) / 20) * 20;
    const X = v => m2.l + (W - m2.l - m2.r) * (v + lim) / (2 * lim), Y = v => m2.t + (H2 - m2.t - m2.b) * (1 - (v - ylo) / (yhi - ylo));
    const s2 = el("svg", { viewBox: `0 0 ${W} ${H2}`, role: "img", "aria-label": "一號木開球散布圖", style: "margin-top:18px" }, host);
    el("rect", { x: X(-aw / 2), y: m2.t, width: X(aw / 2) - X(-aw / 2), height: H2 - m2.t - m2.b, fill: "#4f9a48", "fill-opacity": 0.22 }, s2);
    for (let v = ylo; v <= yhi; v += 20) { el("line", { x1: m2.l, x2: W - m2.r, y1: Y(v), y2: Y(v), stroke: v === ylo ? "var(--axis)" : "var(--grid)" }, s2); el("text", { x: m2.l - 8, y: Y(v) + 4, "text-anchor": "end", class: "tick" }, s2, v); }
    for (let v = -Math.floor(lim / 10) * 10; v <= lim; v += 10) if (!nar || v % 20 === 0) el("text", { x: X(v), y: H2 - m2.b + 18, "text-anchor": "middle", class: "tick" }, s2, v === 0 ? "中線" : (v < 0 ? "左 " : "右 ") + Math.abs(v));
    el("line", { x1: X(0), x2: X(0), y1: m2.t, y2: H2 - m2.b, stroke: "var(--ink)", "stroke-dasharray": "4 4", "stroke-opacity": 0.6 }, s2);
    el("text", { x: m2.l - 8, y: m2.t - 8, "text-anchor": "end", class: "tick" }, s2, "碼");
    d.forEach(p => { el("circle", { cx: X(p.off), cy: Y(p.yd), r: 7, fill: p.fw ? "var(--series)" : "var(--down)", stroke: "var(--surface)", "stroke-width": 2 }, s2);
      el("text", { x: X(p.off) + 11, y: Y(p.yd) + 4, class: "xn" }, s2, p.hole); });
    const lg2 = h("div", "legend", host);
    lg2.innerHTML = `<span><svg width="20" height="14"><rect x="1" y="1" width="18" height="12" fill="#4f9a48" fill-opacity=".3"/></svg>球道平均寬度 ${aw.toFixed(0)} 碼</span><span>數字＝洞號</span><span>縱軸＝開球距離</span>`; };
  // ----- 球道圖：一次看幾洞，用 Garmin 地圖描出來的輪廓加上每一桿的路線 -----
  const MAPC = { outline: "#2f5d2a", tree: "#1c3a1e", fairway: "#58963f", water: "#3c6edc", sand: "#e1cdaa", green: "#8ccd6e" }, SHOTC = { d: "#ff5a5a", w: "#ffa51f", i: "#5aa9ff", c: "#ffffff" };
  const holeMap = (r, H, hpx, brief) => { const geo = (REPORT.geo[H.cid] || {})[H.h] || {}, fig = document.createElement("figure"), st = (r.holes[H.n - 1] || [])[6];
    const par = H.par || (r.holes[H.n - 1] || [])[1], zone = r.parts && r.parts[H.n - 1] ? `　${r.parts[H.n - 1]} ${H.h}` : ""; h("figcaption", "", fig, brief ? `${H.n}　P${par}` + (st ? `　${st}` : "") : (zone && NARROW() ? `${H.n}${zone}　P${par}` : `第 ${H.n} 洞${zone}　Par ${par}`) + (r.len && r.len[H.n] ? `　${r.len[H.n]} 碼` : "") + (st ? `　${st} 桿` : ""));
    const pts = [].concat(...(geo.outline || []), ...H.shots.map(s => [[s[2], s[3]], [s[4], s[5]]]), H.pin ? [H.pin] : []), xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    if (!pts.length) return fig;
    const pad = geo.outline ? 10 : 40, x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad, w = Math.max(...xs) - x0 + pad, hh = Math.max(...ys) - y0 + pad, k = Math.max(w, hh) / 300;
    const svg = el("svg", { viewBox: `${x0} ${y0} ${w} ${hh}`, style: `height:${hpx}px`, preserveAspectRatio: "xMidYMid meet" }, fig);
    drawCourse(svg, geo);
    H.shots.forEach(s => { const c = SHOTC[s[1]], thin = s[1] === "c";
      el("line", { x1: s[2], y1: s[3], x2: s[4], y2: s[5], stroke: c, "stroke-width": (thin ? 1.2 : 2.4) * k, "stroke-opacity": thin ? 0.7 : 1, "stroke-linecap": "round" }, svg);
      if (!thin) { el("circle", { cx: s[4], cy: s[5], r: 4.2 * k, fill: c, stroke: "#111", "stroke-width": 1 * k }, svg).appendChild(el("title", {}, null, `${s[0]} ${s[6]} 碼`)); } });
    if (H.pin) { const [px, py] = H.pin; el("line", { x1: px, y1: py, x2: px, y2: py - 16 * k, stroke: "#fff", "stroke-width": 1.5 * k }, svg); el("polygon", { points: `${px},${py - 16 * k} ${px + 9 * k},${py - 12.5 * k} ${px},${py - 9 * k}`, fill: "#ff3b3b" }, svg); }
    return fig; };
  const holeMap1 = (r, H, hpx) => { const geo = (REPORT.geo[H.cid] || {})[H.h] || {}, fig = document.createElement("figure"), row = r.holes[H.n - 1] || [], st = row[6], putts = row[4];
    const par = H.par || row[1], zone = r.parts && r.parts[H.n - 1] ? `${r.parts[H.n - 1]} ${H.h}` : `第 ${H.n} 洞`, len = r.len && r.len[H.n];
    const pts = [].concat(...(geo.outline || []), ...H.shots.map(s => [[s[2], s[3]], [s[4], s[5]]]), H.pin ? [H.pin] : []), xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    if (!pts.length) return fig;
    const pad = geo.outline ? 10 : 40, x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad, w = Math.max(...xs) - x0 + pad, hh = Math.max(...ys) - y0 + pad, k = Math.max(w, hh) / 300;
    const svg = el("svg", { viewBox: `${x0} ${y0} ${w} ${hh}`, style: `height:${hpx}px`, preserveAspectRatio: "xMidYMid meet" }, fig);
    drawCourse(svg, geo);
    H.shots.forEach((s, i) => { const c = SHOTC[s[1]], thin = s[1] === "c";
      el("line", { x1: s[2], y1: s[3], x2: s[4], y2: s[5], stroke: c, "stroke-width": (thin ? 1.2 : 2.4) * k, "stroke-opacity": thin ? 0.7 : 1, "stroke-linecap": "round" }, svg);
      if (!thin) el("circle", { cx: s[4], cy: s[5], r: 4.2 * k, fill: c, stroke: "#111", "stroke-width": 1 * k }, svg); });
    if (H.pin) { const [px, py] = H.pin; el("line", { x1: px, y1: py, x2: px, y2: py - 16 * k, stroke: "#fff", "stroke-width": 1.5 * k }, svg); el("polygon", { points: `${px},${py - 16 * k} ${px + 9 * k},${py - 12.5 * k} ${px},${py - 9 * k}`, fill: "#ff3b3b" }, svg); }
    const tl = h("div", "mtl", fig); h("span", "z", tl, zone); h("b", "", tl, len ? `${len} 碼` : `Par ${par}`); if (len) h("span", "", tl, `Par ${par}`);
    const AB = REPORT.abbr || {}, ab = c => AB[c] || String(c).replace(/^(\d)鐵$/, "$1i").replace(/^(\d\d)度$/, "$1°"), NUMS = "①②③④⑤⑥⑦⑧⑨⑩", br = h("div", "mbr", fig);
    if (st) { const dv = st - par, hd = h("div", "hd", br); h("b", "", hd, `${st} 桿（${dv > 0 ? "+" + dv : dv === 0 ? "Par" : "−" + (-dv)}）`); if (putts != null) h("span", "", hd, row[8] != null ? `${row[8]} ON ${putts} 推` : `${putts} 推`); }
    H.shots.forEach((s, i) => { const rr = h("div", "r", br); h("i", "", rr, NUMS[i] || i + 1); const cb = h("b", "", rr, ab(s[0])); cb.style.color = SHOTC[s[1]] === undefined ? "" : SHOTC[s[1]]; h("span", "", rr, s[9] ? "" : String(s[6])); if (s[9]) h("em", "hz" + (s[9] === "OB" ? " ob" : ""), rr, s[9]); });
    if (row[7] && !H.shots.some(s => s[9])) { const rr = h("div", "r", br); h("i", "", rr, "罰"); h("b", "", rr, row[7] + " 桿"); }
    if (row[9]) { const rr = h("div", "r miss", br); h("i", "", rr, "?"); h("b", "", rr, `漏記 ${row[9]} 桿`); }
    return fig; };
  const mapPanel = (r, host) => { const HS = r.gps.holes, narrow = NARROW(), s2 = h("div", "sub", host), grid = h("div", "maps strip" + (narrow ? "" : " big"), host), lg = h("div", "legend", host);
    grid.style.setProperty("--per", 1);
    lg.innerHTML = [["d", "一號木"], ["w", "鐵木桿"], ["i", "鐵桿"]].map(([c, t]) => `<span><svg width="20" height="14"><line x1="1" y1="7" x2="13" y2="7" stroke="${SHOTC[c]}" stroke-width="2.4"/><circle cx="15" cy="7" r="3.6" fill="${SHOTC[c]}"/></svg>${t}</span>`).join("") +
      `<span><svg width="20" height="14"><line x1="1" y1="7" x2="19" y2="7" stroke="var(--ink2)" stroke-width="1.2"/></svg>切球桿</span>`;
    const figs = HS.map(H => grid.appendChild(holeMap1(r, H, narrow ? 520 : 600)));
    if (narrow) h("span", "", s2, "↔ 左右滑動看下一洞");
    else { const bs = HS.map((H, i) => { const b = h("button", "fchip" + (i ? "" : " on"), s2, String(H.n)); b.addEventListener("click", () => grid.scrollTo({ left: figs[i].offsetLeft - grid.offsetLeft, behavior: "smooth" })); return b; });
      grid.addEventListener("scroll", () => { const i = Math.round(grid.scrollLeft / Math.max(1, grid.clientWidth)); bs.forEach((b, k) => b.classList.toggle("on", k === i)); }); }
    grid.scrollLeft = 0; };
  // 點開一場：計分卡在上，下面一排按鈕選要看的細節
  const detail = r => { const box = document.createElement("div"); h("div", "scname", box, r.course + (WIDE_R() ? "　" + r.date : "")); box.appendChild(card(r));
    const tabs = h("div", "tabs dtabs", box), panel = h("div", "panel", box); let open = null;
    const items = [["明細", p => p.appendChild(shotsTable(r))]];
    if (r.clubs && r.clubs.length) items.push(["距離", p => clubPanel(r, p)]);
    if (r.gps) { items.push(["開球", p => drivePanel(r, p)]); items.push(["揮桿圖", p => mapPanel(r, p)]); }
    items.forEach(([label, fill]) => { const b = h("button", "fchip", tabs, label);
      b.addEventListener("click", () => { const was = open === label; open = was ? null : label; panel.replaceChildren(); [...tabs.children].forEach(c => c.classList.toggle("on", !was && c === b)); if (!was) fill(panel); }); });
    return box; };
  const clubPanel = (r, p) => { const tb = h("table", "ptab", p), hr = h("tr", "", tb); ["球桿", "這一場", "之前 " + REPORT.recent_n + " 場", "之前全部"].forEach(x => h("th", "", hr, x));
    const cell = (tr, v, strong) => { const td = h("td", strong ? "strong" : "", tr); if (!v) { td.textContent = "—"; return; } td.textContent = v[0]; const sm = h("span", "", td, " (" + v[1] + ")"); sm.style.cssText = "font-size:11px;font-weight:400;color:var(--muted)"; };
    r.clubs.forEach(([c, a, b, d]) => { const tr = h("tr", "", tb); h("td", "", tr, c); cell(tr, a, 1); cell(tr, b); cell(tr, d); });
    h("div", "cap", p, "常態距離（中位數，碼），括號是球數"); };
  const shotsTable = r => { const box = document.createElement("div"), tb = h("table", "rt", box), hh = h("tr", "", tb);
    ["洞", "Par", "碼", "擊球", "ON", "推", "桿", "±"].forEach(x => h("th", "", hh, x));
    const AB = REPORT.abbr || {}, ab = c => AB[c] || String(c).replace(/^(\d)鐵$/, "$1i").replace(/^(\d\d)度$/, "$1°");
    r.holes.forEach(([hole, par, yd, shots, putts, est, st, pen, on, miss], i) => { const tr = h("tr", "", tb);
      h("td", "", tr, hole); h("td", "", tr, par); h("td", "", tr, yd || ""); const td = h("td", "shots", tr);
      shots.forEach(([c, dd, sr, wc, hz, bad]) => { const b = h("span", "chip" + (bad ? " bad" : ""), td); h("b", "", b, ab(c)); if (hz) h("i", "hz" + (hz === "OB" ? " ob" : ""), b, hz); b.title = c; });
      if (pen && !shots.some(x => x[4])) { const b = h("span", "chip pen", td); h("b", "", b, "罰"); h("span", "", b, " " + pen); }
      for (let k = 0; k < (miss || 0); k++) { const b = h("span", "chip miss", td); h("b", "", b, "?"); b.title = "手錶沒記到"; }
      h("td", "", tr, on == null ? "" : on); h("td", "", tr, putts == null ? "" : putts); h("td", "", tr, st);
      const dv = st - par; h("td", dv >= 3 ? "bad" : dv <= 0 ? "good" : "", tr, dv > 0 ? "+" + dv : dv === 0 ? "E" : dv);
      if (r.holes.length > 9 && (i % 9 === 8 || i === r.holes.length - 1)) { const k = i - i % 9, part = r.holes.slice(k, i + 1), t2 = h("tr", "tot nine", tb), nm = h("td", "", t2, r.parts && r.parts[k] ? r.parts[k] : k === 0 ? "前九" : k === 9 ? "後九" : ""); nm.colSpan = 3; nm.style.textAlign = "left"; { const nb = part.reduce((a, x) => a + x[3].filter(y => y[5]).length, 0), mc = h("td", "shots", t2); if (nb && r.site) h("b", "mist", mc, `失誤 ${nb}`); }
        h("td", "", t2, part.every(x => x[8] != null) ? part.reduce((a, x) => a + x[8], 0) : ""); h("td", "", t2, part.every(x => x[4] != null) ? part.reduce((a, x) => a + x[4], 0) : ""); h("td", "", t2, part.reduce((a, x) => a + x[6], 0)); h("td", "", t2, ""); } });
    h("p", "note", box, "紅框＝失誤（下水、OB、切了沒上果嶺、距離不到常態七成）　？＝沒記到的一桿　ON＝幾桿上果嶺　每一桿的距離看揮桿圖");
    return box; };
  const T = sec.targets || {}, full = r => r.cat === "full";
  const FLAT = 2, FULL = sec.data.filter(full), F5 = FULL.slice(0, 5), AVG5 = F5.length ? F5.reduce((a, r) => a + r.strokes, 0) / F5.length : null;
  const blowOf = r => r.holes.filter(x => x[6] - x[1] >= 3).length, threeOf = r => r.holes.filter(x => x[4] >= 3).length;
  const lvl = dv => dv <= -1 ? "b" : dv === 0 ? "p" : dv === 1 ? "g" : dv === 2 ? "d" : "x";
  const cardEl = r => { const c = document.createElement("div"); c.className = "rcard"; c.tabIndex = 0;
    const top = h("div", "rc1", c), L = h("div", "rttl", top); h("span", "rdate", L, (r.date.slice(0, 4) !== String(new Date().getFullYear()) ? r.date.slice(2, 4) + "/" : "") + r.date.slice(5).replace("-", "/")); h("span", "rname", L, r.short);
    [r.tag, r.n !== 18 ? r.n + " 洞" : ""].filter(Boolean).forEach(x => h("span", "rsub", L, x));
    const stat = (box, k, v, ok, ic) => { const d = h("div", "", box); if (ic && ic !== "dot") h("i", ic, d); h("span", ic === "dot" ? "p3" : "", d, k + " "); h("b", ok ? "ok" : "", d, v == null ? "—" : v); };
    const s2 = h("div", "rstk", top); stat(s2, "推桿", r.putts, full(r) && r.putts != null && r.putts <= T.putts);
    const R = h("div", "rtot", top); const dir = full(r) && AVG5 != null && Math.abs(r.strokes - AVG5) > FLAT ? (r.strokes < AVG5 ? "dn" : "up") : "";
    h("b", dir, R, r.strokes); h("i", dir, R, dir ? (dir === "dn" ? "▼" : "▲") : "");
    const sc = h("div", "rsc", c); { const lb = h("div", "lb", sc); h("u", "", lb, "\u00a0"); h("b", "", lb, "+"); }
    r.holes.forEach((x, i) => { const dv = x[6] - x[1], cell = h("div", "c" + (i && i % 9 === 0 ? " gap" : ""), sc); h("u", "", cell, x[1]); h("b", lvl(dv), cell, dv > 0 ? dv : dv === 0 ? "●" : "−" + (-dv)); h("i", x[4] >= 3 ? "t" : "", cell, x[4] == null ? "" : "•".repeat(Math.min(x[4], 4))); });
    const ft = h("div", "rsum", c), two = r.holes.length > 9 && r.holes.length <= 18, sub = i => { const part = r.holes.slice(i, i + 9), sp = h("span", "rs", ft, (r.parts && r.parts[i] ? r.parts[i] : i === 0 ? "前九" : "後九") + " "); h("b", "", sp, part.reduce((a, x) => a + x[6], 0)); };
    if (two) sub(0); else h("span", "rs", ft);
    const mid = h("span", "mid", ft); { const nb = r.holes.filter(x => x[6] < x[1]).length; if (nb) { const d = h("div", "", mid); h("b", "bd", d, "★"); h("b", "", d, " " + nb); } } stat(mid, "Par", r.holes.filter(x => x[6] === x[1]).length, false, "pd"); stat(mid, "爆桿", blowOf(r), full(r) && blowOf(r) <= T.blow, "sq"); if (r.site) stat(mid, "失誤", r.holes.reduce((a, x) => a + x[3].filter(y => y[5]).length, 0), false, "mx");
    if (two) sub(9); else h("span", "rs", ft);
    return c; };
  const WIDE = innerWidth >= 900; let sel = null;
  const head = h("div", "rhd", host), bar2 = h("div", "filters", host), wrap = h("div", WIDE ? "r2" : "r1", host), list = h("div", "rlist", wrap), pane = WIDE ? h("div", "rpane", wrap) : null;
  const draw = () => { list.replaceChildren(); if (pane) pane.replaceChildren();
    const rows = sec.data.filter(r => on[r.cat]); let month = "";
    const pick = (r, c) => { if (WIDE) { list.querySelectorAll(".rcard.on").forEach(x => x.classList.remove("on")); c.classList.add("on"); sel = r; pane.replaceChildren(); pane.appendChild(detail(r)); pane.scrollTop = 0; return; }
      const nx = c.nextSibling, was = nx && nx.classList.contains("rdet"); const top0 = c.getBoundingClientRect().top;
      list.querySelectorAll(".rdet").forEach(d => d.remove()); list.querySelectorAll(".rcard.on").forEach(x => x.classList.remove("on"));
      if (!was) { const d = document.createElement("div"); d.className = "rdet"; const hd = h("div", "rdh", d); h("b", "", hd, r.short + "　" + r.date.slice(5).replace("-", "/") + "　" + r.strokes + " 桿"); h("span", "", hd, "收合 ▴"); hd.addEventListener("click", () => pick(r, c)); d.appendChild(detail(r)); c.after(d); c.classList.add("on"); }
      scrollBy(0, c.getBoundingClientRect().top - top0); };
    rows.forEach(r => { const m = r.date.slice(0, 7); if (m !== month) { month = m; h("div", "rmonth", list, m.slice(0, 4) + " 年 " + (+m.slice(5)) + " 月"); }
      const c = cardEl(r); list.appendChild(c); c.addEventListener("click", () => pick(r, c));
      [5, 10, 20].forEach(n => { if (FULL.length >= n && r === FULL[n - 1]) { const g = FULL.slice(0, n), e5 = h("div", "r5", list), sp = h("span", "", e5); sp.append("最近 " + n + " 場　平均 "); h("b", "", sp, (g.reduce((a, x) => a + x.strokes, 0) / n).toFixed(1)); sp.append("　最好 "); h("b", "", sp, Math.min(...g.map(x => x.strokes))); } });
      c.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(r, c); } const dir = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0; if (!dir) return; e.preventDefault(); const cs = [...list.querySelectorAll(".rcard")], t2 = cs[cs.indexOf(c) + dir]; if (t2) { t2.focus(); if (WIDE) t2.click(); } }); });
    if (WIDE && rows.length) { const cs = [...list.querySelectorAll(".rcard")], i = Math.max(0, rows.indexOf(sel)); pick(rows[i], cs[i]); } };
  head.remove();
  cats.forEach(([key, label]) => { const n = sec.data.filter(r => r.cat === key).length, b = h("button", "fchip" + (on[key] ? " on" : ""), bar2, `${label} ${n}`);
    b.addEventListener("click", () => { on[key] = !on[key]; b.classList.toggle("on", on[key]); draw(); }); });
  { const lg = h("div", "rlg", host); [["b", "Birdie", "−1"], ["p", "Par", "●"], ["g", "Bogey", "1"], ["d", "Double", "2"], ["x", "爆桿（+3 以上）", "3"]].forEach(([k, t, n]) => { const sp = h("span", "", lg); h("span", "k " + k, sp, n); sp.append(t); }); h("span", "", lg, "上面的小字是標準桿，＋ 那一列是超出標準桿幾桿　• 推桿數（紅色＝三推以上）"); if (AVG5 != null) { const s3 = h("span", "", lg); h("b", "dn", s3, "▼"); s3.append(" 比最近 " + F5.length + " 場平均少　"); h("b", "up", s3, "▲"); s3.append(" 多（差 " + FLAT + " 桿以內不標）"); } }
  draw();
};

DRAW.plan = (sec, host) => {
  const d = sec.data, box = h("div", "plan", host);
  h("p", "lead", box, "AI 分析完 " + d.total + " 場的數據之後，制定的目標和訓練計劃。"); h("h3", "", box, "短期計劃（到 " + d.goal.by.replace("前", "") + "）");
  const g = h("div", "pgoal", box); h("div", "k", g, d.goal.by + "打出一場"); h("div", "big", g, d.goal.score + " 以下"); h("div", "k", g, "最近 " + d.base.n + " 場平均 " + d.base.strokes + "，最好 " + d.base.best + "・" + d.goal.next);
  h("h3", "", box, "每場只看兩個數字");
  const t2 = h("div", "ptiles", box);
  [["推桿", d.base.putts, d.check.putts + " 以下"], ["爆桿", d.base.blow + " 洞", d.check.blow + " 洞以下"]].forEach(([k, now, tg]) => { const t = h("div", "ptile", t2); h("div", "k", t, k); h("div", "v", t, tg); h("div", "k", t, "現在 " + now); });
  const mk = (td, v, ok) => { td.textContent = v == null ? "—" : v; if (v != null) td.className = ok ? "ok" : "no"; };
  const tb = h("table", "ptab", box), hr = h("tr", "", tb); ["", "球場", "總桿", "推桿", "爆桿"].forEach(x => h("th", "", hr, x));
  const rows = d.after.length ? d.after : d.recent;
  rows.forEach(r => { const tr = h("tr", "", tb); h("td", "", tr, r.date.slice(2).replaceAll("-", "/")); h("td", "", tr, r.short);
    mk(h("td", "", tr), r.strokes, r.strokes <= d.goal.score); mk(h("td", "", tr), r.putts, r.putts <= d.check.putts); mk(h("td", "", tr), r.blow, r.blow <= d.check.blow); });
  h("div", "cap", box, d.after.length ? "計劃開始後的 " + d.after.length + " 場，綠色是達標" : "計劃開始前最近 " + rows.length + " 場，綠色是達標。下一場起列在這裡");
  h("h3", "", box, "四個練習目標");
  d.train.forEach(([t, goal, how], i) => { const c = h("div", "pcard", box); h("b", "num", c, String(i + 1)); const w = h("div", "", c); const r = h("div", "t", w, t + "　"); h("span", "tg", r, goal); h("div", "f", w, "現在 " + d.factlist[i]); h("div", "b", w, how); });
  h("h3", "", box, "上場只想這幾件事");
  const c = h("div", "pcard rule", box), ol = h("ol", "", c); d.rule.forEach(x => h("li", "", ol, x));
  const bt = h("table", "ptab", box), bh = h("tr", "", bt); ["離旗桿", "球桿", "常態", "打得好"].forEach(x => h("th", "", bh, x));
  d.bag.forEach(b => { const tr = h("tr", "", bt), few = b.n < 30; h("td", "", tr, b.use); h("td", "strong", tr, b.club); h("td", "", tr, b.chip ? "—" : few ? "累積中" : b.med); h("td", "", tr, b.chip || few ? "—" : b.good); });
  h("div", "cap", box, "碼。常態與打得好是全揮桿的距離");
  h("h3", "", box, "長期計劃（" + d.goal.next + "）");
  const ul = h("ul", "plater", box); d.later.forEach(x => h("li", "", ul, x));
  h("div", "cap", box, "短期目標達成後才開始");
};

document.getElementById("meta").textContent = REPORT.meta;
[["", "自動"], ["light", "淺"], ["dark", "深"]].forEach(([k, t]) => { const b = h("button", k === THEME ? "on" : "", document.getElementById("thm"), t); b.addEventListener("click", () => { try { if (k) localStorage.setItem("mi-theme", k); else localStorage.removeItem("mi-theme"); } catch (e) {} location.reload(); }); });
document.getElementById("h1").textContent = REPORT.title; document.title = REPORT.title;
(REPORT.tabs || []).forEach(([href, label]) => { const a = h("a", href === (REPORT.tab || REPORT.page) ? "on" : "", document.getElementById("ptabs"), label); a.href = href; });
if (matchMedia("(max-width:640px)").matches && (REPORT.tabs || []).length) { const B = document.body, nv = document.getElementById("ptabs"), gp = document.createElement("div"); gp.className = "tabgrip"; B.append(gp); B.prepend(nv); B.classList.add("tabs-top"); const th = () => B.style.setProperty("--tabh", nv.offsetHeight + "px"); th(); addEventListener("resize", th); }   /* 手機：頁籤固定在每一頁最上面，不收起來（使用者 2026-11-28） */
{ const a = document.getElementById("link"); if (REPORT.link) { a.textContent = REPORT.link[1]; a.href = REPORT.link[0]; } else a.parentNode.style.display = "none"; }
const main = document.getElementById("main"), nav = document.getElementById("nav");
function render() {
  main.replaceChildren(); nav.replaceChildren();
  nav.style.display = REPORT.sections.length > 1 ? "" : "none";
  for (const sec of REPORT.sections) {
    if (REPORT.sections.length > 1) { const a = h("a", "", nav, sec.short || sec.title); a.href = "#" + sec.id; }
    const s = h("section", sec.kind === "roundlist" || sec.kind === "caddie" ? "solo" : "", main); s.id = sec.id;
    h("h2", "", s, sec.title);
    const host = h("div", "chart", s);
    if (!DRAW[sec.kind]) { host.textContent = "場次還不夠，這一頁的統計算不出來。多打幾場再回來看。"; } else { try { DRAW[sec.kind](sec, host); } catch (e) { host.textContent = sec.kind === "caddie" ? "桿弟需要更多場次的擊球紀錄才能給建議（大約 5 場以上）。" : "這張圖畫不出來（資料不足）"; console.error(sec.id, e); } }
    if (host.classList.contains("scroll")) { const hint = h("div", "hint", null, "↔ 可左右滑動"); host.before(hint); }
    if (sec.kind === "caddie") continue;
    const det = h("details", "", s); h("summary", "", det, sec.table.head.length ? "數字表與計算方式" : "說明"); h("p", "note", det, sec.note);
    const t = h("table", "", det), hr = h("tr", "", t);
    sec.table.head.forEach(label => h("th", "", hr, label));
    sec.table.rows.forEach(r => { const tr = h("tr", "", t); r.forEach(v => h("td", "", tr, v)); });
  }
}
render();
let rt, lastW = innerWidth;   // 手機捲動時網址列收合也會觸發 resize，只有寬度真的變了才重畫
addEventListener("resize", () => { if (innerWidth === lastW) return; lastW = innerWidth; clearTimeout(rt); rt = setTimeout(render, 150); });
</script></body></html>
"""

NAV_SHORT = {"rounds": "場次總表", "score-trend": "總桿", "handicap": "差點", "club-distance": "擊球距離", "score-par": "洞型", "putt-count": "推桿", "courses": "打過的球場", "plan": "訓練計劃", "caddie": "桿弟",
             "putt-holes": "每月推桿", "club-recent": "球桿比較", "seq-cost": "失誤代價", "mishit-trend": "打壞比例", "mishit-club": "各桿打壞", "seq-club": "各桿成敗", "seq-chain": "連續失誤",
             "chip": "切球", "chip-once": "切一次上果嶺", "score-par-dist": "洞型分布", "length-par3": "三桿洞洞長", "length-par4": "四桿洞洞長", "length-par5": "五桿洞洞長", "blowup": "爆桿",
             "putt-trend": "推桿趨勢", "putt-share": "推桿佔比", "putt-par": "洞型推桿", "putt-approach": "上果嶺距離", "putt-course": "球場推桿"}

_pp = __import__("pathlib").Path(ROOT) / "data" / "photos" / "index.json"
PHOTOS = json.loads(_pp.read_text()) if _pp and _pp.exists() else {}

def main():
    tmp = os.path.join(tempfile.mkdtemp(), "g.db"); shutil.copyfile(DB, tmp)   # 掛載資料夾上直接開 SQLite 會出錯
    con = sqlite3.connect(tmp)
    n, first, last = con.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM rounds").fetchone()
    shots = con.execute("SELECT COUNT(*) FROM shots").fetchone()[0]
    meta = f"{n} 場（{first} 至 {last}）・{shots} 桿擊球紀錄・產生時間 {datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))):%Y-%m-%d %H:%M}"
    T0 = SITE_TITLE
    pages = [("index.html", T0, MAIN, None, None),
             ("data.html", T0, DATA, ["more.html", "更多分析（其餘 %d 張圖）→" % len(MORE)], None),
             ("more.html", T0, MORE, ["data.html", "← 回數據"], "data.html"),
             ("courses.html", T0, [section_courses], None, None),
             ("plan.html", T0, [section_plan], None, None),
             ("caddie.html", T0, [section_caddie], None, None)]
    tabs = [["index.html", "場次"], ["data.html", "數據"], ["courses.html", "球場"], ["plan.html", "計劃"], ["caddie.html", "桿弟"]]
    if not PLAN_ON:                                                  # 訓練計劃是原作者個人的內容（PLAN_* 常數）；新使用者預設不產生這一頁
        pages = [p for p in pages if p[0] != "plan.html"]; tabs = [t for t in tabs if t[0] != "plan.html"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True); skipped = []
    for fname, title, sections, link, tab in pages:
        secs = []
        for f in sections:                                       # 資料很少時（新使用者只有幾場）有些統計算不出來：略過那一張圖，不要讓整個網站產生不出來
            try: secs.append(f(con))
            except Exception as e:
                skipped.append(f"{fname}：{getattr(f, '__name__', '區塊')}（{type(e).__name__}: {e}）")
        if not secs: secs = [{"id": "empty", "title": "資料還不夠", "note": "", "kind": "empty", "data": [], "table": {"head": [], "rows": []}}]
        for x in secs: x["short"] = NAV_SHORT.get(x["id"], x["title"])          # 導覽列只放簡稱，不放說明
        report = {"abbr": ABBR, "clubs": {"driver": DRIVER, "fairway": FAIRWAY, "chip": CHIP_CLUB, "sand": SAND_CLUB}, "meta": meta, "title": title, "link": link, "success": SUCCESS, "recent_n": RECENT_N, "sections": secs, "tabs": tabs, "tab": tab, "page": fname, "geo": GEO if any(x["kind"] in ("roundlist", "courses", "caddie") for x in secs) else {}, "photos": PHOTOS if any(x["kind"] == "caddie" for x in secs) else {}}
        path = os.path.join(os.path.dirname(OUT), fname)
        open(path, "w", encoding="utf-8").write(PAGE.replace("__TITLE__", title).replace("__DATA__", json.dumps(report, ensure_ascii=False)))
        print("已產生", path, "|", len(sections), "個區塊")
    print(meta)
    if skipped: print("⚠️ 有 %d 個區塊因為資料不足或計算失敗而略過（場次多了之後通常會自己出現；原作者的資料不應該出現這一行）：\n  " % len(skipped) + "\n  ".join(skipped))

if __name__ == "__main__":
    main()
