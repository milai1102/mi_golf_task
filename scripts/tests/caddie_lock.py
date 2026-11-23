import sys; sys.path.insert(0, "/home/claude/shot"); import cadlib
import asyncio, json, glob
from playwright.async_api import async_playwright
f=[x for x in glob.glob('/home/claude/mi_golf_task/data/raw/gps/*.txt') if '032542' in open(x).readline()][0]
H={}; cur=None
for ln in open(f, encoding='utf-8'):
    p=ln.strip().split('|')
    if ln.startswith('H'): cur=p[0]; H[cur]=[]; continue
    if cur and len(p)>5: a=p[4].split(','); H[cur].append((int(a[0])*180/2**31, int(a[1])*180/2**31))
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width":393,"height":683}, permissions=["geolocation"], geolocation={"latitude":H['H5'][0][0],"longitude":H['H5'][0][1],"accuracy":6})
        pg = await ctx.new_page(); errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
        st = lambda: pg.evaluate("[document.querySelector('.cad-holes .on').textContent, document.querySelector('.cad-msg').textContent]")
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1500); print("open", await st())
        await pg.click(".cad-holes .fchip:nth-of-type(8)"); await pg.wait_for_timeout(400); print("pick 8", await st())
        await cadlib.club(pg, "H"); await pg.wait_for_timeout(1200); print("tap H", await st())
        await ctx.set_geolocation({"latitude":H['H5'][1][0],"longitude":H['H5'][1][1],"accuracy":6}); await pg.wait_for_timeout(1200); print("moved", await st())
        await pg.reload(); await pg.wait_for_timeout(1500); print("reload", await st())
        await cadlib.club(pg, "A"); await pg.wait_for_timeout(1200)
        print(json.dumps(json.loads(await pg.evaluate("localStorage.getItem('mi-caddie-shots')"))['holes'], ensure_ascii=False))
        lg = json.loads(await pg.evaluate("localStorage.getItem('mi-caddie-log')"))
        for e in lg:
            if e['ev'] in ('shot','shotgps'): print(e['ev'], e.get('h'), e.get('club'), e.get('fix'), e.get('lat'), e.get('lon'), e.get('acc'), e.get('err'))
        await pg.click(".cad-here"); await pg.wait_for_timeout(300); await ctx.set_geolocation({"latitude":H['H5'][0][0],"longitude":H['H5'][0][1],"accuracy":6}); await pg.wait_for_timeout(1300); print("locate btn", await st())
        print("errs", errs); await b.close()
asyncio.run(main())
