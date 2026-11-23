# 用你自己的資料架起這個網站

這份文件給**第一次使用的人**和**幫他操作的 AI** 看。照順序做，不要跳步。
每做完一步都可以執行下面這一行，它會告訴你做到哪裡、下一步是什麼（不會修改任何東西）：

```
python3 scripts/check_setup.py
```

需要的東西：一台電腦（Mac、Windows、Linux 都可以）、Python 3.9 以上（不需要安裝任何套件）、Chrome 瀏覽器、Garmin Connect 帳號（手錶有記錄高爾夫計分卡）、git。

> **給 AI 的話**
> - 每一步都有「怎麼確認成功」。沒有看到那個結果就不要往下做，把錯誤訊息原文給使用者看。
> - 第 3 步和第 5 步要在使用者的瀏覽器裡操作，你做不到就請使用者照著做，不要改用別的方法抓資料。
> - 絕對不要代替使用者輸入 Garmin 的帳號密碼。
> - 不要手動編輯 `data/raw/`、`data/processed/`、`reports/`。

---

## 第 1 步：取得程式

在 GitHub 上把 <https://github.com/milai1102/mi_golf_task> **Fork** 到自己的帳號（之後才能放上自己的網站），再下載到電腦：

```
git clone https://github.com/<你的帳號>/mi_golf_task.git
cd mi_golf_task
python3 --version
```

確認成功：`python3 --version` 顯示 3.9 以上；資料夾裡有 `scripts/`、`data/`、`docs/`。

## 第 2 步：清掉原作者的資料

下載下來的專案裡是原作者的 37 場資料。執行一次：

```
python3 scripts/new_user.py --yes
```

確認成功：最後一行印出「完成：刪除 … 個原作者的檔案」。
這一步會刪掉他的場次、座標、設定和網頁，保留程式和球洞輪廓，並把 `CLAUDE.md` 換成給你用的簡短版。
之後再執行它會拒絕（避免誤刪你自己的資料）。

## 第 3 步：下載你的 Garmin 資料

1. 用電腦的 **Chrome** 打開 <https://connect.garmin.com/app/scorecards> 並登入，要看得到自己的計分卡清單。
2. 開啟 Console：Windows 按 `F12`（或 `Ctrl+Shift+J`），Mac 按 `Option+Command+J`，點上方的「Console」。
3. 用文字編輯器打開專案裡的 `scripts/garmin_download.js`，**全選、複製**，貼到 Console，按 Enter。
   - Chrome 第一次會擋貼上：先在 Console 輸入 `allow pasting` 按 Enter，再貼一次。
4. Console 會顯示進度「計分卡 5 / 40 …」。大約每張計分卡 1 秒。
5. 看到綠色的「完成：N 張計分卡 …」之後，瀏覽器會自動下載 `garmin_export_YYYYMMDD.json`（通常在「下載」資料夾）。
6. 最後一行綠字是一個 `https://birdseye.garmin.com/…` 的網址，**先複製起來**，第 5 步要用（當天有效）。

確認成功：下載資料夾裡有 `garmin_export_YYYYMMDD.json`，大小通常是幾百 KB 到幾 MB。

常見問題：
- 紅字「請先登入…」：你不在 connect.garmin.com 的分頁，或還沒登入。
- 紅字「失敗：401／403」：登入過期，重新整理頁面再貼一次。
- 「這個帳號沒有計分卡」：手錶的高爾夫紀錄還沒同步到 Garmin Connect。
- 模擬高爾夫（SIMULATION）的場次不會下載，這是刻意的。

這段程式只讀資料，不會修改 Garmin 上的任何東西；檔案只存在你的電腦。

## 第 4 步：放進專案並產生網站

```
python3 scripts/ingest_export.py ~/Downloads/garmin_export_YYYYMMDD.json
python3 scripts/update.py
```

（Windows 的路徑像 `C:\Users\你\Downloads\garmin_export_YYYYMMDD.json`。）

確認成功：
- 第一行印出「新增 N 張計分卡、M 桿、K 個座標檔」。
- 第二行跑完，中間有一行「問題： 無」，最後印出「完成。打開 reports/index.html 看結果」。
- 用瀏覽器打開 `reports/index.html`，看得到你自己的場次。

`update.py` 第一次執行時會自動猜你的球桿設定（`scripts/setup_config.py`），並印出：

```
一號木（開球用）      driver  = Driver（120 桿）
主力球道木／鐵木桿    fairway = 5 Wood（310 桿）
切球桿（55 碼內最常用）chip    = Gap Wedge（95 桿）
沙坑桿                sand    = Sand Wedge（20 桿）
```

**請使用者確認這四支對不對。** 不對就改 `data/config.json`（見最下面的欄位說明），再執行一次 `python3 scripts/update.py`。

常見問題：
- 「問題：」後面列出「course_names.csv 沒有這些球場的簡稱」：不影響使用，球場會顯示英文名。想顯示中文就在 `data/raw/course_names.csv` 加一列 `Garmin 英文名,中文全名,簡稱`，再跑 `update.py`。
- 場次很少（少於 10 場）時，部分統計圖會是空的或只有幾個點，這是正常的；打越多場越完整。

