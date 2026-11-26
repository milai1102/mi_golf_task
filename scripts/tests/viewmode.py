import asyncio
from playwright.async_api import async_playwright
import sys; sys.path.insert(0, "/home/claude/shot"); from cadlib import hole
TEE = [23.152308, 120.372705]   # 11/26 南寶 A1 發球台附近
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for sch in ["dark", "light"]:
            # 1. 不在球場（台北）→ 看球道
            ctx = await b.new_context(viewport={"width":393,"height":760}, device_scale_factor=2, color_scheme=sch, permissions=["geolocation"], geolocation={"latitude":25.03,"longitude":121.56,"accuracy":8})
            pg = await ctx.new_page(); errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1500)
            await pg.evaluate("""() => { const s=[...document.querySelectorAll('select')]; const o=[...s[0].options].findIndex(x=>x.textContent.includes('南寶')); s[0].selectedIndex=o; s[0].dispatchEvent(new Event('change')); }""")
            await pg.wait_for_timeout(700)
            st = "() => ({view: document.querySelector('.cad-view')!=null, add: getComputedStyle(document.querySelector('.cad-gps')).display, bar: getComputedStyle(document.querySelector('.cad-modebar')).display, card: getComputedStyle(document.querySelector('.cad-card')).display, tl: document.querySelector('.hud.tl b')?.textContent, mapH: Math.round(document.querySelector('.cad-map').getBoundingClientRect().height)})"
            print(sch, "台北", await pg.evaluate(st))
            await pg.screenshot(path=f"/tmp/vm1_{sch}.png")
            # tap on map moves ball
            box = await pg.locator(".cad-map svg").bounding_box()
            await pg.mouse.click(box["x"]+box["width"]*0.5, box["y"]+box["height"]*0.45); await pg.wait_for_timeout(600)
            print("  tap", await pg.evaluate(st), "ins?", await pg.locator(".cad-ins").count())
            await pg.screenshot(path=f"/tmp/vm2_{sch}.png")
            # start recording manually
            await pg.click(".cad-modebar button"); await pg.wait_for_timeout(500)
            print("  開始記錄", await pg.evaluate(st))
            await pg.reload(); await pg.wait_for_timeout(1200); print("  重新整理", await pg.evaluate(st))
            print("  errs", errs); await ctx.close()
        # 2. 在球場 → 自動切到記錄
        ctx = await b.new_context(viewport={"width":393,"height":760}, permissions=["geolocation"], geolocation={"latitude":TEE[0],"longitude":TEE[1],"accuracy":5})
        pg = await ctx.new_page(); errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(3000)
        print("在球場", await pg.evaluate(st), await pg.locator(".cad-msg").text_content())
        # 3. 手動切回看球道，GPS 不再切回
        await pg.click(".cad-card"); await pg.wait_for_timeout(300); await pg.click(".cad-tomode"); await pg.wait_for_timeout(300)
        await ctx.set_geolocation({"latitude":TEE[0]+0.0003,"longitude":TEE[1],"accuracy":5}); await pg.wait_for_timeout(2500)
        print("手動看球道後", await pg.evaluate(st))
        await pg.reload(); await pg.wait_for_timeout(2500); print("  重新整理", await pg.evaluate(st)); print("errs", errs)
        await b.close()
asyncio.run(main())
