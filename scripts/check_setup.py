"""檢查安裝進度，並告訴你下一步該做什麼。隨時可以執行，不會修改任何東西。
用法：python3 scripts/check_setup.py
"""
import csv, glob, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
J = lambda *p: os.path.join(ROOT, *p)
n = lambda pat: len(glob.glob(J(pat)))
steps = []; nxt = None
def step(ok, name, detail, todo):
    global nxt
    steps.append(("✅" if ok else "❌", name, detail))
    if not ok and nxt is None: nxt = todo

mine = os.path.exists(J("data/.my_data"))
step(mine, "1 清掉原作者的資料", "data/.my_data 存在" if mine else "這個資料夾裡還是原作者（milai1102）的資料",
     "python3 scripts/new_user.py --yes　（如果你就是原作者，不需要做這一步，也不需要這份檢查）")
step(n("data/raw/parts/rounds_*.txt") > 0, "2 下載並放入 Garmin 資料", f"data/raw/parts 有 {n('data/raw/parts/rounds_*.txt')} 個 rounds 檔、data/raw/gps 有 {n('data/raw/gps/*.txt')} 個座標檔",
     "照 docs/SETUP.md 第 3 步：在 Chrome 的 Console 執行 scripts/garmin_download.js，再執行 python3 scripts/ingest_export.py <下載的檔案>")
step(n("data/rounds/*.csv") > 0, "3 產生場次檔", f"data/rounds 有 {n('data/rounds/*.csv')} 場", "python3 scripts/update.py")
cfg = {}
if os.path.exists(J("data/config.json")):
    try: cfg = json.load(open(J("data/config.json"), encoding="utf-8"))
    except Exception as e: step(False, "4 個人設定", f"data/config.json 讀不出來：{e}", "修正 data/config.json 的 JSON 格式，或刪掉它再執行 python3 scripts/setup_config.py")
if cfg or not os.path.exists(J("data/config.json")):
    clubs = set()
    for p in glob.glob(J("data/rounds/*.csv")):
        clubs |= {r["club"] for r in csv.DictReader(open(p, encoding="utf-8-sig"))}
    c = cfg.get("clubs", {}); miss = [f"{k}={v}" for k, v in c.items() if v and clubs and v not in clubs]
    ok = bool(cfg) and bool(c.get("driver")) and not miss
    step(ok, "4 個人設定", ("driver=%s fairway=%s chip=%s sand=%s" % (c.get("driver"), c.get("fairway"), c.get("chip"), c.get("sand"))) if cfg else "沒有 data/config.json",
         ("data/config.json 裡這些球桿名稱在你的資料裡找不到：" + "、".join(miss) + "。改成資料裡的名稱（data/rounds 的 club 欄），或執行 python3 scripts/setup_config.py --force") if miss else "python3 scripts/setup_config.py")
step(os.path.exists(J("data/processed/golf.db")) and n("reports/*.html") >= 4, "5 產生網站", f"reports 有 {n('reports/*.html')} 個頁面", "python3 scripts/update.py")
geo = {os.path.basename(p)[:-5] for p in glob.glob(J("data/course_geo/*.json"))}; need = set()
for p in glob.glob(J("data/raw/gps/*.txt")):
    for line in open(p, encoding="utf-8"):
        if line.startswith("H"):
            f = line.rstrip("\n").split("|")
            if len(f) > 3 and f[3].startswith("gid"): need.add(f[3].split("/")[0][3:])
lack = sorted(need - geo)
step(not lack, "6 球洞輪廓（選配：球場頁、揮桿圖、桿弟頁才需要）", f"你打過的球場代碼 {len(need)} 個，缺輪廓 {len(lack)} 個" + (f"：{lack[:8]}" if lack else ""),
     "照 docs/SETUP.md 第 5 步：在 birdseye.garmin.com 的分頁執行 scripts/garmin_course_download.js，再 python3 scripts/ingest_export.py course_geo_export.json，然後 python3 scripts/update.py")
for s in steps: print(s[0], s[1], "—", s[2])
print("\n下一步：" + (nxt or "全部完成。之後每打完一場：重做第 3 步（下載）→ ingest_export.py → update.py。要放上網站看 docs/SETUP.md 第 6 步。"))
