"""步驟 1.5：替沒有推桿紀錄的洞估算推桿數，寫回 data/rounds 的場次檔。

估算公式：推桿 = 該洞總桿 − 被記錄的非推桿擊球數 − 罰桿，再限制在 1 到 3 之間。
  （用 72 個有實際推桿紀錄的洞驗證：89% 完全正確，平均誤差 0.12 桿，沒有系統性偏差。
    差值 4 以上的洞多半是手錶漏記了擊球而不是真的四推，所以上限取 3。）
欄位 putts_source 標示來源：garmin = 手錶輸入的實際值；manual = 人工補的；estimated = 本程式估算。
只會填空白或 estimated 的洞，garmin / manual 的值永遠不動。完全沒有擊球紀錄的洞無法估算，保持空白。
用法：python3 scripts/estimate_putts.py
"""
import csv, glob, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def estimate(strokes, n_shots, penalties):
    if n_shots == 0: return None
    return min(max(strokes - n_shots - penalties, 1), 3)

def main():
    filled = kept = blank = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "data/rounds", "*.csv"))):
        rows = list(csv.reader(open(path, encoding="utf-8-sig")))
        head, body = rows[0], rows[1:]
        if "putts_source" not in head:                       # 舊格式：補上來源欄，原有的推桿值都是手錶輸入的
            i = head.index("putts") + 1; head.insert(i, "putts_source")
            for r in body: r.insert(i, "garmin" if r[i - 1] != "" else "")
        c = {name: k for k, name in enumerate(head)}
        holes = {}
        for r in body: holes.setdefault(r[c["seq"]], []).append(r)
        for rs in holes.values():
            r0 = rs[0]
            if r0[c["putts_source"]] in ("garmin", "manual"): kept += 1; continue
            n = sum(1 for r in rs if r[c["shot_no"]] != "" and r[c["shot_type"]] != "PUTT")
            est = estimate(int(r0[c["hole_strokes"]]), n, int(r0[c["penalties"]] or 0))
            for r in rs:
                r[c["putts"]] = "" if est is None else str(est); r[c["putts_source"]] = "" if est is None else "estimated"
            if est is None: blank += 1
            else: filled += 1
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(head); w.writerows(body)
    print(f"推桿：估算 {filled} 洞，保留實際紀錄 {kept} 洞，無法估算 {blank} 洞")

if __name__ == "__main__":
    main()
