"""一次跑完整條流程：raw → rounds → 估算推桿 → 資料庫 → 報表。
用法：python3 scripts/update.py
"""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
for step in ("import_garmin.py", "estimate_putts.py", "build_db.py", "build_report.py"):
    print(f"── {step}")
    subprocess.run([sys.executable, os.path.join(HERE, step)], check=True)
