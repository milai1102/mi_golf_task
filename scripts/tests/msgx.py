import asyncio, datetime
from playwright.async_api import async_playwright
day=datetime.date.today().isoformat()
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for sch in ["dark","light"]:
            ctx=await b.new_context(viewport={"width":393,"height":760},device_scale_factor=2,color_scheme=sch,permissions=["geolocation"],geolocation={"latitude":25.0552,"longitude":121.45,"accuracy":10})
            pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1500)
            await pg.evaluate("()=>{localStorage.__cad_noask=1}")
            await pg.click(".cad-here"); await pg.wait_for_timeout(2000)
            t=await pg.locator(".cad-msg").inner_text(); await pg.screenshot(path=f"/home/claude/shot/msgx_{sch}.png")
            await pg.click(".cad-msg"); await pg.wait_for_timeout(300)
            t2=await pg.locator(".cad-msg").inner_text()
            print(sch, repr(t[:40]), "after tap:", repr(t2), errs); await ctx.close()
asyncio.run(main())
