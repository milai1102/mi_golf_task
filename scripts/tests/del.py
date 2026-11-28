import asyncio
from playwright.async_api import async_playwright
d = lambda v: v*180/2**31
TEE=(d(276218362),d(1436102968))
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for sch,W in (("dark",393),("light",360)):
            ctx=await b.new_context(viewport={"width":W,"height":760},device_scale_factor=2,color_scheme=sch,permissions=["geolocation"],geolocation={"latitude":TEE[0],"longitude":TEE[1],"accuracy":5})
            pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(2500)
            for c in ("1W","H"):
                await pg.click(".cad-add"); await pg.wait_for_timeout(300); await pg.click(f'.cad-sheet .clubs3 button[data-c="{c}"]'); await pg.wait_for_timeout(400)
            await pg.locator('.hud.br .dsum').click(); await pg.wait_for_timeout(300)
            await pg.locator('.cad-sheet .shots button').nth(1).click(); await pg.wait_for_timeout(300)
            await pg.screenshot(path=f"/home/claude/shot/del_{sch}{W}.png")
            ov=await pg.evaluate("[...document.querySelectorAll('.cad-sheet .acts3 button')].map(b=>[b.textContent,b.scrollWidth>b.clientWidth])")
            await pg.click('.cad-sheet .acts3 .dl'); await pg.wait_for_timeout(400)
            print(sch,W,ov,'after del:',await pg.locator('.cad-msg').inner_text(),errs)
        await b.close()
asyncio.run(main())
