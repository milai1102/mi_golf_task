"""步驟 3：彙整資料庫 (data/processed/golf.db) → 一頁式報表 (reports/index.html)。

所有圖表都在同一頁，一張圖一個區塊。新增圖表：寫一個 section_xxx(con) 函式回傳
{id, title, note, kind, data, table}，加進 SECTIONS，並在頁面的 DRAW 物件加對應的繪圖函式。
用法：python3 scripts/build_report.py
"""
import csv, datetime, json, os, shutil, sqlite3, statistics, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB, OUT = os.path.join(ROOT, "data/processed/golf.db"), os.path.join(ROOT, "reports/index.html")
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
        WHERE shot_type != 'PUTT' AND club NOT IN ('','Putter','已刪除球桿','未知球桿')""").fetchall()
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
        WHERE shot_type != 'PUTT' AND club NOT IN ('','Putter','已刪除球桿','未知球桿')""").fetchall()
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
    full = [r for r in rows if roles.get(r[3]) == "full" and r[4] not in ("CHIP", "RECOVERY", "PUTT") and r[6] is not None and r[7] is not None]
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
        WHERE s.shot_type NOT IN ('CHIP','RECOVERY','PUTT') ORDER BY r.date""").fetchall()
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
    groups = {"一號木桿": "一號木桿", "鐵木桿": "鐵木桿"}; rows_lbl = ["一號木桿", "鐵木桿", "7 鐵到 P 桿"]
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
    for r in rows: seqs.setdefault(r["garmin_scorecard_id"], {})[int(r["seq"])] = r["layout"]
    lab = {}
    for sid, d in seqs.items():
        L = layout_labels(next(iter(d.values()))); ss = sorted(d)
        for i, q in enumerate(ss): lab[q] = (L[1] if len(L) == 2 and len(ss) > 9 and i >= 9 else L[0]) if L else ""
    per = [lab[q] for q in sorted(lab)]; groups = [x for i, x in enumerate(per) if i == 0 or x != per[i - 1]]
    if len(groups) < 2 or not all(groups): return None, ""
    return per, "＋".join(groups)

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
                club = c[1]; cat = "d" if club == "一號木桿" else "c" if roles.get(club) == "chip" else "w" if "木" in club else "i"
                sp, ep = (int(s[2]), int(s[3])), (int(e[2]), int(e[3])); yd = round(int(c[3]) * 1.09361)
                cur["shots"].append([club, cat, sp[0], sp[1], ep[0], ep[1], yd, e[4]])
                if cat == "d" and int(c[0]) == 1:                                   # 只算開球那一桿
                    fo = fairway_offset(sp, ep, int(c[3]), GEO.get(cur["cid"], {}).get(str(cur["h"]), {}).get("fairway", []))
                    drive.append({"hole": seq, "yd": yd, "width": fo[0] if fo else None, "off": fo[1] if fo else None, "fw": e[4] == "Fairway"})
    holes.sort(key=lambda h: h["n"]); drive.sort(key=lambda d: d["hole"])
    return {"holes": holes, "drive": drive} if holes else None

def section_courses(con):
    """球場紀錄：每個球場、每一洞把歷次打過的路線疊在一起，下場前或打完檢討用。"""
    courses = {}      # 簡稱 → {name, layouts: {cid: {label, holes: {洞號: {par, plays: [...]}}}}}
    for rid, date, course, short, fname in con.execute("SELECT round_id, date, course, course_short, file FROM rounds ORDER BY date"):
        g = round_gps(fname)
        if not g: continue
        hrow = {seq: (par, st, putts) for seq, par, st, putts in con.execute("SELECT seq, par, strokes, putts FROM holes WHERE round_id=?", (rid,))}
        layout = {r["garmin_scorecard_id"]: r["layout"] for r in csv.DictReader(open(os.path.join(ROOT, "data/rounds", fname), encoding="utf-8-sig"))}
        cid_layout = {}
        for sid, lay in layout.items():
            p = os.path.join(ROOT, "data/raw/gps", f"{sid}.txt")
            if os.path.exists(p): cid_layout[open(p, encoding="utf-8").readline().split("|")[1]] = "／".join(layout_labels(lay))
        per = round_parts(fname)[0]
        c = courses.setdefault(short, {"name": course, "short": short, "layouts": {}, "last": date, "rounds": set()})
        c["last"] = date; c["rounds"].add(rid)
        for H in g["holes"]:
            par, st, putts = hrow.get(H["n"], (H["par"], None, None))
            L = c["layouts"].setdefault(H["cid"], {"cid": H["cid"], "label": (per[H["n"] - 1] if per and H["n"] <= len(per) else "") or cid_layout.get(H["cid"], ""), "holes": {}})
            hh = L["holes"].setdefault(H["h"], {"h": H["h"], "par": par, "plays": []})
            hh["plays"].append({"date": date, "st": st, "putts": putts, "pin": H["pin"], "shots": H["shots"]})
    out = []
    for c in sorted(courses.values(), key=lambda c: c["last"], reverse=True):
        lays = [{"cid": L["cid"], "label": L["label"], "holes": [L["holes"][k] for k in sorted(L["holes"])]} for L in c["layouts"].values()]
        out.append({"short": c["short"], "name": c["name"], "n": len(c["rounds"]), "last": c["last"], "layouts": lays})
    note = "每一洞把歷次的擊球路線疊在同一張圖上，最近一次最清楚，越早的越淡。紅＝一號木、橘＝鐵木桿、藍＝鐵桿、細白線＝切球桿。球場輪廓是依 Garmin 地圖重新描的示意圖。"
    return {"id": "courses", "title": "打過的球場", "note": note, "kind": "courses", "data": out, "table": {"head": [], "rows": []}}

def section_roundlist(con):
    tags = {"short_course": "短球場", "partial": "未打完", "nine": "9 洞"}; out = []
    for rid, date, course, cat, n, strokes, over, diff, short, fname in con.execute("SELECT round_id, date, course, category, holes_played, strokes, over_par, differential, course_short, file FROM rounds ORDER BY date DESC"):
        holes = []
        for seq, hole, par, st, putts, src, yd in con.execute("SELECT seq, hole, par, strokes, putts, putts_source, length_yd FROM holes WHERE round_id=? ORDER BY seq", (rid,)):
            shots = con.execute("SELECT club, distance_yd FROM shots WHERE round_id=? AND seq=? AND shot_type != 'PUTT' ORDER BY shot_no", (rid, seq)).fetchall()
            holes.append([hole, par, yd, [[c or "?", d] for c, d in shots], putts, src == "estimated", st])
        pv = [h[4] for h in holes if h[4] is not None]
        out.append({"date": date, "course": course, "short": short, "tag": tags.get(cat, ""), "cat": {"full_18": "full", "short_course": "short"}.get(cat, "partial"), "n": n, "strokes": strokes, "over": over, "hcp": diff,
                    "putts": sum(pv) if pv else None, "est": any(h[5] for h in holes), "holes": holes, "gps": round_gps(fname), "parts": round_parts(fname)[0], "combo": round_parts(fname)[1]})
    note = "由新到舊排列，預設只列完整打完 18 洞的場次，上方按鈕可加入未打完與短球場的場次。點任一場展開 18 洞明細。" \
           "擊球欄是被手錶記錄到的擊球，依序為球桿與距離（碼）。沒有手錶紀錄的洞，推桿數由「總桿 − 記錄擊球 − 罰桿」推算。"
    return {"id": "rounds", "title": "場次總表", "note": note, "kind": "roundlist", "data": out, "table": {"head": [], "rows": []}}

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
MAIN = [section_roundlist, section_score_trend, section_handicap,                 # 我有沒有進步？
        section_club_distance,                                  # 每支桿可以期待什麼？
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
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MI 高爾夫數據</title>
<style>
:root{color-scheme:light;--surface:#fcfcfb;--page:#f4f3ef;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--series:#2a78d6;--trend:#eb6834;--up:#0c8a0c;--down:#d03b3b;--putt:#1baf7a}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--surface:#1a1a19;--page:#111110;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--series:#3987e5;--trend:#f08a4b;--up:#2fbf4f;--down:#f06a6a;--putt:#199e70}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font-family:-apple-system,"PingFang TC","Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;line-height:1.6}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 60px}
header h1{font-size:24px;font-weight:600;margin:0 0 4px}
header p{margin:0;color:var(--ink2);font-size:14px}
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
.chip{display:inline-block;border:1px solid var(--border);border-radius:6px;padding:1px 7px;margin:2px 4px 2px 0;font-size:12px;white-space:nowrap}.chip b{font-weight:500}.chip span{color:var(--ink2)}
.rt td.bad{color:#d03b3b;font-weight:600}.rt td.good{color:#0c8a0c;font-weight:600}.rt tr.tot td{font-weight:600;border-top:1px solid var(--axis)}
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
.tabs{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 4px}.chart .legend svg,.chart .sclg svg{display:inline-block;width:auto;height:auto;flex:none}.panel .legend{margin-top:10px}.panel{padding:6px 0 4px}
.maps{display:grid;gap:10px;margin-top:8px}.maps figure{margin:0;background:#0c0c0b;border-radius:10px;padding:6px 4px 4px;min-width:0}.maps figcaption{font-size:12px;color:#c3c2b7;text-align:center;padding-bottom:2px;white-space:nowrap}
.maps svg{display:block;width:100%}.maps.strip{width:calc(100vw - 46px);display:flex;overflow-x:auto;-webkit-overflow-scrolling:touch;scroll-snap-type:x mandatory;gap:8px;padding-bottom:6px}.maps.strip figure{flex:0 0 calc((100% - (var(--per) - 1) * 8px) / var(--per));scroll-snap-align:start}.sub{display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:12px;color:var(--ink2);margin-top:6px}.sub .fchip{font-size:12px;padding:2px 10px}
.ptabs{display:flex;margin:16px 0 4px;border:1px solid var(--border);border-radius:12px;background:var(--surface);padding:4px;gap:4px}.ptabs a{flex:1;text-align:center;font-size:15px;font-weight:600;color:var(--ink2);text-decoration:none;padding:9px 6px;border-radius:9px;white-space:nowrap}.ptabs a.on{background:var(--ink);color:var(--surface)}
nav{margin:10px 0 0}nav a{font-size:12px;padding:3px 10px}
.morelink{display:none}
.maps.strip.big{width:auto}.maps.strip.big figure{padding:10px 8px 8px}.maps.strip.big figcaption{font-size:15px;font-weight:600;color:#f0efec}
.cstat{display:flex;flex-wrap:wrap;justify-content:center;gap:4px 14px;font-size:12px;color:#c3c2b7;padding:2px 0 6px}.cstat b{color:#f0efec;font-weight:600}
.cplays{width:100%;margin-top:6px;font-size:12px;color:#c3c2b7}.cplays td{padding:3px 4px;text-align:center;border-bottom:1px solid #2c2c2a;white-space:nowrap}.cplays td.bad{color:#f06a6a;font-weight:600}.cplays td.good{color:#2fbf4f;font-weight:600}
.sub.holes .fchip{min-width:34px;padding:2px 6px}
.hint{display:none;font-size:12px;color:var(--muted);margin:0 0 4px}
.chart.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}.chart.scroll svg{width:var(--w);max-width:none}
details{overflow-x:auto}
@media (max-width:640px){
.wrap{padding:18px 10px 48px}header h1{font-size:21px}header p{font-size:12px}
nav{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:4px}nav a{flex:none;white-space:nowrap}
section{padding:14px 12px 12px;margin-top:14px;border-radius:10px}section h2{font-size:16px}
.hint{display:block}
.filters,.sub.holes{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:4px}.filters .fchip,.sub.holes .fchip{flex:none}
.rl{font-size:14px}.rl th,.rl td{padding:9px 4px;white-space:nowrap}.rl td.course{white-space:normal;line-height:1.35}.rl td.arrow,.rl>tr>th:last-child{display:none}
.rl tr.detail>td{padding:4px 0 12px 0;white-space:normal}.sc{padding:4px 2px 0}.m{width:23px;height:23px;font-size:14px;border-width:1.5px}.m.dbl,.m.blow,.m.eagle{outline-width:1.5px;outline-offset:1.5px}.scg .hn,.scg .pr{font-size:12px}.dots{font-size:11px;letter-spacing:0}.rt{font-size:12px}.rt td,.rt th{padding:5px 3px}.rt td.shots{white-space:normal}.chip{padding:0 5px;margin:1px 3px 1px 0;font-size:11px}
.legend{gap:6px 14px}th,td{padding:4px 8px}
}
</style></head><body><div class="wrap">
<header><h1 id="h1"></h1><p id="meta"></p></header><div class="ptabs" id="ptabs"></div><svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<pattern id="pFw" width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(40)"><rect width="14" height="14" fill="#7fc95a"/><rect width="7" height="14" fill="#8ed667"/></pattern>
<pattern id="pTree" width="22" height="22" patternUnits="userSpaceOnUse"><rect width="22" height="22" fill="#14351b"/><circle cx="6" cy="6" r="5.2" fill="#1f5128"/><circle cx="4.8" cy="4.8" r="2.4" fill="#2c6a35"/><circle cx="17" cy="16" r="5.6" fill="#1b4723"/><circle cx="15.8" cy="14.8" r="2.6" fill="#2a6332"/><circle cx="17" cy="4" r="3.2" fill="#193f20"/><circle cx="5" cy="17" r="3.4" fill="#1d4a25"/></pattern>
</defs></svg><nav id="nav"></nav><main id="main"></main><p class="morelink"><a id="link"></a></p></div>
<script>
const REPORT = __DATA__;
const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs, parent, text) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; };
const h = (tag, cls, parent, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; };

const BIN = 10;   // 色條每格 10 碼
const OK = REPORT.success;   // 成功門檻：眾數的幾成
const DARK = matchMedia("(prefers-color-scheme: dark)").matches;
const STOPS = DARK ? [[57, 135, 229], [138, 138, 132], [235, 96, 96]] : [[42, 120, 214], [226, 224, 216], [227, 73, 72]];   // 少=藍 → 中性 → 多=紅
const heat = t => { const u = t * 2, a = STOPS[u < 1 ? 0 : 1], b = STOPS[u < 1 ? 1 : 2], f = u < 1 ? u : u - 1;
  return "rgb(" + a.map((v, i) => Math.round(v + (b[i] - v) * f)).join(",") + ")"; };
// 球桿距離圖專用的機率色階：藍（少）→ 紫 → 紅（多），中間不經過灰色，才不會和「沒成功的灰點」混淆
const PSTOPS = DARK ? [[57, 135, 229], [170, 105, 215], [235, 96, 96]] : [[42, 120, 214], [150, 90, 200], [227, 73, 72]];
const prob = t => { const u = t * 2, a = PSTOPS[u < 1 ? 0 : 1], b = PSTOPS[u < 1 ? 1 : 2], f = u < 1 ? u : u - 1;
  return "rgb(" + a.map((v, i) => Math.round(v + (b[i] - v) * f)).join(",") + ")"; };
const NARROW = () => innerWidth <= 640;
// 寬度不夠時：圖照 min 寬度畫（字不縮小），外框改成可以左右滑動
const wide = (host, min, mobile) => { const cw = host.clientWidth; if (cw >= min) return cw;
  const w = mobile || min; host.classList.add("scroll"); host.style.setProperty("--w", w + "px"); return w; };
// 球場示意圖的畫法：長草是底色，樹林用深色加樹冠的圓點，球道用淺色斜條紋加亮邊，果嶺最亮，沙坑米色，水藍色
const COURSE_STYLE = { outline: { fill: "#4a8a3c" }, tree: { fill: "url(#pTree)", stroke: "#14351b", "stroke-width": 3, "stroke-linejoin": "round" }, water: { fill: "#3d8fe6", stroke: "#8cc4ff", "stroke-width": 1.5 },
  fairway: { fill: "url(#pFw)", stroke: "#c9f0a2", "stroke-width": 1.6, "stroke-linejoin": "round" }, sand: { fill: "#f1e2b4", stroke: "#b9a36a", "stroke-width": 1 }, green: { fill: "#b5ec83", stroke: "#e6ffc9", "stroke-width": 1.4, "stroke-linejoin": "round" } };
const drawCourse = (svg, geo) => ["outline", "tree", "fairway", "water", "sand", "green"].forEach(key => (geo[key] || []).forEach(p => el("polygon", Object.assign({ points: p.map(q => q.join(",")).join(" ") }, COURSE_STYLE[key]), svg)));
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
  let ci = 0, li = 0; const s1 = h("div", "filters", host), s2 = h("div", "sub", host), s3 = h("div", "sub holes", host), strip = h("div", "maps strip big", host), lg = h("div", "legend", host);
  lg.innerHTML = [["d", "一號木"], ["w", "鐵木桿"], ["i", "鐵桿"]].map(([c, t]) => `<span><svg width="20" height="14"><line x1="1" y1="7" x2="13" y2="7" stroke="${SHOTC[c]}" stroke-width="2.4"/><circle cx="15" cy="7" r="3.6" fill="${SHOTC[c]}"/></svg>${t}</span>`).join("") +
    `<span><svg width="20" height="14"><line x1="1" y1="7" x2="19" y2="7" stroke="var(--ink2)" stroke-width="1.2"/></svg>切球桿</span><span>越淡＝越早的場次</span>`;
  const fig = (L, H) => { const f = document.createElement("figure"), geo = (REPORT.geo[L.cid] || {})[H.h] || {}, P = H.plays, sts = P.map(p => p.st).filter(v => v != null);
    const dr = P.map(p => p.shots[0]).filter(s => s && s[1] === "d"), fw = dr.filter(s => s[7] === "Fairway").length;
    h("figcaption", "", f, `第 ${H.h} 洞　Par ${H.par}`);
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
  const draw = () => { s1.replaceChildren(); s2.replaceChildren(); s3.replaceChildren(); strip.replaceChildren(); const C = sec.data[ci], L = C.layouts[li] || C.layouts[0];
    sec.data.forEach((c, i) => { const b = h("button", "fchip" + (i === ci ? " on" : ""), s1, `${c.short} ${c.n}`); b.addEventListener("click", () => { ci = i; li = 0; draw(); }); });
    if (C.layouts.length > 1) C.layouts.forEach((l, i) => { const b = h("button", "fchip" + (i === li ? " on" : ""), s2, l.label || `區 ${i + 1}`); b.addEventListener("click", () => { li = i; draw(); }); });
    strip.style.setProperty("--per", NARROW() ? 1 : Math.min(3, L.holes.length));
    L.holes.forEach((H, i) => { const f = fig(L, H); strip.appendChild(f); const b = h("button", "fchip", s3, H.h); b.addEventListener("click", () => strip.scrollTo({ left: f.offsetLeft - strip.offsetLeft, behavior: "smooth" })); });
    strip.scrollLeft = 0; };
  draw();
};

DRAW.roundlist = (sec, host) => {
  const SHOW = 10, cats = [["full", "完整 18 洞", true], ["partial", "未打完", false], ["short", "短球場", false]];
  const on = Object.fromEntries(cats.map(c => [c[0], c[2]])); let all = false;
  const bar = h("div", "filters", host), t = h("table", "rl", host), more = h("button", "morebtn", host);
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
    const par = H.par || (r.holes[H.n - 1] || [])[1], zone = r.parts && r.parts[H.n - 1] ? `　${r.parts[H.n - 1]} ${H.h}` : ""; h("figcaption", "", fig, brief ? `${H.n}　P${par}` + (st ? `　${st}` : "") : (zone && NARROW() ? `${H.n}${zone}　P${par}` : `第 ${H.n} 洞${zone}　Par ${par}`) + (st ? `　${st} 桿` : ""));
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
  const mapPanel = (r, host) => { const strip = NARROW(); let per = strip ? 2 : 3, start = 0;   // 手機：全部的洞排成一橫列，左右滑動
    const HS = r.gps.holes, sizes = strip ? [1, 2, 3] : [2, 3];
    const s1 = h("div", "sub", host), s2 = h("div", "sub", host), grid = h("div", "maps", host), lg = h("div", "legend", host);
    lg.innerHTML = [["d", "一號木"], ["w", "鐵木桿"], ["i", "鐵桿"]].map(([c, t]) => `<span><svg width="20" height="14"><line x1="1" y1="7" x2="13" y2="7" stroke="${SHOTC[c]}" stroke-width="2.4"/><circle cx="15" cy="7" r="3.6" fill="${SHOTC[c]}"/></svg>${t}</span>`).join("") +
      `<span><svg width="20" height="14"><line x1="1" y1="7" x2="19" y2="7" stroke="var(--ink2)" stroke-width="1.2"/></svg>切球桿</span>`;
    const draw = () => { s1.replaceChildren(); s2.replaceChildren(); grid.replaceChildren(); h("span", "", s1, "一次看");
      sizes.forEach(n => { const b = h("button", "fchip" + (n === per ? " on" : ""), s1, n >= HS.length ? "全部" : n + " 洞"); b.addEventListener("click", () => { per = n; start = 0; draw(); }); });
      if (strip) { h("span", "", s2, "↔ 左右滑動看下一洞"); grid.className = "maps strip"; grid.style.setProperty("--per", per);
        const hpx = per === 1 ? 500 : per === 2 ? 400 : 300; HS.forEach(H => grid.appendChild(holeMap(r, H, hpx, per === 3))); grid.scrollLeft = 0; return; }
      if (per < HS.length) for (let i = 0; i < HS.length; i += per) { const b = h("button", "fchip" + (i === start ? " on" : ""), s2, `${HS[i].n}–${HS[Math.min(i + per, HS.length) - 1].n}`); b.addEventListener("click", () => { start = i; draw(); }); }
      const show = HS.slice(start, start + per), cols = per;
      grid.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`; const hpx = per === 2 ? 560 : 500;
      show.forEach(H => grid.appendChild(holeMap(r, H, hpx, false))); };
    draw(); };
  // 點開一場：計分卡在上，下面一排按鈕選要看的細節
  const detail = r => { const box = document.createElement("div"); h("div", "scname", box, r.course); box.appendChild(card(r));
    const tabs = h("div", "tabs", box), panel = h("div", "panel", box); let open = null;
    const items = [["逐洞擊球明細", p => p.appendChild(shotsTable(r))]];
    if (r.gps) { items.push(["一號木開球", p => drivePanel(r, p)]); items.push(["球道圖", p => mapPanel(r, p)]); }
    items.forEach(([label, fill]) => { const b = h("button", "fchip", tabs, label);
      b.addEventListener("click", () => { const was = open === label; open = was ? null : label; panel.replaceChildren(); [...tabs.children].forEach(c => c.classList.toggle("on", !was && c === b)); if (!was) fill(panel); }); });
    return box; };
  const shotsTable = r => { const box = document.createElement("div"), tb = h("table", "rt", box), hh = h("tr", "", tb);
    ["洞", "標準桿", "洞長", "擊球", "推桿", "桿數", "±"].forEach(x => h("th", "", hh, x));
    r.holes.forEach(([hole, par, yd, shots, putts, est, st]) => { const tr = h("tr", "", tb);
      h("td", "", tr, hole); h("td", "", tr, par); h("td", "", tr, yd || ""); const td = h("td", "shots", tr);
      shots.forEach(([c, dd]) => { const b = h("span", "chip", td); h("b", "", b, c); h("span", "", b, " " + dd); });
      h("td", "", tr, putts == null ? "" : putts); h("td", "", tr, st);
      const dv = st - par; h("td", dv >= 3 ? "bad" : dv <= 0 ? "good" : "", tr, dv > 0 ? "+" + dv : dv === 0 ? "E" : dv); });
    return box; };
  const draw = () => { t.replaceChildren(); const hr = h("tr", "", t);
    ["日期", "球場", "總桿", "差點", "爆桿", "推桿", ""].forEach(x => h("th", "", hr, x));
    const rows = sec.data.filter(r => on[r.cat]);
    rows.slice(0, all ? rows.length : SHOW).forEach(r => { const tr = h("tr", "row", t);
      h("td", "", tr, NARROW() ? r.date.slice(2).replace(/-/g, "/") : r.date); const c = h("td", "course", tr, r.short); if (r.tag) h("span", "tag", c, r.tag); if (r.combo) h("span", "tag", c, r.combo); if (r.n !== 18) h("span", "tag", c, r.n + " 洞");   // 18 洞是預設，不是 18 洞才標出來
      h("td", "strong", tr, r.strokes); h("td", "", tr, r.hcp == null ? "" : r.hcp.toFixed(1)); const blow = r.holes.filter(x => x[6] - x[1] >= 3).length;   // 爆洞：+3 以上的洞數，0 用綠色
      h("td", blow ? "" : "zero", tr, blow); h("td", "", tr, r.putts == null ? "" : r.putts);
      const arrow = h("td", "arrow", tr, "▸");
      tr.addEventListener("click", () => { const nx = tr.nextSibling;
        if (nx && nx.classList.contains("detail")) { nx.remove(); arrow.textContent = "▸"; return; }
        t.querySelectorAll("tr.detail").forEach(d => d.remove()); t.querySelectorAll("td.arrow").forEach(a => a.textContent = "▸");   // 一次只展開一場
        const top0 = tr.getBoundingClientRect().top;
        const dr = document.createElement("tr"); dr.className = "detail"; const td = h("td", "", dr); td.colSpan = 7; td.appendChild(detail(r));
        tr.after(dr); arrow.textContent = "▾"; scrollBy(0, tr.getBoundingClientRect().top - top0); }); });
    more.style.display = rows.length > SHOW ? "" : "none"; more.textContent = all ? `只顯示最近 ${SHOW} 場` : `顯示全部 ${rows.length} 場`; };
  cats.forEach(([key, label]) => { const n = sec.data.filter(r => r.cat === key).length, b = h("button", "fchip" + (on[key] ? " on" : ""), bar, `${label} ${n}`);
    b.addEventListener("click", () => { on[key] = !on[key]; b.classList.toggle("on", on[key]); draw(); }); });
  more.addEventListener("click", () => { all = !all; draw(); });
  draw();
};

document.getElementById("meta").textContent = REPORT.meta;
document.getElementById("h1").textContent = REPORT.title; document.title = REPORT.title;
(REPORT.tabs || []).forEach(([href, label]) => { const a = h("a", href === REPORT.page ? "on" : "", document.getElementById("ptabs"), label); a.href = href; });
{ const a = document.getElementById("link"); a.textContent = REPORT.link[1]; a.href = REPORT.link[0]; }
const main = document.getElementById("main"), nav = document.getElementById("nav");
function render() {
  main.replaceChildren(); nav.replaceChildren();
  nav.style.display = REPORT.sections.length > 1 ? "" : "none";
  for (const sec of REPORT.sections) {
    if (REPORT.sections.length > 1) { const a = h("a", "", nav, sec.short || sec.title); a.href = "#" + sec.id; }
    const s = h("section", "", main); s.id = sec.id;
    h("h2", "", s, sec.title);
    const host = h("div", "chart", s);
    DRAW[sec.kind](sec, host);
    if (host.classList.contains("scroll")) { const hint = h("div", "hint", null, "↔ 可左右滑動"); host.before(hint); }
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

NAV_SHORT = {"rounds": "場次總表", "score-trend": "總桿", "handicap": "差點", "club-distance": "擊球距離", "score-par": "洞型", "putt-count": "推桿", "courses": "打過的球場",
             "putt-holes": "每月推桿", "club-recent": "球桿比較", "seq-cost": "失誤代價", "mishit-trend": "打壞比例", "mishit-club": "各桿打壞", "seq-club": "各桿成敗", "seq-chain": "連續失誤",
             "chip": "切球", "chip-once": "切一次上果嶺", "score-par-dist": "洞型分布", "length-par3": "三桿洞洞長", "length-par4": "四桿洞洞長", "length-par5": "五桿洞洞長", "blowup": "爆桿",
             "putt-trend": "推桿趨勢", "putt-share": "推桿佔比", "putt-par": "洞型推桿", "putt-approach": "上果嶺距離", "putt-course": "球場推桿"}

def main():
    tmp = os.path.join(tempfile.mkdtemp(), "g.db"); shutil.copyfile(DB, tmp)   # 掛載資料夾上直接開 SQLite 會出錯
    con = sqlite3.connect(tmp)
    n, first, last = con.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM rounds").fetchone()
    shots = con.execute("SELECT COUNT(*) FROM shots").fetchone()[0]
    meta = f"{n} 場（{first} 至 {last}）・{shots} 桿擊球紀錄・產生時間 {datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))):%Y-%m-%d %H:%M}"
    pages = [("index.html", "MI 高爾夫數據", MAIN, ["more.html", "更多分析（其餘 %d 張圖）→" % len(MORE)]),
             ("more.html", "MI 高爾夫數據", MORE, ["index.html", "← 回主頁"]),
             ("courses.html", "MI 高爾夫數據", [section_courses], ["index.html", "← 回主頁"])]
    tabs = [["index.html", "主畫面"], ["courses.html", "打過的球場"], ["more.html", "更多分析"]]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    for fname, title, sections, link in pages:
        secs = [f(con) for f in sections]
        for x in secs: x["short"] = NAV_SHORT.get(x["id"], x["title"])          # 導覽列只放簡稱，不放說明
        report = {"meta": meta, "title": title, "link": link, "success": SUCCESS, "sections": secs, "tabs": tabs, "page": fname, "geo": GEO if any(x["kind"] in ("roundlist", "courses") for x in secs) else {}}
        path = os.path.join(os.path.dirname(OUT), fname)
        open(path, "w", encoding="utf-8").write(PAGE.replace("__DATA__", json.dumps(report, ensure_ascii=False)))
        print("已產生", path, "|", len(sections), "個區塊")
    print(meta)

if __name__ == "__main__":
    main()
