// 下載「你自己的」Garmin 高爾夫資料（計分卡、每一桿的座標、球桿、球洞地圖的網址），存成一個檔 garmin_export_YYYYMMDD.json。
// 用法（細節見 docs/SETUP.md 第 3 步）：
//   1. 用電腦的 Chrome 登入 https://connect.garmin.com/app/scorecards（要看得到自己的計分卡清單）。
//   2. 按 F12（Mac：Option+Command+J）開「Console」，把這個檔案的全部內容貼上、按 Enter。
//      Chrome 第一次會要求先輸入 allow pasting 再貼。
//   3. 等 Console 印出「完成」，瀏覽器會自動下載 garmin_export_YYYYMMDD.json。
//   4. 回到專案資料夾執行：python3 scripts/ingest_export.py <下載的檔案路徑>
// 由 AI 透過瀏覽器工具執行也可以（docs/SETUP.md 的主要做法）：整段當成一個運算式執行即可，它會自己下載檔案。
// 只讀取資料，不會修改 Garmin 上的任何東西；資料只存在你的電腦裡。模擬場次（SIMULATION）不下載。
// 已經匯入過的計分卡不用特別排除：ingest_export.py 會自動略過重複的。
(async () => {
  const say = (m) => { window.__GOLF_STATUS = m; console.log('%c[golf] ' + m, 'color:#0a7;font-weight:bold'); };
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (!location.hostname.endsWith('connect.garmin.com') || !meta) { console.error('[golf] 請先登入 https://connect.garmin.com/app/scorecards ，在那個分頁的 Console 執行。'); return; }
  const B = '/golf-api/gcs-golfcommunity/api/v2';
  const get = async (u) => { const r = await fetch(u, { credentials: 'include', headers: { 'connect-csrf-token': meta.content, 'x-requested-with': 'XMLHttpRequest', accept: 'application/json' } });
    if (!r.ok) throw new Error(r.status + ' ' + u); return r.json(); };
  const sc = Math.PI / 2147483648, R = 6371000;
  const dist = (a, b) => { if (!a || !b || a.lat == null || b.lat == null) return '';
    const p1 = a.lat * sc, p2 = b.lat * sc, x = (b.lon - a.lon) * sc * Math.cos((p1 + p2) / 2), y = p2 - p1; return Math.round(Math.sqrt(x * x + y * y) * R); };
  const TY = { TEE: 'T', APPROACH: 'A', CHIP: 'C', LAYUP: 'L', PUTT: 'P', RECOVERY: 'R', UNKNOWN: 'U' };
  const LI = { TeeBox: 'T', Fairway: 'F', Rough: 'R', Bunker: 'B', Green: 'G', Waste: 'W', Unknown: 'U' };
  const hash = (s) => { let x = 5381; for (let i = 0; i < s.length; i++) x = ((x * 33) ^ s.charCodeAt(i)) >>> 0; return x; };
  const clean = (v) => String(v == null ? '' : v).replace(/[|;,\n\r]/g, ' ').trim();
  const loc = (l) => (l && l.lat != null ? [l.lat, l.lon, l.x ?? '', l.y ?? '', l.lie || ''].join(',') : ',,,,');
  try {
    say('讀取球桿…');
    const types = {}; (await get(`${B}/club/types`)).forEach((t) => (types[t.value] = t.name));
    const clubList = await get(`${B}/club/player?per-page=1000&include-stats=true`);
    const clubName = {}; clubList.forEach((c) => (clubName[c.id] = clean(c.name) || types[c.clubTypeId] || ''));
    const clubs = clubList.map((c) => [c.id, c.id, clean(c.name), c.clubTypeId, types[c.clubTypeId] || '', c.retired ? 1 : 0, c.deleted ? 1 : 0].join('|'));
    say('讀取計分卡清單…');
    const sum = await get(`${B}/scorecard/summary?per-page=10000`);
    const all = (sum.scorecardSummaries || []).filter((s) => s.roundType !== 'SIMULATION' && !s.roundInProgress).sort((a, b) => (a.startTime < b.startTime ? -1 : 1));
    if (!all.length) { console.error('[golf] 這個帳號沒有計分卡。'); return; }
    const rounds = [], holes = [], shots = [], gps = {}, maps = {}, summary = [], failed = [];
    for (let i = 0; i < all.length; i++) {
      const sm = all[i];
      try {
        const d = await get(`${B}/scorecard/detail?scorecard-ids=${sm.id}&include-longest-shot-distance=true`);
        const sh = await get(`${B}/shot/scorecard/${sm.id}/hole?image-size=IMG_730X730`);
        const s = d.scorecardDetails[0].scorecard, c = (d.courseSnapshots || [])[0] || {}, pars = c.holePars || sm.holePars || '';
        const cname = clean(c.name || sm.courseName);
        rounds.push([sm.id, sm.id, (s.formattedStartTime || '').slice(0, 16), (s.formattedEndTime || '').slice(11, 16), cname,
          s.courseGlobalId, s.roundType, s.holesCompleted, s.strokes, clean(s.teeBox), s.teeBoxRating || '', s.teeBoxSlope || '',
          c.roundPar || '', pars, s.playerHandicap ?? ''].join('|'));
        const hs = {}; (sh.holeShots || []).forEach((h) => (hs[h.holeNumber] = h));
        const g = ['C|' + String(s.courseGlobalId || '').padStart(6, '0') + '|' + cname + '|' + (s.courseGlobalId || '') + '|' + (s.formattedStartTime || '').slice(0, 16)];
        for (const h of s.holes || []) {
          const H = hs[h.number];
          const pin = (H && H.pinPosition) || (h.pinPositionLat != null ? { lat: h.pinPositionLat, lon: h.pinPositionLon } : null);
          const list = ((H && H.shots) || []).slice().sort((a, b) => a.shotOrder - b.shotOrder);
          const tee = list.find((x) => x.shotType === 'TEE' || (x.startLoc && x.startLoc.lie === 'TeeBox'));
          holes.push([sm.id, h.number, pars[h.number - 1] || '', h.strokes ?? '', h.putts ?? '', h.penalties ?? '',
            (h.fairwayShotOutcome || '')[0] || '', tee ? dist(tee.startLoc, pin) : '', list.length].join(','));
          for (const x of list) shots.push([sm.id, h.number, x.shotOrder, x.clubId || '', TY[x.shotType] || 'U',
            LI[x.startLoc && x.startLoc.lie] || '', LI[x.endLoc && x.endLoc.lie] || '', Math.round(x.meters || 0),
            dist(x.startLoc, pin), dist(x.endLoc, pin)].join(','));
          const url = (H && H.holeImageUrl) || '', m = url.match(/(gid(\d+)\/hole(\d+)\/[^?]+)/);
          if (m) maps[m[2] + '_' + Number(m[3])] = url;
          g.push('H' + h.number + '|' + (pars[h.number - 1] || '') + '|' + (pin && pin.lat != null ? [pin.lat, pin.lon, pin.x ?? '', pin.y ?? ''].join(',') : ',,,') + '|' + (m ? m[1] : ''));
          for (const x of list) g.push([x.shotOrder, clubName[x.clubId] || '', x.shotType || 'UNKNOWN', Math.round(x.meters || 0), loc(x.startLoc), loc(x.endLoc), x.shotTime ?? '', x.autoShotType ?? ''].join('|'));
        }
        gps[sm.id] = g.join('\n') + '\n';
        summary.push([sm.id, (sm.startTime || '').slice(0, 10), cname, s.strokes]);
      } catch (e) { failed.push([sm.id, String(e)]); }
      if ((i + 1) % 5 === 0 || i === all.length - 1) say(`計分卡 ${i + 1} / ${all.length}`);
    }
    const parts = { rounds: rounds.join(';'), holes: holes.join(';'), shots: shots.join(';'), clubs: clubs.join(';') };
    const out = { version: 1, exported_at: new Date().toISOString(), parts, check: Object.fromEntries(Object.entries(parts).map(([k, v]) => [k, [v.length, hash(v)]])), gps, maps, summary, failed };
    const day = new Date().toISOString().slice(0, 10).replace(/-/g, ''), name = `garmin_export_${day}.json`;
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(out)], { type: 'application/json' })); a.download = name; document.body.appendChild(a); a.click(); a.remove();
    window.__GOLF_EXPORT = out;
    say(`完成：${summary.length} 張計分卡、${shots.length} 桿、${Object.keys(maps).length} 張球洞地圖網址` + (failed.length ? `；${failed.length} 張失敗（見 window.__GOLF_EXPORT.failed）` : '') + `。已下載 ${name}`);
    say('下一步：python3 scripts/ingest_export.py <下載的檔案>');
    window.__golfOpenMaps = (cids) => { const want = cids && cids.length ? new Set(cids.map((c) => String(c).padStart(6, '0'))) : null, pick = {};
      for (const k in maps) if (!want || want.has(k.split('_')[0].padStart(6, '0'))) pick[k] = maps[k];
      const keys = Object.keys(pick); if (!keys.length) { console.error('[golf] 這些球場代碼在這次下載的資料裡沒有地圖網址：' + JSON.stringify(cids)); return 0; }
      const url = pick[keys[0]] + '#golf=' + btoa(unescape(encodeURIComponent(JSON.stringify(pick)))); setTimeout(() => { location.href = url; }, 300); return keys.length; };
    say('要球洞圖：在這個分頁執行 __golfOpenMaps(["球場代碼", …])（代碼由 python3 scripts/check_setup.py 列出；不給參數＝全部），頁面會跳到一張球洞圖，再在那一頁執行 scripts/garmin_course_download.js。');
  } catch (e) { window.__GOLF_STATUS = '失敗：' + e; console.error('[golf] 失敗：' + e + '。常見原因：沒登入、登入過期（重新整理頁面再試）、Garmin 改版。'); }
})();