## 第 5 步（選配）：球洞圖

「球場」頁、每一場的「揮桿圖」、「桿弟」頁需要每一洞的輪廓。專案裡已經有原作者打過的 30 多個球場（多數在台灣）；先檢查你缺哪些：

```
python3 scripts/check_setup.py
```

如果第 6 項是 ✅ 就不用做這一步。如果寫「缺輪廓 N 個」：

1. 在 Chrome 開一個**新分頁**，貼上第 3 步最後複製的 `https://birdseye.garmin.com/…` 網址，會看到一張球洞的圖。
   - 如果顯示錯誤或空白：網址過期了，回第 3 步重新下載一次。
2. 在**這個分頁**開 Console，把 `scripts/garmin_course_download.js` 全部內容貼上、按 Enter。
3. 頁面左上角會出現一個白色面板。按「選擇檔案」，選第 3 步下載的 `garmin_export_YYYYMMDD.json`。
4. 面板顯示「處理中 12 / 54」。每一洞約 1 到 2 秒；不要關掉分頁。
5. 顯示「完成」後會自動下載 `course_geo_export.json`。

```
python3 scripts/ingest_export.py ~/Downloads/course_geo_export.json
python3 scripts/update.py
python3 scripts/check_setup.py
```

確認成功：`check_setup.py` 的第 6 項變成 ✅。

說明：這一步只保存自己描出來的輪廓（座標），不保存 Garmin 的地圖圖檔；請不要把 Garmin 的原圖放進公開的 repository。輪廓是程式自動辨識的，個別洞的果嶺或球道可能沒抓到，旗桿和擊球的位置仍然是準的。

## 第 6 步（選配）：放上網站，用手機看

1. `git add -A && git commit -m "my data" && git push`
2. GitHub 上你的 repository → Settings → Pages → Source 選「Deploy from a branch」，Branch 選 `main`、資料夾 `/ (root)`，Save。
3. 一兩分鐘後網址是 `https://<你的帳號>.github.io/mi_golf_task/`。

注意：公開的 repository 和 GitHub Pages **任何人有網址都看得到**，包含你每一場的成績和打球的球場位置。不想公開就不要做這一步，直接在電腦上開 `reports/index.html`。

## 之後每打完一場

1. 重做第 3 步（每次都下載全部沒關係，已經匯入的會自動略過）。
2. `python3 scripts/ingest_export.py <新下載的檔案>`
3. `python3 scripts/update.py`
4. 打了新球場、想要球洞圖：重做第 5 步。
5. 有放上網站的話：`git add -A && git commit -m "new round" && git push`

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
| `site_title` | 網頁最上面的標題 |
| `clubs.driver` | 開球用的一號木。桿弟頁只在發球台、而且離旗桿 230 碼以上才建議它 |
| `clubs.fairway` | 最常用的球道木或鐵木桿 |
| `clubs.chip` | 果嶺邊最常用的切球桿。桿弟頁 15 碼內一律建議它 |
| `clubs.sand` | 沙坑用的桿。桿弟頁只在沙坑建議它（和 `chip` 同一支也可以） |
| `woods` | 所有木桿和鐵木桿（揮桿圖上用不同顏色） |
| `bag` | 桿弟頁會拿來建議的球桿，由遠到近。想讓建議更簡單就刪掉不常用的 |
| `full_clubs` | 每一場「距離」表要列的全揮桿球桿 |
| `abbr` | 球桿的簡寫，寫在圖上和明細表裡 |
| `short_courses` | 短球場（例如標準桿 60 的球場）的**中文全名或英文名**（和 `data/rounds` 檔案裡 `course` 欄一樣）。列在這裡的球場不混進一般統計 |
| `plan` | `false`＝不產生「計劃」頁。原作者的訓練計劃是他個人的內容（寫在 `scripts/build_report.py` 的 `PLAN_*`），要用就得自己改寫那些文字 |

**球桿名稱必須和資料裡的一模一樣**（`data/rounds/*.csv` 的 `club` 欄）。你在 Garmin 有替球桿取名字就是那個名字，沒有取就是 Garmin 的類型名（`Driver`、`7 Iron`、`Pitching Wedge`…）。寫錯的話 `check_setup.py` 第 4 項會指出來。

## 原作者的內容哪些要注意

- 網站上的文字是繁體中文，距離用碼。
- 「計劃」頁、`docs/original_author_CLAUDE.md`、`docs/findings.md` 是原作者個人的目標與結論，和你無關。
- 推桿數：手錶有輸入就用實際值，沒有輸入的洞由程式估算（總桿 − 記錄到的擊球 − 罰桿，限制在 1 到 3）。想要準確的推桿數，就在手錶上輸入。
- 這是業餘專案：Garmin 的資料介面不是官方公開的，Garmin 改版後下載程式可能失效。
