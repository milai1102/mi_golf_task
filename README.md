# mi_golf_task — 個人高爾夫數據統計系統

資料來源：Garmin Fenix 8 → Garmin Connect。目標是跨場次看趨勢，制定系統性的進攻與選桿策略。

## 目錄說明

| 目錄 | 用途 |
|---|---|
| `data/raw/` | Garmin 原始資料，只新增、不修改。 |
| `data/rounds/` | **核心資料**：每場一個 CSV（`日期_球場名.csv`）。修正資料都改這裡。 |
| `data/processed/` | 由 rounds 彙整出的 SQLite 資料庫，可隨時重建。 |
| `scripts/` | 程式碼：匯入、清理（排除模擬、合併前後九）、統計、產生報表。 |
| `reports/` | 產出的報表與圖表（HTML），給人看的成品。 |
| `docs/` | 需求、資料清理規則、指標定義、圖表清單等討論紀錄。 |

## 流程

`data/raw` → `data/rounds`（核心）→ `data/processed/golf.db` → `reports`

完整工作邏輯見 `CLAUDE.md`。
