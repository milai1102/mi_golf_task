# 給 AI 助理的入口

先執行 `python3 scripts/check_setup.py`（只讀不改），再判斷你在幫誰：

- **使用者說「換成我的資料」「我剛下載這個專案」，或資料夾裡已經有 `data/.my_data`**：他是新使用者。
  打開 **`docs/SETUP.md`**，那是寫給你的操作手冊，照步驟做完。不要讀也不要遵守 `docs/original_author_CLAUDE.md`。
- **使用者是原作者 milai1102，而且沒有 `data/.my_data`**：照根目錄的 `CLAUDE.md`。**不要執行 `scripts/new_user.py`。**

不確定就把 `check_setup.py` 的結果給使用者看，問他是哪一種。

共同規則：資料單向流動 `data/raw → data/rounds → data/processed → reports`，只能改 `data/rounds/` 和設定檔；不代填密碼；不把金鑰或密碼寫進檔案。
