// 把 Garmin 的球洞地圖描成向量輪廓（球道、果嶺、沙坑、水、樹），下載成 course_geo_export.json。只保存輪廓，不保存 Garmin 的圖。
// 這個檔案由 scripts/make_course_download.py 自動產生，不要手改。
// 要在 https://birdseye.garmin.com/… 的分頁執行（那個網域才讀得到圖的像素）。兩種用法：
//   A（AI 操作，docs/SETUP.md 的做法）：在 Garmin Connect 的分頁執行完 garmin_download.js 之後，執行 __golfOpenMaps([...])，
//      頁面會跳到一張球洞圖、網址後面帶著要處理的清單；在那一頁執行這個檔案，它會自動開始，做完自動下載。
//   B（手動）：自己開一張球洞圖的網址，執行這個檔案，左上角出現面板，選 garmin_export_YYYYMMDD.json。
// 每洞約 1 到 2 秒。進度看頁面左上角的面板，或讀 window.__GOLF_GEO_STATUS。
// 做完之後：python3 scripts/ingest_export.py <course_geo_export.json 的路徑>
(async () => {
if (location.hostname !== 'birdseye.garmin.com') { console.error('[golf] 這段程式要在 https://birdseye.garmin.com/… 的分頁執行（見 docs/SETUP.md）。'); return; }
const N = 730; const G_ = {};
G_.load = async url => { const r = await fetch(url); if (!r.ok) throw new Error('http ' + r.status); const bmp = await createImageBitmap(await r.blob()); const c = new OffscreenCanvas(N, N); const g = c.getContext('2d'); g.drawImage(bmp, 0, 0, N, N); const d = g.getImageData(0,0,N,N).data;
  const R = new Float32Array(N*N), G = new Float32Array(N*N), B = new Float32Array(N*N); for (let i=0;i<N*N;i++){R[i]=d[4*i];G[i]=d[4*i+1];B[i]=d[4*i+2];} return {R,G,B}; };
G_.box = (a, k) => { const r = (k-1)>>1, W = N+1, I = new Float64Array(W*W); for (let y=0;y<N;y++){ let s=0; for (let x=0;x<N;x++){ s+=a[y*N+x]; I[(y+1)*W+x+1]=I[y*W+x+1]+s; } }
  const o = new Float32Array(N*N); for (let y=0;y<N;y++){ const y0=Math.max(0,y-r), y1=Math.min(N,y+r+1); for (let x=0;x<N;x++){ const x0=Math.max(0,x-r), x1=Math.min(N,x+r+1); o[y*N+x]=(I[y1*W+x1]-I[y0*W+x1]-I[y1*W+x0]+I[y0*W+x0])/((y1-y0)*(x1-x0)); } } return o; };
G_.gauss = (a, s) => { const r = Math.ceil(4*s), kk=[]; let sum=0; for (let i=-r;i<=r;i++){const v=Math.exp(-i*i/(2*s*s)); kk.push(v); sum+=v;} const t = new Float32Array(N*N), o = new Float32Array(N*N);
  for (let y=0;y<N;y++) for (let x=0;x<N;x++){ let v=0; for (let i=-r;i<=r;i++){ const xx=Math.min(N-1,Math.max(0,x+i)); v+=a[y*N+xx]*kk[i+r]; } t[y*N+x]=v/sum; }
  for (let y=0;y<N;y++) for (let x=0;x<N;x++){ let v=0; for (let i=-r;i<=r;i++){ const yy=Math.min(N-1,Math.max(0,y+i)); v+=t[yy*N+x]*kk[i+r]; } o[y*N+x]=v/sum; } return o; };
G_.thr = (a, f) => { const o = new Uint8Array(N*N); for (let i=0;i<N*N;i++) o[i] = f(a[i], i) ? 1 : 0; return o; };
G_.dil = (m, k) => G_.thr(G_.box(m, k), v => v > 1e-6); G_.ero = (m, k) => G_.thr(G_.box(m, k), v => v > 1 - 1e-6);
G_.open = (m, k) => G_.dil(G_.ero(m, k), k); G_.close = (m, k) => G_.ero(G_.dil(m, k), k);
G_.fill = m => { const bg = new Uint8Array(N*N), st = []; const push = (x,y) => { const i=y*N+x; if (!m[i] && !bg[i]) { bg[i]=1; st.push(i); } };
  for (let i=0;i<N;i++){ push(i,0); push(i,N-1); push(0,i); push(N-1,i); }
  while (st.length){ const i=st.pop(), x=i%N, y=(i/N)|0; if(x>0)push(x-1,y); if(x<N-1)push(x+1,y); if(y>0)push(x,y-1); if(y<N-1)push(x,y+1); }
  return G_.thr(bg, v => !v); };
G_.comps = (m, minA) => { const lab = new Int32Array(N*N), out = []; let n = 0;
  for (let s=0;s<N*N;s++) if (m[s] && !lab[s]) { n++; const st=[s]; lab[s]=n; let area=0; while (st.length){ const i=st.pop(); area++; const x=i%N;
      if(x>0&&m[i-1]&&!lab[i-1]){lab[i-1]=n;st.push(i-1);} if(x<N-1&&m[i+1]&&!lab[i+1]){lab[i+1]=n;st.push(i+1);} if(i>=N&&m[i-N]&&!lab[i-N]){lab[i-N]=n;st.push(i-N);} if(i<N*N-N&&m[i+N]&&!lab[i+N]){lab[i+N]=n;st.push(i+N);} }
    if (area >= minA) out.push({id:n, area, first:s}); }
  return {lab, out}; };
G_.trace = (lab, id, first) => { const at = (x,y) => x>=0&&y>=0&&x<N&&y<N&&lab[y*N+x]===id; const dx=[1,1,0,-1,-1,-1,0,1], dy=[0,1,1,1,0,-1,-1,-1];
  let x=first%N, y=(first/N)|0, dir=7; const sx=x, sy=y, pts=[[x,y]]; let guard=0;
  while (guard++ < 200000) { let found=false; for (let k=0;k<8;k++){ const d=(dir+6+k)%8; const nx=x+dx[d], ny=y+dy[d]; if (at(nx,ny)) { x=nx; y=ny; dir=d; found=true; break; } }
    if (!found) break; if (x===sx && y===sy) break; pts.push([x,y]); }
  return pts; };
G_.dp = (pts, tol) => { if (pts.length < 4) return pts; const keep = new Uint8Array(pts.length); keep[0]=keep[pts.length-1]=1; const st=[[0,pts.length-1]];
  while (st.length){ const [a,b]=st.pop(); let md=0, mi=-1; const [x1,y1]=pts[a],[x2,y2]=pts[b], L=Math.hypot(x2-x1,y2-y1)||1;
    for (let i=a+1;i<b;i++){ const d=Math.abs((x2-x1)*(y1-pts[i][1])-(x1-pts[i][0])*(y2-y1))/L; if(d>md){md=d;mi=i;} }
    if (md>tol){ keep[mi]=1; st.push([a,mi],[mi,b]); } }
  return pts.filter((_,i)=>keep[i]); };
G_.polys = (m, minA, tol) => { const {lab,out} = G_.comps(m, minA); return out.map(c => { const p = G_.trace(lab, c.id, c.first); const mid = p.length>>1; const a = G_.dp(p.slice(0,mid+1), tol), b = G_.dp(p.slice(mid), tol); return a.concat(b.slice(1)); }).filter(p => p.length >= 4); };
G_.classify = async url => { const {R,G,B} = await G_.load(url); const T = G_.thr;
  const sky = T(B, (b,i) => b>200 && G[i]>190 && R[i]<160);                                  // 地圖外的淺藍底
  const water = T(B, (b,i) => b>170 && R[i]<120 && G[i]<170 && !sky[i]);
  let hole = G_.fill(G_.open(T(sky, v => !v), 5)); { const {lab,out} = G_.comps(hole, 5000); const big = out.sort((a,b)=>b.area-a.area)[0]; hole = big ? T(lab, v => v === big.id) : hole; }
  const sand = G_.open(T(R, (r,i) => r>185 && G[i]>160 && B[i]>140 && r>B[i]+12 && hole[i] && !water[i]), 3);
  const grass = T(G, (g,i) => g>190 && g<232 && R[i]>125 && R[i]<178 && B[i]>45 && B[i]<110);
  const gs = G_.gauss(G, 1.2), hp = new Float32Array(N*N); for (let i=0;i<N*N;i++) hp[i]=Math.abs(G[i]-gs[i]); const e = G_.box(hp, 11);   // 高頻紋理：球道的斜條紋約 1.4，平滑的長草約 0.5，果嶺的格紋約 2.3
  const dark = T(G, (g,i) => !(g>170 && R[i]>120) || B[i]>150), halo = G_.dil(dark, 7);
  const gb = G_.box(grass, 7);
  let green = T(R, (r,i) => r>95 && r<140 && G[i]>168 && G[i]<205 && B[i]>35 && B[i]<90 && hole[i]); const gg = G_.box(green, 7);
  green = G_.fill(G_.close(G_.open(T(gg, (v,i) => v>0.75 && e[i]>1.5), 5), 5));
  let fw = T(gb, (v,i) => v>0.9 && e[i]>0.95 && e[i]<2.3 && !halo[i]); fw = G_.fill(G_.close(G_.close(G_.open(fw, 5), 7), 7)); fw = T(fw, (v,i) => v && !green[i]);
  let tree = T(G, (g,i) => hole[i] && !water[i] && !sand[i] && (g<165 || R[i]<115 || (R[i]>190 && g>190 && B[i]<120))); tree = G_.close(G_.open(tree, 5), 5); tree = T(tree, (v,i) => v && !green[i]);
  return { outline: G_.polys(hole, 5000, 2.0), fairway: G_.polys(fw, 900, 1.6), green: G_.polys(green, 250, 1.2), water: G_.polys(water, 120, 1.2), sand: G_.polys(sand, 40, 1.0), tree: G_.polys(tree, 140, 2.2) }; };
window.G_ = G_;

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
const m = location.hash.match(/golf=([A-Za-z0-9+\/=]+)/);
if (m) { try { box.querySelector('#gfile').style.display = 'none'; set('開始處理…'); await run(JSON.parse(decodeURIComponent(escape(atob(m[1]))))); } catch (e) { set('失敗：' + e); } }
else console.log('[golf] 面板已出現在頁面左上角，請選擇 garmin_export 檔案。');
})();
