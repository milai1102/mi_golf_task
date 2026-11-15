"""列出 data/rounds 已匯入的 Garmin 計分卡編號（給 garmin_fetch_new.js 的 KNOWN 用）。"""
import csv, glob, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ids = set()
for p in glob.glob(os.path.join(ROOT, "data/rounds", "*.csv")):
    ids |= {r["garmin_scorecard_id"] for r in csv.DictReader(open(p, encoding="utf-8-sig"))}
print(",".join(sorted(ids)))
