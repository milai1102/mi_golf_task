"""從「你自己的」資料產生個人設定：data/config.json 和 data/club_roles.csv。

要先匯入資料（python3 scripts/ingest_export.py … 然後 python3 scripts/import_garmin.py）。
用法：python3 scripts/setup_config.py            # 還沒有設定檔時產生
      python3 scripts/setup_config.py --force    # 重新產生（會覆寫你手動改過的設定）
產生的是「猜測」：程式依 Garmin 的球桿類型和你實際的用法去猜哪一支是一號木、主力球道木／鐵木桿、切球桿、沙坑桿。
請把印出來的結果給使用者確認；不對就直接改 data/config.json（欄位說明見 docs/SETUP.md）。
"""
import csv, glob, json, os, re, statistics, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG, ROLES = os.path.join(ROOT, "data/config.json"), os.path.join(ROOT, "data/club_roles.csv")

def abbr_of(name, typ):
    t = typ or name
    if t == "Driver": return "1W"
    m = re.match(r"(\d+) Wood", t)
    if m: return m.group(1) + "W"
    m = re.match(r"(\d+) Hybrid", t)
    if m: return m.group(1) + "H"
    m = re.match(r"(\d+) Iron", t)
    if m: return m.group(1) + "i"
    return {"Pitching Wedge": "P", "Gap Wedge": "A", "Sand Wedge": "S", "Lob Wedge": "L", "Putter": "PT"}.get(t, name[:3])

def main(force):
    if os.path.exists(CFG) and not force:
        sys.exit("data/config.json 已經存在，沒有變更。要重新產生請加 --force（會覆寫手動修改）。")
    types = {}                                                # 球桿顯示名稱 → Garmin 的類型（Driver、7 Iron…）
    for p in sorted(glob.glob(os.path.join(ROOT, "data/raw/parts/clubs_*.txt"))):
        for row in open(p, encoding="utf-8").read().strip().split(";"):
            c = row.split("|")
            if len(c) > 4: types[c[2] or c[4]] = c[4]
    shots = []
    for p in glob.glob(os.path.join(ROOT, "data/rounds/*.csv")):
        for r in csv.DictReader(open(p, encoding="utf-8-sig")):
            if r["club"] and r["shot_type"] != "PUTT" and types.get(r["club"]) != "Putter": shots.append(r)
    if not shots: sys.exit("錯誤：data/rounds/ 裡沒有擊球紀錄。請先匯入資料（docs/SETUP.md 第 3、4 步）。")
    by = {}
    for r in shots: by.setdefault(r["club"], []).append(r)
    n = lambda c: len(by.get(c, []))
    med = lambda c: statistics.median([float(r["distance_m"]) for r in by[c] if r["distance_m"]] or [0])
    is_wood = lambda c: bool(re.search(r"Wood|Hybrid|Driver", types.get(c, "")))
    is_wedge = lambda c: types.get(c, "") in ("Gap Wedge", "Sand Wedge", "Lob Wedge")
    pick = lambda cands: max(cands, key=n) if cands else ""
    driver = pick([c for c in by if types.get(c) == "Driver"]) or pick([c for c in by if any(r["shot_type"] == "TEE" for r in by[c]) and med(c) > 130])
    fairway = pick([c for c in by if is_wood(c) and c != driver]) or pick([c for c in by if c != driver and not is_wedge(c)])
    short = [r for r in shots if r["to_pin_start_m"] and float(r["to_pin_start_m"]) <= 55 and r["start_lie"] not in ("Green", "Bunker")]
    cnt = lambda rows: {c: sum(1 for r in rows if r["club"] == c) for c in {r["club"] for r in rows}}
    cs = cnt(short); chip = max(cs, key=cs.get) if cs else pick([c for c in by if is_wedge(c)])
    bs = cnt([r for r in shots if r["start_lie"] == "Bunker"]); sand = max(bs, key=bs.get) if bs else chip
    order = sorted(by, key=lambda c: -med(c))
    bag = [c for c in order if n(c) >= 10 or c in (driver, fairway, chip, sand)]
    roles = {c: ("chip" if (is_wedge(c) or c in (chip, sand)) and c not in (driver, fairway) else "full") for c in order}
    full = [c for c in bag if roles[c] == "full"]
    cfg = {"site_title": "我的高爾夫數據", "clubs": {"driver": driver, "fairway": fairway, "chip": chip, "sand": sand},
           "woods": [c for c in order if is_wood(c)], "bag": bag, "full_clubs": full,
           "abbr": {**{c: abbr_of(c, types.get(c, "")) for c in order}, "Putter": "PT"}, "short_courses": [], "plan": False}
    json.dump(cfg, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open(ROLES, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["club", "role"]); w.writerows([c, roles[c]] for c in order)
    print("已寫入 data/config.json 與 data/club_roles.csv。請向使用者確認下面的猜測：")
    print(f"  一號木（開球用）      driver  = {driver or '（找不到）'}（{n(driver)} 桿）")
    print(f"  主力球道木／鐵木桿    fairway = {fairway or '（找不到）'}（{n(fairway)} 桿）")
    print(f"  切球桿（55 碼內最常用）chip    = {chip or '（找不到）'}（{n(chip)} 桿）")
    print(f"  沙坑桿                sand    = {sand or '（找不到）'}（{n(sand)} 桿）")
    print("  球袋（桿弟頁會建議的球桿，由遠到近）:", "、".join(f"{c}[{cfg['abbr'][c]}]" for c in bag))
    print("不對的話直接改 data/config.json，再執行 python3 scripts/update.py。")

if __name__ == "__main__":
    main("--force" in sys.argv)
