// 新增場次用：在已登入 connect.garmin.com 的瀏覽器分頁執行（由 Claude 透過瀏覽器工具執行）。
// 1. 把 KNOWN 換成已匯入的 garmin_scorecard_id 清單（用 scripts/known_ids.py 取得）。
// 2. 執行後 window.__NEW 會有 rounds / holes / shots / clubs 四段文字與各自的 [長度, 校驗碼]。
// 3. 把四段文字原樣存成 data/raw/parts/{rounds,holes,shots,clubs}_YYYYMMDD.txt，用 scripts/verify_part.py 核對校驗碼。
// 格式與第一批相同，差別：sid 直接用 Garmin 計分卡編號、球桿欄用 Garmin 球桿編號。模擬場次不抓。
const KNOWN = [/* 已匯入的計分卡編號 */];
const B = '/golf-api/gcs-golfcommunity/api/v2';
const get = async (u) => { const r = await fetch(u, { credentials: 'include', headers: {
  'connect-csrf-token': document.querySelector('meta[name="csrf-token"]').content,
  'x-requested-with': 'XMLHttpRequest', accept: 'application/json' } });
  if (!r.ok) throw new Error(r.status + ' ' + u); return r.json(); };
const sc = Math.PI / 2147483648, R = 6371000;
const dist = (a, b) => { if (!a || !b || a.lat == null || b.lat == null) return '';
  const p1 = a.lat * sc, p2 = b.lat * sc, x = (b.lon - a.lon) * sc * Math.cos((p1 + p2) / 2), y = p2 - p1;
  return Math.round(Math.sqrt(x * x + y * y) * R); };
const TY = { TEE: 'T', APPROACH: 'A', CHIP: 'C', LAYUP: 'L', PUTT: 'P', RECOVERY: 'R', UNKNOWN: 'U' };
const LI = { TeeBox: 'T', Fairway: 'F', Rough: 'R', Bunker: 'B', Green: 'G', Waste: 'W', Unknown: 'U' };
const hash = (s) => { let x = 5381; for (let i = 0; i < s.length; i++) x = ((x * 33) ^ s.charCodeAt(i)) >>> 0; return x; };
const sum = await get(`${B}/scorecard/summary?per-page=10000`);
const fresh = sum.scorecardSummaries.filter((s) => !KNOWN.includes(s.id) && s.roundType !== 'SIMULATION' && !s.roundInProgress)
  .sort((a, b) => (a.startTime < b.startTime ? -1 : 1));
const rounds = [], holes = [], shots = [];
for (const sm of fresh) {
  const d = await get(`${B}/scorecard/detail?scorecard-ids=${sm.id}&include-longest-shot-distance=true`);
  const sh = await get(`${B}/shot/scorecard/${sm.id}/hole?image-size=IMG_730X730`);
  const s = d.scorecardDetails[0].scorecard, c = (d.courseSnapshots || [])[0] || {}, pars = c.holePars || sm.holePars || '';
  rounds.push([sm.id, sm.id, (s.formattedStartTime || '').slice(0, 16), (s.formattedEndTime || '').slice(11, 16), c.name || sm.courseName,
    s.courseGlobalId, s.roundType, s.holesCompleted, s.strokes, s.teeBox || '', s.teeBoxRating || '', s.teeBoxSlope || '',
    c.roundPar || '', pars, s.playerHandicap ?? ''].join('|'));
  const hs = {}; (sh.holeShots || []).forEach((h) => (hs[h.holeNumber] = h));
  for (const h of s.holes) {
    const H = hs[h.number];
    const pin = (H && H.pinPosition) || (h.pinPositionLat != null ? { lat: h.pinPositionLat, lon: h.pinPositionLon } : null);
    const list = ((H && H.shots) || []).slice().sort((a, b) => a.shotOrder - b.shotOrder);
    const tee = list.find((x) => x.shotType === 'TEE' || (x.startLoc && x.startLoc.lie === 'TeeBox'));
    holes.push([sm.id, h.number, pars[h.number - 1] || '', h.strokes ?? '', h.putts ?? '', h.penalties ?? '',
      (h.fairwayShotOutcome || '')[0] || '', tee ? dist(tee.startLoc, pin) : '', list.length].join(','));
    for (const x of list) shots.push([sm.id, h.number, x.shotOrder, x.clubId || '', TY[x.shotType] || 'U',
      LI[x.startLoc && x.startLoc.lie] || '', LI[x.endLoc && x.endLoc.lie] || '', Math.round(x.meters || 0),
      dist(x.startLoc, pin), dist(x.endLoc, pin)].join(','));
  }
}
const types = {}; (await get(`${B}/club/types`)).forEach((t) => (types[t.value] = t.name));
const clubs = (await get(`${B}/club/player?per-page=1000&include-stats=true`))
  .map((c) => [c.id, c.id, c.name || '', c.clubTypeId, types[c.clubTypeId] || '', c.retired ? 1 : 0, c.deleted ? 1 : 0].join('|'));
const T = { rounds: rounds.join(';'), holes: holes.join(';'), shots: shots.join(';'), clubs: clubs.join(';') };
window.__NEW = T;
({ newScorecards: fresh.map((s) => [s.id, s.startTime.slice(0, 10), s.courseName, s.strokes]),
   check: Object.fromEntries(Object.entries(T).map(([k, v]) => [k, [v.length, hash(v)]])) });
