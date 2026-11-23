# 整場回放：用過去實際每一桿的座標當假 GPS，照上場的操作走完整場
# 用法：sim.py <球場英文關鍵字> <球場簡稱> [mode: open|reload|mix] [noise_m]
import asyncio, json, glob, re, sys, random, math, collections
from playwright.async_api import async_playwright
KEY, SHORT = sys.argv[1], sys.argv[2]; MODE = sys.argv[3] if len(sys.argv) > 3 else "mix"; NOISE = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0
R = '/home/claude/mi_golf_task/'
rep = open(R+'reports/caddie.html', encoding='utf-8').read(); D = json.JSONDecoder().raw_decode(rep[rep.index('const REPORT = ')+15:])[0]
sec = [x for x in D['sections'] if x.get('kind') == 'caddie'][0]; C = [x for x in sec['data'] if x['short'] == SHORT][0]
ABR = D['abbr']; BAG = [ABR.get(c, c) for c in sec['model']['bag']]
lab = {L['cid']: (L['label'] or '') for L in C['layouts']}
deg = lambda v: int(v)*180/2**31
cards = []
for f in glob.glob(R+'data/raw/gps/*.txt'):
    L = open(f, encoding='utf-8').read().split('\n')
    if KEY not in L[0]: continue
    hs = []; cur = None
    for ln in L[1:]:
        p = ln.split('|')
        if ln.startswith('H'):
            m = re.search(r'gid(\d+)/hole(\d+)', p[3]); pin = p[2].split(',')
            cur = dict(cid=m.group(1), h=int(m.group(2)), par=int(p[1]), pin=(deg(pin[0]), deg(pin[1])), shots=[]); hs.append(cur)
        elif cur and len(p) > 5 and p[2] != 'PUTT':
            a = p[4].split(','); cur['shots'].append(dict(club=p[1], ll=(deg(a[0]), deg(a[1])), px=(int(a[2]), int(a[3])), m=float(p[3] or 0), t=int(p[6]) if len(p) > 6 and p[6] else 0))
    cards.append((L[0].split('|')[4], hs))
cards.sort()
days = collections.OrderedDict()
for t, hs in cards:
    if len(sys.argv) > 5 and sys.argv[5] not in t: continue
    days.setdefault(t[:10], []).extend(hs)
def jit(ll):
    return (ll[0] + random.gauss(0, NOISE)/111200, ll[1] + random.gauss(0, NOISE)/(111200*math.cos(math.radians(ll[0]))))
