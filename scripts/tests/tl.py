import asyncio
from playwright.async_api import async_playwright
d = lambda v: v*180/2**31
TEE=(d(276218362),d(1436102968)); S2=(d(276233641),d(1436103681))
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for sch,W in [("dark",393),("light",360)]:
            ctx=await b.new_context(viewport={"width":W,"height":760},device_scale_factor=2,color_scheme=sch,permissions=["geolocation"],geolocation={"latitude":TEE[0],"longitude":TEE[1],"accuracy":5})
            pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(2500)
            await pg.click(".cad-add"); await pg.wait_for_timeout(300); await pg.click('.cad-sheet .clubs3 button[data-c="1W"]'); await pg.wait_for_timeout(400)
            await ctx.set_geolocation({"latitude":S2[0],"longitude":S2[1],"accuracy":6}); await pg.wait_for_timeout(1500)
            await pg.click(".cad-here"); await pg.wait_for_timeout(2500)
            await pg.screenshot(path=f"/home/claude/shot/tl_{sch}{W}.png"); print(errs); await ctx.close()
asyncio.run(main())
