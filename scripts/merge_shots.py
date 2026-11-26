"""把兩個來源的擊球合併成一份：手錶（Garmin，data/rounds + data/raw/gps）和網站的桿弟頁（data/raw/caddie）。

兩份原始資料分開放、不互相覆寫；每次重建資料庫和報表時才在這裡合併，所以改了合併規則，舊場次也會跟著重算。

規則
- 每洞的成績（ON、推桿、第一推步數、罰桿）：場次檔有人工修正（putts_source = manual）就用場次檔，否則用網站記的。
- 每一桿：同一洞裡，手錶和網站的擊球依先後順序一對一配對。配對條件：起點相距 MATCH_M 公尺內，
  而且手錶的擊球時間在網站按下去之前 DT_BEFORE 秒到之後 DT_AFTER 秒之間（通常是先在網站按、再揮桿）。
  配到的＝兩邊都有（both）：位置、距離用手錶的，球桿用網站的（打之前自己選的）；兩邊球桿不同時記成 conflict。
  沒配到的手錶擊球＝garmin；沒配到的網站擊球＝site（手錶漏記），起點用網站記下的 GPS。
- 合併後每一桿的落點＝下一桿的起點；最後一桿的落點＝旗桿（推桿前那一桿）。所以手錶漏記一桿時，
  前一桿原本被 Garmin 拉到旗桿的落點，會改成漏記那一桿的起點。
"""
import csv, glob, json, math, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K = 180 / 2 ** 31
MATCH_M, DT_BEFORE, DT_AFTER = 25, 90, 900

def dist_m(a, b):
    return math.hypot((a[0] - b[0]) * 111320, (a[1] - b[1]) * 111320 * math.cos(math.radians(a[0])))

_CADDIE = None
def caddie_files():
    """{(日期, 球場簡稱): 內容}；data/raw/caddie/YYYY-MM-DD_簡稱.json"""
    global _CADDIE
    if _CADDIE is None:
        _CADDIE = {}
        for p in sorted(glob.glob(os.path.join(ROOT, "data/raw/caddie", "*.json"))):
            d = json.load(open(p, encoding="utf-8")); _CADDIE[(d["date"], d["course_short"])] = d
    return _CADDIE

def garmin_gps(sid):
    """一張計分卡的座標：{(球場代碼, 洞號): {"pin": (lat, lon), "shots": [{"no", "club", "s": (lat, lon), "e": (lat, lon), "t": 秒}]}}"""
    p = os.path.join(ROOT, "data/raw/gps", f"{sid}.txt"); out = {}
    if not os.path.exists(p): return out
    cid = key = None
    for line in open(p, encoding="utf-8"):
        c = line.rstrip("\n").split("|")
        if c[0] == "C": cid = c[1]
        elif c[0].startswith("H"):
            hc, hn = cid, int(c[0][1:]); mp = c[3].split("/") if len(c) > 3 and c[3] else []
            if len(mp) >= 2 and mp[0].startswith("gid") and mp[1].startswith("hole"): hc, hn = mp[0][3:], int(mp[1][4:])
            q = c[2].split(","); key = (hc, hn, int(c[0][1:]))
            out[key] = {"pin": (int(q[0]) * K, int(q[1]) * K) if q[0] else None, "pin_px": (int(q[2]), int(q[3])) if len(q) > 3 and q[2] else None, "shots": []}
        elif c[0] and key:
            s, e = c[4].split(","), c[5].split(",")
            out[key]["shots"].append({"no": int(c[0]), "club": c[1], "s": (int(s[0]) * K, int(s[1]) * K) if s[0] else None,
                                      "e": (int(e[0]) * K, int(e[1]) * K) if e[0] else None, "sp": (int(s[2]), int(s[3])) if len(s) > 3 and s[2] else None,
                                      "ep": (int(e[2]), int(e[3])) if len(e) > 3 and e[2] else None, "elie": e[4] if len(e) > 4 else None, "t": int(c[6]) / 1000 if len(c) > 6 and c[6] else None})
    return out

