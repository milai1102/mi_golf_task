import sys; sys.path.insert(0, "/home/claude/shot"); import cadlib
import asyncio, json, glob
from playwright.async_api import async_playwright
f=[x for x in glob.glob('/home/claude/mi_golf_task/data/raw/gps/*.txt') if '032542' in open(x).readline()][0]
H={}; cur=None
for ln in open(f, encoding='utf-8'):
    p=ln.strip().split('|')
    if ln.startswith('H'): cur=p[0]; H[cur]=[]; continue
    if cur and len(p)>5:
        a=p[4].split(','); H[cur].append((int(a[0])*180/2**31, int(a[1])*180/2**31))
pts=H['H4']; t5=H['H5'][0]
DELAY="""(()=>{const g=navigator.geolocation,w=g.watchPosition.bind(g);g.watchPosition=(ok,er,o)=>w(p=>setTimeout(()=>ok(p),window.__D||0),er,o);})();"""
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width":393,"height":683}, device_scale_factor=2, color_scheme="light", permissions=["geolocation"], geolocation={"latitude":pts[0][0],"longitude":pts[0][1],"accuracy":6})
        await ctx.add_init_script("localStorage.setItem('__cad_tap','1')")
        await ctx.add_init_script(DELAY)
        pg = await ctx.new_page(); errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
        st = lambda: pg.evaluate("""(()=>{const q=s=>document.querySelector(s);return [q('.cad-holes .on')&&q('.cad-holes .on').textContent, q('.cad-msg').textContent, [document.querySelector('.cad-add').textContent, document.querySelector('.cad-finb').textContent].join(' | '), q('.cad-here').className, (JSON.parse(localStorage.getItem('mi-caddie-shots')||'{}').holes)||{}]})()""")
        async def show(tag):
            s = await st(); print(f"[{tag}] hole={s[0]} msg={s[1]!r}\n     row={s[2]} btn={s[3]}\n     store={json.dumps(s[4],ensure_ascii=False)}")
        async def geo(pt, acc=6, wait=900):
            await ctx.set_geolocation({"latitude":pt[0],"longitude":pt[1],"accuracy":acc}); await pg.wait_for_timeout(wait)
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1500)
        await show("1 open at tee 4 (auto)")
        await cadlib.club(pg, "sg"); await pg.wait_for_timeout(200); await show("2 tap suggested")
        await cadlib.club(pg, 2); await pg.wait_for_timeout(200); await show("3 change club same spot")
        await cadlib.club(pg, 1); await pg.wait_for_timeout(200)
        await geo(pts[1]); await show("4 walked to ball 2 (auto)"); await pg.screenshot(path="/home/claude/shot/f2a.png")
        await cadlib.club(pg, 2); await pg.wait_for_timeout(200); await show("5 tap H"); await pg.screenshot(path="/home/claude/shot/f2b.png")
        # reload with slow GPS, tap club before fix => pend
        await ctx.set_geolocation({"latitude":pts[2][0],"longitude":pts[2][1],"accuracy":6})
        await ctx.add_init_script("window.__D=2500")
        await pg.reload(); await pg.wait_for_timeout(700); await show("6 reload, before fix")
        await cadlib.club(pg, 4); await pg.wait_for_timeout(300); await show("6b tapped 8i before fix")
        await pg.wait_for_timeout(2800); await show("6c after fix (pend settled)")
        await ctx.add_init_script("window.__D=0"); await pg.evaluate("window.__D=0")
        await geo((pts[2][0]+0.00002, pts[2][1])); await show("7 jitter 2m (no change)")
        await pg.mouse.click(200, 400); await pg.wait_for_timeout(300); await show("8 map tap while live")
        await geo(pts[2], acc=80); await show("9 inaccurate fix")
        await geo((pts[2][0]+0.02, pts[2][1])); await show("10 off course")
        await pg.evaluate("localStorage.setItem('__cad_skew', '400000')"); await geo(pts[2]); await geo(t5); await show("11 tee of hole 5"); 
        await cadlib.hole(pg, 8); await pg.wait_for_timeout(400); await show("12 browse hole 8")
        await geo((t5[0]+0.0002, t5[1])); await show("12b fix while browsing")
        await pg.click(".cad-here"); await pg.wait_for_timeout(1200); await show("13 locate button"); await pg.screenshot(path="/home/claude/shot/f2c.png")
        await cadlib.hole(pg, 4); await pg.wait_for_timeout(400); await show("14 back to hole 4 view")
        await pg.click(".cad-card"); await pg.wait_for_timeout(200); print((await pg.inner_text(".cad-sheet")).replace("\n"," ")); await pg.click(".cad-sheet .x")
        await pg.screenshot(path="/home/claude/shot/f2d.png", full_page=True)
        lg = json.loads(await pg.evaluate("localStorage.getItem('mi-caddie-log')"))
        from collections import Counter
        print(Counter(e["ev"] for e in lg)); 
        for e in lg:
            if e["ev"]=="shot": print(e)
        print("errs", errs); await b.close()
asyncio.run(main())
