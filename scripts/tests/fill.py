import asyncio, sys
sys.path.insert(0,'/home/claude/shot')
from cadlib import club, hole
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); ctx=await b.new_context(viewport={"width":393,"height":760},color_scheme="dark")
        pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html")
        await pg.evaluate("localStorage.setItem('__cad_noask','1');localStorage.setItem('mi-caddie-mode',JSON.stringify({day:new Date().toLocaleDateString('sv'),m:'play',man:1}))")
        await pg.reload(); await pg.wait_for_timeout(1000)
        await hole(pg,1)
        await club(pg,"1W"); await pg.wait_for_timeout(5200); await club(pg,"H")
        await pg.click(".cad-finb"); await pg.wait_for_timeout(300)
        await pg.locator(".cad-sheet .nums").nth(0).get_by_text("5", exact=True).click()
        await pg.locator(".cad-sheet .nums").nth(1).get_by_text("2", exact=True).click()
        await pg.click(".cad-sheet .ok"); await pg.wait_for_timeout(400)
        print("msg:", await pg.inner_text(".cad-msg"))
        print("card:", (await pg.inner_text(".cad-card")).replace("\n"," "))
        await pg.screenshot(path="/home/claude/shot/fill0.png")
        await pg.click(".cad-msg button >> text=現在改"); await pg.wait_for_timeout(500)
        print("bar:", (await pg.inner_text(".cad-fill")).replace("\n"," | "))
        await pg.screenshot(path="/home/claude/shot/fill1.png")
        # pick club and tap map
        await pg.click(".cad-fill .fc button >> text=P"); 
        mb=await pg.evaluate("JSON.stringify(document.querySelector('.cad-map').getBoundingClientRect())")
        import json; r=json.loads(mb)
        await pg.mouse.click(r['x']+r['width']*0.4, r['y']+r['height']*0.75); await pg.wait_for_timeout(500)
        print("bar2:", (await pg.inner_text(".cad-fill")).replace("\n"," | "))
        await pg.screenshot(path="/home/claude/shot/fill2.png")
        n=0
        while await pg.locator(".cad-fill").count() and n<6:
            await pg.click(".cad-fill .ok"); await pg.wait_for_timeout(400); n+=1
        print("after done msg:", await pg.inner_text(".cad-msg"), "steps", n)
        await pg.click(".cad-card"); await pg.wait_for_timeout(300)
        await pg.locator(".cad-sheet .fsc .c").first.click(); await pg.wait_for_timeout(300)
        print("hole:", (await pg.inner_text(".cad-sheet")).replace("\n"," | ")[:400])
        await pg.screenshot(path="/home/claude/shot/fill3.png")
        await pg.reload(); await pg.wait_for_timeout(1000)
        print("reload hud:", (await pg.inner_text(".cad-map .hud.br")).replace("\n"," "))
        print("errs",errs); await b.close()
asyncio.run(main())
