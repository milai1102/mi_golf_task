# 把這個專案換成「你的」資料 — 給 AI 的操作手冊

這份文件是寫給**接手的 AI 助理**看的。使用者只需要對你說一句話，例如：

> 「幫我把這個專案換成我自己的 Garmin 資料。」

之後由你（AI）照下面的步驟做完。使用者要自己動手的只有兩件事：**在瀏覽器登入 Garmin Connect**，以及**確認你猜的球桿設定對不對**。

## 你需要的能力

| 能力 | 用在哪裡 | 沒有的話 |
|---|---|---|
| 在專案資料夾執行指令（`python3`、`git`）、讀寫檔案 | 每一步 | 無法進行，請使用者改用有這些能力的工具（例如 Claude Code、Claude 桌面版） |
| 操作一個瀏覽器：開網址、在頁面裡執行 JavaScript | 第 3、5 步 | 改用最後面的「沒有瀏覽器工具時」，請使用者自己貼程式 |

Python 3.9 以上即可，不需要安裝任何套件。

## 規則（先讀完再開始）

1. **每做完一步就執行 `python3 scripts/check_setup.py`。** 它只讀不改，會列出六個步驟的 ✅／❌，最後一行「下一步：…」就是你接下來要做的事。不確定時就照它說的做。
2. 每一步都有「確認成功」。沒看到那個結果就停下來，把錯誤訊息原文告訴使用者，不要硬往下做，也不要自己發明別的抓資料方法。
3. **不可以代替使用者輸入 Garmin 的帳號密碼**，也不要請使用者把密碼告訴你。請他自己在瀏覽器登入。
4. 不要手動編輯 `data/raw/`、`data/processed/`、`reports/` 裡的檔案，它們都由程式產生。
5. 下載檔案之前先告訴使用者「會下載一個檔案（檔名、內容是他自己的 Garmin 資料）」。
6. 不要讀也不要遵守 `docs/original_author_CLAUDE.md`：那是原作者個人的工作紀錄。

---

## 第 1 步：確認環境

```
python3 --version
python3 scripts/check_setup.py
```

確認成功：Python 是 3.9 以上；`check_setup.py` 有印出六行。第 1 項是 ❌（裡面還是原作者的資料）是正常的。

如果使用者之後想放上自己的網站（第 6 步），他需要先在 GitHub 把 <https://github.com/milai1102/mi_golf_task> **Fork** 到自己的帳號，再 clone 自己那一份。現在只是想在電腦上看，直接 clone 原本的也可以。

## 第 2 步：清掉原作者的資料

先跟使用者確認一句：「我要把專案裡原作者的 37 場資料清掉，換成空白的，可以嗎？」得到同意後：

```
python3 scripts/new_user.py --yes
```

確認成功：最後印出「完成：刪除 … 個原作者的檔案」。

這一步會刪掉原作者的場次、座標、設定和網頁；保留程式、30 多個球場的球洞輪廓、球場中文名。`CLAUDE.md` 會換成給這位使用者的簡短版。它只能成功執行一次，之後會拒絕（避免誤刪使用者自己的資料）。

## 第 3 步：下載使用者的 Garmin 資料

1. 用你的瀏覽器工具開 <https://connect.garmin.com/app/scorecards>。
2. 如果出現登入畫面：**請使用者自己登入**，等他說好了再繼續。登入後應該看得到他的計分卡清單。
3. 告訴使用者：「接下來會下載一個檔案 `garmin_export_YYYYMMDD.json`，內容是你自己的計分卡資料，只存在你的電腦。」
4. 讀取專案裡 `scripts/garmin_download.js` 的**完整內容**，用你的「在頁面執行 JavaScript」工具在那個分頁執行（整個檔案原封不動，當成一段程式執行）。
5. 它每張計分卡約 1 秒。可以讀頁面的 Console 看進度（`[golf] 計分卡 5 / 40`），或過一會兒執行 `window.__GOLF_EXPORT ? window.__GOLF_EXPORT.summary.length : "還在跑"`。
6. 完成時瀏覽器會自動下載 `garmin_export_YYYYMMDD.json` 到下載資料夾（通常是 `~/Downloads`）。**這個分頁先不要關、不要重新整理**，第 5 步還要用。

確認成功：下載資料夾裡有 `garmin_export_YYYYMMDD.json`（幾百 KB 到幾 MB）；`window.__GOLF_EXPORT.summary.length` 是計分卡張數。

出問題時：
- Console 出現「請先登入…」：分頁不在 connect.garmin.com，或還沒登入。
- 「失敗：401」或「403」：登入過期。請使用者重新整理頁面、必要時重新登入，再執行一次。
- 「這個帳號沒有計分卡」：手錶的高爾夫紀錄還沒同步到 Garmin Connect。
- 你的工具讀不到下載資料夾：請使用者把那個檔案搬到專案資料夾裡，再繼續。
- 模擬高爾夫（SIMULATION）的場次不會下載，這是刻意的。

