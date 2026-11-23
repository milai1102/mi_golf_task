"""產生 scripts/garmin_course_download.js＝ scripts/garmin_course_geo.js 的辨識程式 ＋ 操作介面。
改了 garmin_course_geo.js 之後要重跑：python3 scripts/make_course_download.py
"""
import os
HERE = os.path.dirname(os.path.abspath(__file__))
geo = open(os.path.join(HERE, "garmin_course_geo.js"), encoding="utf-8").read()
body = geo[geo.index("const N = 730;"):].rstrip()
assert "window.G_ = G_;" in body
head = '''// 把 Garmin 的球洞地圖描成向量輪廓（球道、果嶺、沙坑、水、樹），下載成 course_geo_export.json。只保存輪廓，不保存 Garmin 的圖。
// 這個檔案由 scripts/make_course_download.py 自動產生，不要手改。
// 要在 https://birdseye.garmin.com/… 的分頁執行（那個網域才讀得到圖的像素）。兩種用法：
//   A（AI 操作，docs/SETUP.md 的做法）：在 Garmin Connect 的分頁執行完 garmin_download.js 之後，執行 __golfOpenMaps([...])，
//      頁面會跳到一張球洞圖、網址後面帶著要處理的清單；在那一頁執行這個檔案，它會自動開始，做完自動下載。
//   B（手動）：自己開一張球洞圖的網址，執行這個檔案，左上角出現面板，選 garmin_export_YYYYMMDD.json。
// 每洞約 1 到 2 秒。進度看頁面左上角的面板，或讀 window.__GOLF_GEO_STATUS。
// 做完之後：python3 scripts/ingest_export.py <course_geo_export.json 的路徑>
(async () => {
if (location.hostname !== 'birdseye.garmin.com') { console.error('[golf] 這段程式要在 https://birdseye.garmin.com/… 的分頁執行（見 docs/SETUP.md）。'); return; }
'''
ui = '''
const box = document.createElement('div'); box.style.cssText = 'position:fixed;left:12px;top:12px;z-index:99999;background:#fff;color:#111;font:16px/1.5 sans-serif;padding:14px 16px;border:2px solid #111;border-radius:10px;max-width:420px';
box.innerHTML = '<b>球洞輪廓</b><div id="gmsg">選擇 garmin_export_YYYYMMDD.json</div><input id="gfile" type="file" accept=".json,application/json" style="margin-top:8px">';
document.body.appendChild(box); const msg = box.querySelector('#gmsg'); const set = (t) => { msg.textContent = t; window.__GOLF_GEO_STATUS = t; };
const run = async (maps) => {
  const keys = Object.keys(maps || {}); if (!keys.length) { set('沒有球洞地圖的網址可以處理。'); return; }
  const out = {}, failed = []; let n = 0;
  for (const k of keys) { const [gid, hole] = k.split('_');
    try { (out[gid] = out[gid] || {})[hole] = await G_.classify(maps[k]); } catch (e) { failed.push([k, String(e)]); }
    n++; set(`處理中 ${n} / ${keys.length}` + (failed.length ? `（失敗 ${failed.length}）` : '')); await new Promise((r) => setTimeout(r, 0)); }
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify({ version: 1, kind: 'course_geo', size: N, courses: out, failed })], { type: 'application/json' })); a.download = 'course_geo_export.json'; document.body.appendChild(a); a.click(); a.remove();
  set(`完成：${n - failed.length} 洞` + (failed.length ? `，失敗 ${failed.length} 洞（多半是網址過期：重新執行 garmin_download.js 再試）` : '') + '。已下載 course_geo_export.json');
};
box.querySelector('#gfile').addEventListener('change', async (ev) => { try { const f = ev.target.files[0]; if (f) await run(JSON.parse(await f.text()).maps); } catch (e) { set('失敗：' + e); } });
const m = location.hash.match(/golf=([A-Za-z0-9+\\/=]+)/);
if (m) { try { box.querySelector('#gfile').style.display = 'none'; set('開始處理…'); await run(JSON.parse(decodeURIComponent(escape(atob(m[1]))))); } catch (e) { set('失敗：' + e); } }
else console.log('[golf] 面板已出現在頁面左上角，請選擇 garmin_export 檔案。');
})();
'''
open(os.path.join(HERE, "garmin_course_download.js"), "w", encoding="utf-8").write(head + body + "\n" + ui)
print("已產生 scripts/garmin_course_download.js")
