"""步驟 1：Garmin 原始資料 (data/raw) → 每場一個檔 (data/rounds/日期_球場名.csv)。

規則：排除模擬場次；同一天同球場的多張計分卡合併成一場。
已存在的場次檔不會被覆寫（那是核心資料，可能有人工修正）；要重產請加 --force。
同日同球場後來才出現的計分卡（例如後九）會自動併入既有場次檔，既有的列原樣保留。
用法：python3 scripts/import_garmin.py [--force]
"""
import csv, glob, os, sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, ROUNDS = os.path.join(ROOT, "data/raw"), os.path.join(ROOT, "data/rounds")
TYPE = {"T": "TEE", "A": "APPROACH", "C": "CHIP", "L": "LAYUP", "P": "PUTT", "R": "RECOVERY", "U": "UNKNOWN"}
LIE = {"T": "TeeBox", "F": "Fairway", "R": "Rough", "B": "Bunker", "G": "Green", "W": "Waste", "U": "Unknown", "": ""}
FW = {"H": "HIT", "L": "LEFT", "R": "RIGHT", "": ""}
HEADER = ["date", "course", "layout", "tee", "seq", "hole", "par", "hole_strokes", "putts", "putts_source", "penalties", "fairway",
          "hole_length_m", "shot_no", "club", "shot_type", "start_lie", "end_lie", "distance_m", "to_pin_start_m",
          "to_pin_end_m", "garmin_scorecard_id"]

def load(name, sep):
    rows = []
    for p in sorted(glob.glob(os.path.join(RAW, "parts", name + "_*.txt"))):
        rows += [r.split(sep) for r in open(p, encoding="utf-8").read().strip().split(";")]
    return rows

def main(force):
    cards = {int(c[0]): c for c in load("rounds", "|")}
    zh = {r["garmin_name"]: r["name_zh"] for r in csv.DictReader(open(os.path.join(RAW, "course_names.csv"), encoding="utf-8"))}
    clubs = {}
    for c in load("clubs", "|"):                      # 第一批用流水號、之後用 Garmin 球桿編號，兩種都認
        clubs[c[0]] = clubs[c[1]] = c[2] or c[4]
    clubs["99"] = "已刪除球桿"; clubs[""] = ""
    holes = defaultdict(list); shots = defaultdict(list)
    for h in load("holes", ","):
        if h[3] != "": holes[int(h[0])].append(h)          # 只留有成績的洞
    for s in load("shots", ","):
        shots[(int(s[0]), int(s[1]))].append(s)
    club_of = lambda course: course.split(" ~ ")[0].split("+")[0].strip()
    groups = defaultdict(list)
    for sid, c in sorted(cards.items()):
        if c[6] == "SIMULATION": continue
        groups[(c[2][:10], club_of(c[4]))].append(sid)
    for g in groups.values(): g.sort(key=lambda sid: cards[sid][2])
    os.makedirs(ROUNDS, exist_ok=True)
    made = skipped = merged = 0
    for (date, club), sids in sorted(groups.items()):
        name = zh.get(club, club)
        path = os.path.join(ROUNDS, f"{date}_{name.replace(' ', '_').replace('&', 'and')}.csv")
        keep = {}                                         # 既有檔案的列（保留人工修正），依計分卡編號分組
        head, sidx = HEADER, len(HEADER) - 1
        if os.path.exists(path) and not force:
            for r in csv.reader(open(path, encoding="utf-8-sig")):                # 場次檔可能有人工加的欄位（手記的 strokes_to_green、first_putt_steps），依欄名找計分卡編號並保留原表頭
                if r and r[0] in ("date", "\ufeffdate"): head = r; sidx = r.index("garmin_scorecard_id")
                elif r: keep.setdefault(r[sidx], []).append(r)
            if all(cards[sid][1] in keep for sid in sids):
                skipped += 1; continue                    # 沒有新的計分卡 → 不動
            merged += 1                                   # 同日同球場來了新計分卡（例如後九）→ 自動併入
        rows, seq = [], 0
        for sid in sids:                                  # 依開球時間排序，整場重新連續編 seq
            c = cards[sid]
            if c[1] in keep:
                last = None
                for r in keep[c[1]]:
                    if r[5] != last: seq += 1; last = r[5]
                    rows.append(r[:4] + [seq] + r[5:])
                continue
            for h in holes[sid]:
                seq += 1
                base = [date, name, c[4], c[9], seq, h[1], h[2], h[3], h[4], "garmin" if h[4] != "" else "", h[5], FW[h[6]], h[7]]
                ss = shots.get((sid, int(h[1])), [])
                if not ss:
                    rows.append(base + [""] * 8 + [c[1]])
                for s in ss:
                    rows.append(base + [s[2], clubs.get(s[3], "未知球桿"), TYPE[s[4]], LIE[s[5]], LIE[s[6]], s[7], s[8], s[9], c[1]])
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(head); w.writerows(r + [""] * (len(head) - len(r)) for r in rows)
        made += 1
    print(f"場次檔：寫入 {made}（其中併入新計分卡 {merged}），已存在略過 {skipped}")

if __name__ == "__main__":
    main("--force" in sys.argv)
