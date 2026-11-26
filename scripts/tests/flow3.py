import sys; sys.path.insert(0, "/home/claude/shot"); import cadlib
# 沒有定位權限（模擬器用法）：點圖、按球桿、重新整理、換洞、編輯
import asyncio, json
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for scheme in ("dark","light"):
            ctx = await b.new_context(viewport={"width":393,"height":683}, device_scale_factor=2, color_scheme=scheme)
            await ctx.add_init_script("if(!localStorage.getItem('mi-caddie')){}")
            pg = await ctx.new_page(); errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(600)
            await pg.evaluate("""(()=>{const s=document.querySelector('.cad-top select');const o=[...s.options].find(o=>o.textContent.startsWith('南寶'));s.value=o.value;s.dispatchEvent(new Event('change'));})()""")
            await pg.wait_for_timeout(500)
            st = lambda: pg.evaluate("""(()=>{const q=s=>document.querySelector(s);return [q('.cad-holes .on').textContent, q('.cad-msg').textContent, [document.querySelector('.cad-edit').hidden?'':document.querySelector('.cad-edit').textContent, document.querySelector('.cad-add').textContent, document.querySelector('.cad-finb').textContent].join(' | '), JSON.stringify((JSON.parse(localStorage.getItem('mi-caddie-shots')||'{}').holes)||{})]})()""")
            print(scheme, "start", await st())
            await cadlib.club(pg, "sg"); await pg.wait_for_timeout(200); print("tee tap", await st())
            bx = await pg.locator(".cad-map svg").first.bounding_box()
            await pg.mouse.click(bx["x"]+bx["width"]*0.5, bx["y"]+bx["height"]*0.55); await pg.wait_for_timeout(400); print("map tap", await st())
            await cadlib.club(pg, 2); await pg.wait_for_timeout(200); print("2nd", await st())
            await pg.reload(); await pg.wait_for_timeout(900); print("reload", await st())
            assert len(json.loads((await st())[3])["南寶|0|1"]["s"]) == 2, "two shots"
            await pg.mouse.click(bx["x"]+bx["width"]*0.5, bx["y"]+bx["height"]*0.3); await pg.wait_for_timeout(400)
            await cadlib.club(pg, 6); await pg.wait_for_timeout(300); print("3rd", await st())
            await pg.screenshot(path=f"/home/claude/shot/f3_{scheme}.png")
            await pg.click(".cad-undo"); await pg.wait_for_timeout(300); print("undo", await st())
            await cadlib.hole(pg, 2); await pg.wait_for_timeout(500); print("hole2", await st())
            await cadlib.hole(pg, 1); await pg.wait_for_timeout(500); print("hole1", await st())
            await pg.click(".cad-card"); await pg.wait_for_timeout(200); print("round sheet", (await pg.inner_text(".cad-sheet")).replace("\n"," "))
            await pg.locator(".cad-sheet .shots button").first.click(); await pg.wait_for_timeout(200); await pg.click(".cad-sheet .del"); await pg.wait_for_timeout(300); print("del 1st", await st())
            await pg.screenshot(path=f"/home/claude/shot/f3_{scheme}_full.png", full_page=True)
            print("errs", errs); await ctx.close()
        await b.close()
asyncio.run(main())
