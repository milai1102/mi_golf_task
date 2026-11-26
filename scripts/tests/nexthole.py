import asyncio, sys
from playwright.async_api import async_playwright
d = lambda v: v*180/2**31
TEE=(d(276218362),d(1436102968)); S2=(d(276233641),d(1436103681)); S3=(d(276242542),d(1436101041))
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for sch in ("dark","light"):
            ctx=await b.new_context(viewport={"width":393,"height":700},device_scale_factor=2,color_scheme=sch,permissions=["geolocation"],geolocation={"latitude":TEE[0],"longitude":TEE[1],"accuracy":5})
            pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(2500)
            await pg.click(".cad-add"); await pg.wait_for_timeout(300); await pg.click('.cad-sheet .clubs3 button[data-c="1W"]'); await pg.wait_for_timeout(400)
            await ctx.set_geolocation({"latitude":S3[0],"longitude":S3[1],"accuracy":6}); await pg.wait_for_timeout(5000)
            await pg.click(".cad-add"); await pg.wait_for_timeout(300); await pg.click('.cad-sheet .clubs3 button[data-c="A"]'); await pg.wait_for_timeout(400)
            await pg.click(".cad-finb"); await pg.wait_for_timeout(300)
            await pg.locator('.cad-sheet .nums').nth(0).get_by_text("2",exact=True).click(); await pg.locator('.cad-sheet .nums').nth(1).get_by_text("2",exact=True).click()
            await pg.click('.cad-sheet .ok'); await pg.wait_for_timeout(500)
            print(sch,'add:',await pg.inner_text('.cad-add'),'| fin:',(await pg.inner_text('.cad-finb')).replace('\n',''))
            await pg.screenshot(path=f"nx_{sch}_1.png")
            await pg.click(".cad-finb"); await pg.wait_for_timeout(300); await pg.screenshot(path=f"nx_{sch}_2.png")
            print(' hole sheet:', (await pg.inner_text('.cad-sheet .hd')).replace('\n',' '), '| acts:', await pg.locator('.cad-sheet .acts button').all_inner_texts())
            await pg.click('.cad-sheet .x'); await pg.wait_for_timeout(200)
            await pg.click(".cad-card"); await pg.wait_for_timeout(300)
            bb=await pg.locator('.cad-sheet .box').bounding_box(); print(' round box top',bb['y'],'h',bb['height'],'vh',700)
            await pg.screenshot(path=f"nx_{sch}_3.png"); await pg.click('.cad-sheet .x'); await pg.wait_for_timeout(200)
            await pg.click(".cad-add"); await pg.wait_for_timeout(800)
            print(' after next:', await pg.inner_text('.cad-hole'), '|', await pg.inner_text('.cad-add'), errs); await ctx.close()
        await b.close()
asyncio.run(main())