def _pair(G, S):
    """依先後順序一對一配對：先求配到最多桿，同樣多時選時間差總和最小的（手錶的擊球時間最接近「按下去之後」的那一次）。回傳 [(i, j)]。"""
    def cost(g, s):
        if not (g["s"] and s["s"]) or dist_m(g["s"], s["s"]) > MATCH_M: return None
        if g["t"] is None or not s["t"]: return 0.5
        dt = g["t"] - s["t"]
        return None if not (-DT_BEFORE <= dt <= DT_AFTER) else (abs(dt) / DT_AFTER) * 0.9
    n, m = len(G), len(S); INF = (-1, 0)
    best = [[(0, 0.0)] * (m + 1) for _ in range(n + 1)]; how = [[0] * (m + 1) for _ in range(n + 1)]
    key = lambda v: (v[0], -v[1])
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            c = cost(G[i], S[j]); opts = [(best[i + 1][j], 1), (best[i][j + 1], 2)]
            if c is not None: opts.append(((best[i + 1][j + 1][0] + 1, best[i + 1][j + 1][1] + c), 3))
            best[i][j], how[i][j] = max(opts, key=lambda o: key(o[0]))
    out, i, j = [], 0, 0
    while i < n and j < m:
        if how[i][j] == 3: out.append((i, j)); i += 1; j += 1
        elif how[i][j] == 1: i += 1
        else: j += 1
    return out

