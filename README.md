# mi_golf_task — 個人高爾夫數據統計系統

資料來源：Garmin Fenix 8 → Garmin Connect。目標是跨場次看趨勢，制定系統性的進攻與選桿策略。

## 想用你自己的資料？

這是開源專案，歡迎拿去用。下載下來的專案裡是原作者的資料；換成你自己的，**交給你的 AI 助理做就好**（Claude Code、Claude 桌面版，或其他能執行指令、能操作瀏覽器的 AI）。

1. 把這個專案 Fork／下載到你的電腦，用 AI 助理打開這個資料夾。
2. 對它說：**「讀 AGENTS.md 和 docs/SETUP.md，幫我把這個專案換成我自己的 Garmin 資料。」**
3. 它會做這些事，你只需要在瀏覽器**自己登入 Garmin Connect**，並確認它猜的球桿設定：

| | AI 做的事 | 指令或程式 |
|---|---|---|
| 1 | 檢查環境、隨時確認做到哪裡 | `python3 scripts/check_setup.py` |
| 2 | 清掉原作者的資料 | `python3 scripts/new_user.py --yes` |
| 3 | 在你登入的 Garmin Connect 分頁抓你的計分卡、每一桿的座標 | 在頁面執行 `scripts/garmin_download.js` |
| 4 | 放進專案、猜球桿設定、產生網站 | `python3 scripts/ingest_export.py <檔案>`、`python3 scripts/update.py` |
| 5 | 你打過、專案裡還沒有的球場：描出球洞輪廓 | 在頁面執行 `scripts/garmin_course_download.js` |
| 6 | （你想用手機看才做）放上你自己的 GitHub Pages | `git push` |

之後每打完一場，對它說「把新的一場更新進來」，它會重做第 3、4 步。

完整的操作手冊（給 AI 看的，每一步都有「怎麼確認成功」和出錯時怎麼辦）：**[docs/SETUP.md](docs/SETUP.md)**。

需要：Python 3.9 以上（不用安裝套件）、Garmin Connect 帳號（手錶有記錄高爾夫計分卡）。
注意：放上 GitHub Pages 之後，任何人有網址都看得到你的成績和打球的球場。

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

## AI 桿弟

依歷史紀錄建議每一洞怎麼打的實驗功能。設計文件：[docs/ai_caddie.md](docs/ai_caddie.md)；模擬器：<https://milai1102.github.io/mi_golf_task/reports/caddie.html>。
