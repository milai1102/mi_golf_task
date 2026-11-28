"""一次跑完整條流程：raw → rounds → 估算推桿 →（第一次：產生個人設定）→ 資料庫 → 報表。
用法：python3 scripts/update.py
"""
import glob, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
if not glob.glob(os.path.join(ROOT, "data/raw/parts/rounds_*.txt")):
    sys.exit("還沒有資料：data/raw/parts/ 是空的。請先照 docs/SETUP.md 第 3 步下載 Garmin 資料並執行 python3 scripts/ingest_export.py <檔案>。")
steps = ["import_garmin.py", "site_round.py", "estimate_putts.py"]
if not os.path.exists(os.path.join(ROOT, "data/config.json")): steps.append("setup_config.py")      # 新使用者第一次：從資料猜球桿設定
steps += ["build_db.py", "build_report.py"]
for step in steps:
    print(f"── {step}")
    subprocess.run([sys.executable, os.path.join(HERE, step)], check=True)
print("完成。打開 reports/index.html 看結果；python3 scripts/check_setup.py 可以檢查還缺什麼。")