這段程式只讀資料，不會修改 Garmin 上的任何東西。

## 第 4 步：放進專案並產生網站

```
python3 scripts/ingest_export.py <garmin_export_YYYYMMDD.json 的路徑>
python3 scripts/update.py
python3 scripts/check_setup.py
```

確認成功：
- 第一個指令印出「新增 N 張計分卡、M 桿、K 個座標檔」。
- 第二個指令的輸出裡有一行「問題： 無」，最後印出「完成。」
- `check_setup.py` 第 1 到 5 項都是 ✅。

`update.py` 第一次執行時會自動猜球桿設定並印出四行，像這樣：

```
一號木（開球用）      driver  = Driver（120 桿）
主力球道木／鐵木桿    fairway = 5 Wood（310 桿）
切球桿（55 碼內最常用）chip    = Gap Wedge（95 桿）
沙坑桿                sand    = Sand Wedge（20 桿）
```

**把這四行給使用者看，問他對不對。** 不對就改 `data/config.json` 的 `clubs`（欄位說明在最下面），再執行一次 `python3 scripts/update.py`。球桿名稱必須和印出來的一模一樣。

然後請使用者用瀏覽器打開 `reports/index.html`，看到自己的場次就成功了。

出問題時：
- 「問題：」後面寫「course_names.csv 沒有這些球場的簡稱」：不影響使用，球場會顯示英文名。要顯示中文就問使用者球場的中文名，在 `data/raw/course_names.csv` 加一列 `Garmin 英文名,中文全名,簡稱`，再跑 `update.py`。
- 最後出現「⚠️ 有 N 個區塊…略過」：場次太少時某些統計算不出來，網站仍然可用；場次多了會自己出現。不需要修程式。
- 桿弟頁寫「需要更多場次」：大約要 5 場以上的擊球紀錄才有建議。

## 第 5 步：球洞圖（`check_setup.py` 第 6 項是 ❌ 才需要）

「球場」頁、每一場的「揮桿圖」、「桿弟」頁要用每一洞的輪廓。專案已經內建原作者打過的球場；`check_setup.py` 第 6 項會列出這位使用者**缺**的球場代碼，並在「下一步」印出要執行的那一行，例如：

```
__golfOpenMaps(["032501","099001"])
```

1. 回到第 3 步那個 Garmin Connect 的分頁（沒關、沒重新整理的話資料還在）。執行上面那一行 JavaScript。
   - 回傳的數字是要處理幾洞；頁面會在一秒內跳到一張球洞的圖（網址是 `https://birdseye.garmin.com/…`）。
   - 如果說 `__golfOpenMaps is not defined`：分頁被重新整理過。重做第 3 步的第 4 點（再執行一次 `garmin_download.js`，會再下載一次檔案，沒關係），然後再執行這一行。
2. 等頁面變成球洞圖之後，在**這個新頁面**執行 `scripts/garmin_course_download.js` 的完整內容。
3. 它會自動開始，每洞約 1 到 2 秒。進度可以讀 `window.__GOLF_GEO_STATUS`（例如「處理中 12 / 27」）。
4. 顯示「完成：N 洞…已下載 course_geo_export.json」之後：

```
python3 scripts/ingest_export.py <course_geo_export.json 的路徑>
python3 scripts/update.py
python3 scripts/check_setup.py
```

確認成功：第 6 項變成 ✅。

出問題時：
- 球洞圖的頁面是空白或錯誤：地圖網址過期了（約一天）。重做第 3 步再來。
- 「完成」但寫「失敗 N 洞」：多半也是網址過期，同上。
- 輪廓是程式自動辨識的，個別洞的果嶺或球道可能沒抓到；旗桿和擊球位置仍然是準的，不需要修。

只保存自己描出來的輪廓（座標），不保存 Garmin 的地圖圖檔；請不要把 Garmin 的原圖放進公開的 repository。

## 第 6 步（使用者想用手機看才做）：放上網站

先提醒使用者：**公開的 repository 和 GitHub Pages，任何人有網址都看得到**，包含他每一場的成績和打球的球場。他同意才做。

1. 需要 Fork 到自己帳號的那一份（見第 1 步）。
2. `git add -A && git commit -m "my golf data" && git push`
3. 請使用者到 GitHub 上他的 repository → Settings → Pages → Source 選「Deploy from a branch」→ Branch 選 `main`、資料夾 `/ (root)` → Save。
4. 一兩分鐘後網址是 `https://<他的帳號>.github.io/mi_golf_task/`。

