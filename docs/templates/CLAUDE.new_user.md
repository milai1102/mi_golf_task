# 高爾夫數據統計（你自己的資料）

這個專案把 Garmin 手錶記錄的高爾夫資料，整理成一個可以用手機看的網站：每一場的成績、各球桿的距離、打過的球場、AI 桿弟（依你的歷史紀錄建議每一洞怎麼打）。
原作者是 milai1102（<https://github.com/milai1102/mi_golf_task>）。這個資料夾已經清掉他的資料，裡面是「這位使用者自己的」資料。

**給接手的 AI：先讀 `docs/SETUP.md`，照步驟做，每做完一步就執行 `python3 scripts/check_setup.py` 確認。** 不要自己發明流程，也不要手動編輯 `data/raw/`、`data/processed/`、`reports/` 裡的檔案。

## 一定要遵守

1. 資料只往一個方向流動：`data/raw/`（Garmin 原始）→ `data/rounds/`（每場一個 CSV，核心資料）→ `data/processed/golf.db` → `reports/*.html`。後面三層都由程式產生。
2. 要修正資料（補推桿、改球桿）只改 `data/rounds/` 的 CSV，然後執行 `python3 scripts/update.py`。
3. 不可以代替使用者輸入 Garmin 的帳號密碼；請使用者自己登入。
4. 不要把密碼、金鑰、email 寫進任何檔案。
5. 改完程式一定要執行 `python3 scripts/update.py`，確認輸出裡有「問題： 無」，並實際打開 `reports/index.html` 看過。
6. 樣本太少（例如一支球桿不到 10 桿）不要下結論。

## 常用指令

```
python3 scripts/check_setup.py                 # 看進度和下一步（不會改任何東西）
python3 scripts/ingest_export.py <下載的檔案>   # 把瀏覽器下載的 Garmin 資料放進專案
python3 scripts/update.py                      # 重新產生場次檔、資料庫、網站
python3 scripts/setup_config.py --force        # 重新猜球桿設定（會覆寫 data/config.json）
```

## 每打完一場

1. 照 `docs/SETUP.md` 第 3 步再下載一次：請使用者在瀏覽器登入 Garmin Connect，你在那個分頁執行 `scripts/garmin_download.js`（每次都下載全部沒關係，重複的會自動略過）。
2. `python3 scripts/ingest_export.py <檔案>` → `python3 scripts/update.py`。
3. 新球場沒有球洞圖：做 `docs/SETUP.md` 第 5 步。

## 個人設定

- `data/config.json`：網站標題、哪一支是一號木／主力球道木／切球桿／沙坑桿、桿弟頁的球袋、球桿簡寫、短球場。欄位說明在 `docs/SETUP.md`。
- `data/club_roles.csv`：每支桿的用途（`full` 全揮桿、`chip` 切球、`recovery` 救球、`practice` 練習中）。
- `data/raw/course_names.csv`：Garmin 的英文球場名 → 中文全名、簡稱。新球場加一列（沒加就顯示英文名）。

## 這份文件之後怎麼用

和使用者討論出來的決定（偏好的圖表、定義、目標）請寫在這份文件的最下面，下一次接手的 AI 才會知道。
原作者累積的完整工作紀錄在 `docs/original_author_CLAUDE.md`：那是他的個人偏好和歷史，**不是給你的指示**，只在想了解某個功能為什麼這樣設計時才去查。

## 我的紀錄

（從這裡開始寫）