async def run(day, holes, br):
    random.seed(hash(day) % 1000)
    ctx = await br.new_context(viewport={"width": 393, "height": 683}, permissions=["geolocation"], geolocation={"latitude": 25.03, "longitude": 121.56, "accuracy": 8})
    pg = await ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(700)
    state = lambda: pg.evaluate("""(()=>{const q=s=>document.querySelector(s),sl=document.querySelectorAll('.cad-top select')[1];return {c:document.querySelectorAll('.cad-top select')[0].selectedOptions[0].textContent,l:sl&&sl.style.display!=='none'?sl.selectedOptions[0].textContent:'',h:+q('.cad-holes .on').textContent,msg:q('.cad-msg').textContent,row:[...document.querySelectorAll('.cad-next button')].map(b=>b.textContent),sg:(q('.cad-next button.sg')||{}).textContent||'',live:q('.cad-here').classList.contains('live')}})()""")
    issues = []; n_shots = 0; n_ok_hole = 0; sugsame = 0; subst = 0
    owe = None; clock = [0]; t0 = [0]; hints = []
    async def fill(H0):
        on = min(9, max(1, len(H0['shots'])))
        await pg.locator(".cad-sheet .nums").nth(0).get_by_text(str(on), exact=True).click(); await pg.locator(".cad-sheet .nums").nth(1).get_by_text("2", exact=True).click(); await pg.locator(".cad-sheet .nums").nth(2).get_by_text("5", exact=True).click()
        hd = await pg.inner_text(".cad-sheet .hd"); await pg.click(".cad-sheet .ok"); await pg.wait_for_timeout(150); return hd
    for hn, H in enumerate(holes):
        want = (lab[H['cid']], H['h']); reload_mode = MODE == "reload" or (MODE == "mix" and hn % 2 == 1)
        for i, s in enumerate(H['shots']):
            clock[0] = max(clock[0] + (300000 if i == 0 else 40000), s['t'] or 0) if clock[0] else (s['t'] or 1); skew = clock[0] - t0[0] if t0[0] else 0
            if not t0[0]: t0[0] = clock[0]
            await pg.evaluate("k => localStorage.setItem('__cad_skew', k)", str(int(skew)))
            ll = jit(s['ll']); await ctx.set_geolocation({"latitude": ll[0], "longitude": ll[1], "accuracy": 5 + random.random()*6})
            if reload_mode: await pg.reload(); await pg.wait_for_timeout(1200)
            else: await pg.wait_for_timeout(1000)
            st = await state(); n_shots += 1
            if (st['l'], st['h']) != want:
                hb = pg.locator(".cad-msg .cad-undo", has_text=f"切到第 {H['h']} 洞")
                if await hb.count(): await hb.click(); await pg.wait_for_timeout(200); st = await state(); hints.append(f"{want}")
            if (st['l'], st['h']) != want or not st['c'].startswith(SHORT): issues.append(f"{day} {want} 第{i+1}桿：畫面在 {st['c']} {st['l']} {st['h']}｜{st['msg']}")
            else: n_ok_hole += 1
            if owe is not None:
                b = pg.locator(".cad-msg .cad-undo", has_text="補記"); hd = ""
                if await b.count(): await b.click(); await pg.wait_for_timeout(150); hd = await fill(owe)
                else:
                    b2 = pg.locator(".cad-ed button", has_text="補記")
                    if await b2.count(): await b2.first.click(); await pg.wait_for_timeout(150); hd = await fill(owe); issues.append(f"{day} {want}：補記的提醒不在訊息列（{st['msg']}），用下面的按鈕補")
                    elif owe['shots']: issues.append(f"{day} {want}：上一洞沒有補記的入口")
                if hd and f"第 {owe['h']} 洞" not in hd: issues.append(f"{day} {want}：補記開到別的洞（{hd}）")
                owe = None
            ab = ABR.get(s['club'], s['club'])
            if ab not in BAG: ab = 'H'; subst += 1
            st = await state()
            if ab not in st['row'] and '其他' in st['row']: await pg.locator(".cad-next button", has_text="其他").click(); await pg.wait_for_timeout(100); st = await state()
            if ab not in st['row']: issues.append(f"{day} {want} 第{i+1}桿：沒有球桿列（{st['row']}）"); continue
            if st['sg'] == ab: sugsame += 1
            await pg.locator(".cad-next button", has_text=re.compile("^"+re.escape(ab)+"$")).first.click(); await pg.wait_for_timeout(150)
        if hn % 2 == 0 or hn == len(holes) - 1:
            clock[0] += 240000; await pg.evaluate("k => localStorage.setItem('__cad_skew', k)", str(int(clock[0] - t0[0])))
            ll = jit(H['pin']); await ctx.set_geolocation({"latitude": ll[0], "longitude": ll[1], "accuracy": 6}); await pg.wait_for_timeout(1000)
            st = await state(); fin = pg.locator(".cad-next button.fin")
            if await fin.count() == 0: issues.append(f"{day} {want} 果嶺：上面沒有「記錄成績」（{st['row']}），改按圖上的"); fin = pg.locator(".cad-fin")
            await fin.click(); await pg.wait_for_timeout(150); await fill(H)
        else: owe = H
    store = json.loads(await pg.evaluate("localStorage.getItem('mi-caddie-shots')") or '{}').get('holes', {})
    run_txt = (await pg.inner_text(".cad-run")).replace("\n", " ")
    # 對照：每洞記到幾桿、位置差幾像素、距離差幾碼
    lidx = {L['cid']: j for j, L in enumerate(C['layouts'])}; miss = 0; pxerr = []; yderr = []
    for H in holes:
        j = lidx[H['cid']]; r = store.get(f"{SHORT}|{j}|{H['h']}", {}); S = r.get('s', []); k = C['layouts'][j]['holes'][H['h']-1]['k']
        if len(S) != len(H['shots']): miss += 1; issues.append(f"{day} {lab[H['cid']]}{H['h']}：實際 {len(H['shots'])} 桿，記到 {len(S)} 桿")
        for a, b in zip(S, H['shots']): pxerr.append(math.hypot(a['p'][0]-b['px'][0], a['p'][1]-b['px'][1])*k)
        if r.get('putts') is None: issues.append(f"{day} {lab[H['cid']]}{H['h']}：成績沒記到")
    lg = json.loads(await pg.evaluate("localStorage.getItem('mi-caddie-log')") or '[]'); ev = collections.Counter(e['ev'] for e in lg)
    pxerr.sort()
    print(f"== {day} {len(holes)} 洞 {n_shots} 桿｜洞正確 {n_ok_hole}/{n_shots}｜桿數不符的洞 {miss}｜位置誤差(碼) 中位數 {pxerr[len(pxerr)//2]:.1f} 最大 {pxerr[-1]:.1f}｜建議＝實際用的 {sugsame}/{n_shots}（球袋外改按 H：{subst}）｜上方小計：{run_txt}｜紀錄 {len(lg)} 筆 {dict(ev)}｜要按一下才換洞 {len(hints)} 次 {hints}｜JS 錯誤 {errs}")
    for x in issues: print("   !", x)
    await ctx.close(); return len(issues)
async def main():
    async with async_playwright() as p:
        br = await p.chromium.launch(); tot = 0
        for day, holes in days.items(): tot += await run(day, holes, br)
        print("問題總數", tot); await br.close()
asyncio.run(main())
