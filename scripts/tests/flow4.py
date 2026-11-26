import sys; sys.path.insert(0, "/home/claude/shot"); import cadlib
import asyncio, json, glob
from playwright.async_api import async_playwright
f=[x for x in glob.glob('/home/claude/mi_golf_task/data/raw/gps/*.txt') if '032542' in open(x).readline()][0]
H={}; cur=None
for ln in open(f, encoding='utf-8'):
    p=ln.strip().split('|')
    if ln.startswith('H'): cur=p[0]; H[cur]=[]; pin=p[2].split(','); H[cur+'pin']=(int(pin[0])*180/2**31,int(pin[1])*180/2**31); continue
    if cur and len(p)>5:
        a=p[4].split(','); H[cur].append((int(a[0])*180/2**31, int(a[1])*180/2**31))
pts=H['H4']; t5=H['H5'][0]; pin4=H['H4pin']
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width":393,"height":683}, device_scale_factor=2, color_scheme="light", permissions=["geolocation"], geolocation={"latitude":pts[0][0],"longitude":pts[0][1],"accuracy":6})
        pg = await ctx.new_page(); errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
        row = lambda: pg.evaluate("[document.querySelector('.cad-holes .on').textContent, document.querySelector('.cad-msg').textContent, [document.querySelector('.cad-add').textContent, document.querySelector('.cad-finb').textContent].join(' | ')]")
        async def geo(pt): await ctx.set_geolocation({"latitude":pt[0],"longitude":pt[1],"accuracy":6}); await pg.wait_for_timeout(900)
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1500)
        await cadlib.club(pg, "sg"); await geo(pts[1]); await cadlib.club(pg, 2); await geo(pts[2]); await cadlib.club(pg, 6)
        await geo(pin4); print("green", await row()); await pg.screenshot(path="/home/claude/shot/f4a.png")
        await pg.click(".cad-finb"); await pg.wait_for_timeout(300); await pg.screenshot(path="/home/claude/shot/f4b.png")
        await pg.click(".cad-sheet .nums >> nth=0 >> text='3'"); await pg.locator(".cad-sheet .nums").nth(1).get_by_text("2", exact=True).click(); await pg.locator(".cad-sheet .nums").nth(2).get_by_text("5", exact=True).click()
        await pg.wait_for_timeout(200); await pg.screenshot(path="/home/claude/shot/f4c.png"); await pg.click(".cad-sheet .ok"); await pg.wait_for_timeout(300); print("done", await row())
        # hole 5 without scoring? clear putts first to test reminder
        await pg.click(".cad-finb"); await pg.locator(".cad-sheet .nums").nth(1).get_by_text("2", exact=True).click(); await pg.click(".cad-sheet .ok")
        await pg.evaluate("localStorage.setItem('__cad_skew', '400000')"); await geo(t5); print("tee5", await row()); await pg.screenshot(path="/home/claude/shot/f4d.png")
        await pg.click(".cad-msg .cad-undo"); await pg.wait_for_timeout(300); await pg.locator(".cad-sheet .nums").nth(1).get_by_text("3", exact=True).click(); await pg.click(".cad-sheet .ok"); await pg.wait_for_timeout(300)
        await pg.reload(); await pg.wait_for_timeout(1500)
        print("store", await pg.evaluate("localStorage.getItem('mi-caddie-shots')"))
        await pg.click(".cad-card"); await pg.wait_for_timeout(200); print((await pg.inner_text(".cad-sheet")).replace("\n"," ")); await pg.click(".cad-sheet .x")
        print("errs", errs); await b.close()
asyncio.run(main())