def merge_round(fname, rows=None):
    """回傳 {seq: {"shots": [...], "hole": {...}}}。shots 每一桿：
    club, src (garmin / site / both), conflict（兩邊球桿不同時是手錶的球桿）, row（場次檔那一列，手錶的才有）,
    s / e（起點、落點經緯度）, end_is_pin, lie（網站記的球位）, pen, t。
    hole：on, putts, steps, pen, src（網站的成績有沒有被採用）。"""
    rows = rows or list(csv.DictReader(open(os.path.join(ROOT, "data/rounds", fname), encoding="utf-8-sig")))
    date = rows[0]["date"]
    short = SHORT.get(rows[0]["course"], rows[0]["course"])
    cad = caddie_files().get((date, short), {}).get("holes", {})
    gps = {}
    for sid in dict.fromkeys(r["garmin_scorecard_id"] for r in rows):
        for (cid, h, n_), v in garmin_gps(sid).items(): gps[(sid, n_)] = (cid, h, v)
    by_seq = {}
    for r in rows: by_seq.setdefault(int(r["seq"]), []).append(r)
    out = {}
    for seq, rs in sorted(by_seq.items()):
        r0 = rs[0]; srows = [r for r in rs if r["shot_no"] and r["shot_type"] != "PUTT"]
        cid, h, g = gps.get((r0["garmin_scorecard_id"], int(r0["hole"])), (None, None, {"pin": None, "shots": []}))
        G = []
        gmap = {s["no"]: s for s in g["shots"]}
        for r in srows:
            gs = gmap.get(int(r["shot_no"]), {}); G.append({"row": r, "club": r["club"], "s": gs.get("s"), "e": gs.get("e"), "t": gs.get("t"), "sp": gs.get("sp"), "ep": gs.get("ep"), "elie": gs.get("elie")})
        ch = cad.get(f"{cid}_{h}", {}) if cid else {}
        S = [{"club": s.get("club"), "s": (s["lat"], s["lon"]) if s.get("lat") is not None else None, "t": s["t"] / 1000, "lie": s.get("lie"), "pen": s.get("pen") or 0,
              "ins": s.get("ins"), "auto": s.get("auto")} for s in ch.get("shots", [])]
        pairs = _pair(G, S); pi = dict(pairs); pj = {j: i for i, j in pairs}
        merged = []
        i = j = 0
        while i < len(G) or j < len(S):                     # 依先後排：配對的一起出來，沒配到的照各自的順序插進去
            if i < len(G) and i in pi and j < len(S) and pi[i] == j:
                a, b = G[i], S[j]
                merged.append({"club": b["club"] or a["club"], "src": "both", "conflict": a["club"] if b["club"] and b["club"] != a["club"] else None,
                               "row": a["row"], "s": a["s"] or b["s"], "e": a["e"], "lie": b["lie"], "pen": b["pen"], "t": a["t"] or b["t"], "sp": a["sp"], "ep": a["ep"], "elie": a["elie"]}); i += 1; j += 1
            elif j < len(S) and j not in pj and (i >= len(G) or (i in pi and pi[i] > j) or (S[j]["t"] and G[i]["t"] and S[j]["t"] < G[i]["t"] - DT_BEFORE)):
                b = S[j]; merged.append({"club": b["club"], "src": "site", "conflict": None, "row": None, "s": b["s"], "e": None, "lie": b["lie"], "pen": b["pen"], "t": b["t"], "sp": None, "ep": None, "elie": None}); j += 1
            elif i < len(G) and i not in pi:
                a = G[i]; merged.append({"club": a["club"], "src": "garmin", "conflict": None, "row": a["row"], "s": a["s"], "e": a["e"], "lie": None, "pen": 0, "t": a["t"], "sp": a["sp"], "ep": a["ep"], "elie": a["elie"]}); i += 1
            else:                                            # 剩下的網站擊球
                b = S[j]; merged.append({"club": b["club"], "src": "site", "conflict": None, "row": None, "s": b["s"], "e": None, "lie": b["lie"], "pen": b["pen"], "t": b["t"], "sp": None, "ep": None, "elie": None}); j += 1
        pin = g["pin"]
        for k, m in enumerate(merged):
            nxt = merged[k + 1]["s"] if k + 1 < len(merged) else None
            m["end_is_pin"] = nxt is None
            if nxt: m["e"] = nxt
            else: m["e"] = pin or m["e"]
            m["dist_m"] = round(dist_m(m["s"], m["e"])) if m["s"] and m["e"] else (int(float(m["row"]["distance_m"])) if m["row"] and m["row"]["distance_m"] else None)
            m["pin_s"] = round(dist_m(m["s"], pin)) if m["s"] and pin else None
            m["pin_e"] = round(dist_m(m["e"], pin)) if m["e"] and pin else None
        for k, m in enumerate(merged):                       # 使用者確認過的球桿（data/shot_fixes.csv）
            fx = FIXES.get((fname, seq, k + 1))
            if fx: m["club"] = fx; m["conflict"] = None; m["fixed"] = True
        hole = {"on": ch.get("on"), "putts": ch.get("putts"), "steps": ch.get("steps"), "cid": cid, "h": h, "pin": pin, "pin_px": g.get("pin_px")}
        out[seq] = {"shots": merged, "hole": hole}
    return out

_fp = os.path.join(ROOT, "data/shot_fixes.csv")
FIXES = {(r["file"], int(r["seq"]), int(r["shot"])): r["club"] for r in csv.DictReader(open(_fp, encoding="utf-8"))} if os.path.exists(_fp) else {}

SHORT = {r["name_zh"]: r.get("short") or r["name_zh"] for r in csv.DictReader(open(os.path.join(ROOT, "data/raw/course_names.csv"), encoding="utf-8"))}

if __name__ == "__main__":                                   # 檢查用：python3 scripts/merge_shots.py 2026-11-26_南寶高爾夫鄉村俱樂部.csv
    import sys
    for fn in sys.argv[1:]:
        M = merge_round(os.path.basename(fn))
        for seq, v in M.items():
            print(seq, " ".join(f"{s['club']}[{s['src'][0]}{'!' + s['conflict'] if s['conflict'] else ''}]{round(s['dist_m'] * 1.09361) if s['dist_m'] else '?'}" for s in v["shots"]), v["hole"]["on"], v["hole"]["putts"])
