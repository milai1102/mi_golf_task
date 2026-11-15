// Garmin Connect 高爾夫資料匯出
// 用法：在已登入 connect.garmin.com 的 Chrome 分頁，開 DevTools Console（Cmd+Option+J），貼上整段後按 Enter。
// 完成後瀏覽器會下載 garmin-golf-export.json，請移到 data/raw/。
// 注意：使用 Garmin 網頁內部介面（非官方），Garmin 改版後可能失效。
(async () => {
  const B = '/golf-api/gcs-golfcommunity/api/v2';
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const get = async (u) => {
    const r = await fetch(u, { credentials: 'include', headers: {
      'connect-csrf-token': csrf, 'x-requested-with': 'XMLHttpRequest', accept: 'application/json' } });
    if (!r.ok) throw new Error(r.status + ' ' + u);
    return r.json();
  };
  const summary = await get(`${B}/scorecard/summary?per-page=10000`);
  const out = { exportedAt: new Date().toISOString(), summary, details: {}, shots: {}, errors: [] };
  const ids = summary.scorecardSummaries.map((s) => s.id);
  for (const [i, id] of ids.entries()) {
    try {
      out.details[id] = await get(`${B}/scorecard/detail?scorecard-ids=${id}&include-longest-shot-distance=true`);
      out.shots[id] = await get(`${B}/shot/scorecard/${id}/hole?image-size=IMG_730X730`);
    } catch (e) { out.errors.push([id, String(e)]); }
    console.log(`${i + 1}/${ids.length}`);
    await new Promise((r) => setTimeout(r, 150));
  }
  out.clubs = await get(`${B}/club/player?per-page=1000&include-stats=true`);
  out.clubTypes = await get(`${B}/club/types`);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out)], { type: 'application/json' }));
  a.download = 'garmin-golf-export.json';
  document.body.appendChild(a); a.click(); a.remove();
  console.log('完成', ids.length, '場，錯誤', out.errors.length);
})();