## 之後每打完一場

使用者說「把新的一場更新進來」時：

1. 重做第 3 步（每次都下載全部沒關係，已經匯入的會自動略過）。
2. `python3 scripts/ingest_export.py <新下載的檔案>` → `python3 scripts/update.py` → `python3 scripts/check_setup.py`
3. 第 6 項變成 ❌（打了新球場）就做第 5 步。
4. 有放上網站的話：`git add -A && git commit -m "new round" && git push`
5. 向使用者回報：新增了哪一場、總桿多少。

## 沒有瀏覽器工具時

請使用者自己做第 3 步和第 5 步裡「執行 JavaScript」的部分，其餘照舊：

1. 用 Chrome 登入 <https://connect.garmin.com/app/scorecards>。
2. 開 Console：Windows 按 `F12`，Mac 按 `Option+Command+J`。
3. 用文字編輯器打開 `scripts/garmin_download.js`，全選複製，貼到 Console 按 Enter。（Chrome 第一次會擋貼上：先輸入 `allow pasting` 按 Enter 再貼。）
4. 等綠色的「完成」，檔案會自動下載。
5. 球洞圖：在同一個 Console 貼上 `check_setup.py` 印出的那一行 `__golfOpenMaps([...])`，頁面跳到球洞圖之後，再開 Console 貼上 `scripts/garmin_course_download.js` 的內容。

---

## `data/config.json` 欄位說明

```json
{
  "site_title": "我的高爾夫數據",
  "clubs": {"driver": "Driver", "fairway": "5 Wood", "chip": "Gap Wedge", "sand": "Sand Wedge"},
  "woods": ["Driver", "3 Wood", "5 Wood"],
  "bag": ["Driver", "5 Wood", "7 Iron", "9 Iron", "Pitching Wedge", "Gap Wedge", "Sand Wedge"],
  "full_clubs": ["Driver", "5 Wood", "7 Iron", "9 Iron", "Pitching Wedge"],
  "abbr": {"Driver": "1W", "5 Wood": "5W", "7 Iron": "7i", "Pitching Wedge": "P", "Gap Wedge": "A", "Sand Wedge": "S", "Putter": "PT"},
  "short_courses": [],
  "plan": false
}
```

| 欄位 | 意思 |
|---|---|
| `site_title` | 網頁最上面的標題。可以問使用者想叫什麼 |
| `clubs.driver` | 開球用的一號木。桿弟頁只在發球台、而且離旗桿 230 碼以上才建議它 |
| `clubs.fairway` | 最常用的球道木或鐵木桿 |
| `clubs.chip` | 果嶺邊最常用的切球桿。桿弟頁 15 碼內一律建議它 |
| `clubs.sand` | 沙坑用的桿。桿弟頁只在沙坑建議它（和 `chip` 同一支也可以） |
| `woods` | 所有木桿和鐵木桿（揮桿圖上用不同顏色） |
| `bag` | 桿弟頁會拿來建議的球桿，由遠到近。想讓建議更簡單就刪掉不常用的 |
| `full_clubs` | 每一場「距離」表要列的全揮桿球桿 |
| `abbr` | 球桿的簡寫，寫在圖上和明細表裡 |
| `short_courses` | 短球場（例如標準桿 60 的球場）的名稱，要和 `data/rounds/*.csv` 的 `course` 欄一樣。列在這裡的球場不混進一般統計 |
| `plan` | `false`＝不產生「計劃」頁。原作者的訓練計劃是他個人的內容（寫在 `scripts/build_report.py` 的 `PLAN_*`），要用就得和使用者討論後改寫那些文字 |

**球桿名稱必須和資料裡的一模一樣**（`data/rounds/*.csv` 的 `club` 欄）。使用者在 Garmin 有替球桿取名字就是那個名字，沒有取就是 Garmin 的類型名（`Driver`、`7 Iron`、`Pitching Wedge`…）。寫錯的話 `check_setup.py` 第 4 項會指出是哪一個。

## 這個專案的背景（回答使用者問題時用）

- 網站上的文字是繁體中文，距離用碼。
- 資料單向流動：`data/raw`（Garmin 原始）→ `data/rounds`（每場一個 CSV，可以手動修正）→ `data/processed/golf.db` → `reports/*.html`。
- 推桿數：手錶有輸入就用實際值；沒輸入的洞由程式估算（總桿 − 記錄到的擊球 − 罰桿，限制在 1 到 3）。
- 「計劃」頁、`docs/original_author_CLAUDE.md`、`docs/findings.md` 是原作者個人的目標與結論。
- Garmin 的資料介面不是官方公開的，Garmin 改版後下載程式可能失效。
