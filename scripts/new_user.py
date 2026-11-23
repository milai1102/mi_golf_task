"""新使用者第一步：把原作者（milai1102）的個人資料清掉，換成空白的起點。只需要執行一次。

用法：python3 scripts/new_user.py --yes
會做的事：
  1. 刪除原作者的資料：data/raw/parts、data/raw/gps、data/rounds、data/course_preview、data/processed、
     data/config.json、data/club_roles.csv、data/course_ratings.csv、reports/*.html
  2. 保留可以共用的東西：data/course_geo（球洞輪廓）、data/raw/course_names.csv（球場中文名）、data/layout_names.csv、全部程式
  3. 把原作者的工作紀錄 CLAUDE.md 搬到 docs/original_author_CLAUDE.md，換上給新使用者的簡短版（docs/templates/CLAUDE.new_user.md）
  4. 建立標記檔 data/.my_data，之後再執行會拒絕（避免誤刪你自己的資料）
做完之後照 docs/SETUP.md 第 3 步繼續。
"""
import glob, os, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
J = lambda *p: os.path.join(ROOT, *p)

def main(argv):
    if os.path.exists(J("data/.my_data")):
        sys.exit("已經初始化過了（data/.my_data 存在），沒有刪除任何東西。這個資料夾裡是你自己的資料。")
    if "--yes" not in argv:
        sys.exit(__doc__ + "\n沒有執行：確定要清掉原作者的資料，請加上 --yes。")
    if not os.path.exists(J("docs/templates/CLAUDE.new_user.md")): sys.exit("錯誤：找不到 docs/templates/CLAUDE.new_user.md，專案不完整，請重新下載。沒有刪除任何東西。")
    removed = 0
    for pat in ("data/raw/parts/*", "data/raw/gps/*", "data/rounds/*", "data/course_preview/*", "reports/*.html", "*.png"):
        for p in glob.glob(J(pat)):
            if os.path.isfile(p): os.remove(p); removed += 1
    for p in ("data/config.json", "data/club_roles.csv", "data/course_ratings.csv"):
        if os.path.exists(J(p)): os.remove(J(p)); removed += 1
    shutil.rmtree(J("data/processed"), ignore_errors=True)
    for d in ("data/raw/parts", "data/raw/gps", "data/rounds", "reports"):
        os.makedirs(J(d), exist_ok=True); open(J(d, ".gitkeep"), "w").close()
    if os.path.exists(J("CLAUDE.md")) and not os.path.exists(J("docs/original_author_CLAUDE.md")):
        shutil.move(J("CLAUDE.md"), J("docs/original_author_CLAUDE.md"))
    shutil.copyfile(J("docs/templates/CLAUDE.new_user.md"), J("CLAUDE.md"))
    open(J("reports/index.html"), "w", encoding="utf-8").write("<!doctype html><meta charset='utf-8'><p>還沒有資料。請照 docs/SETUP.md 匯入你的 Garmin 資料。</p>")
    open(J("data/.my_data"), "w", encoding="utf-8").write("這個資料夾已經初始化成新使用者的資料（scripts/new_user.py）。\n")
    print(f"完成：刪除 {removed} 個原作者的檔案；CLAUDE.md 已換成新使用者版本（原本的在 docs/original_author_CLAUDE.md）。")
    print("下一步：docs/SETUP.md 第 3 步（下載你的 Garmin 資料）。隨時可以執行 python3 scripts/check_setup.py 看進度。")

if __name__ == "__main__":
    main(sys.argv[1:])
