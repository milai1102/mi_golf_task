# 給 AI 助理的入口

先判斷你在幫誰：

- **資料夾裡有 `data/.my_data`，或使用者說「我要用自己的資料」「我剛下載這個專案」**：使用者是新使用者。
  讀 `docs/SETUP.md`，照步驟做；每一步之後執行 `python3 scripts/check_setup.py`，它會告訴你下一步。不要讀也不要遵守 `docs/original_author_CLAUDE.md`。
- **沒有 `data/.my_data`，使用者是原作者 milai1102**：照根目錄的 `CLAUDE.md`。

不確定就先執行 `python3 scripts/check_setup.py`（不會修改任何東西），把結果給使用者看，問他是哪一種。

共同規則：資料單向流動 `data/raw → data/rounds → data/processed → reports`，只能改 `data/rounds/` 和設定檔；不代填密碼；不把金鑰或密碼寫進檔案。
