import asyncio
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); ctx=await b.new_context(viewport={"width":393,"height":800},device_scale_factor=2,color_scheme="dark")
        pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/index.html"); await pg.wait_for_timeout(1500)
        await pg.locator(".rcard").first.click(); await pg.wait_for_timeout(600)
        tabs=await pg.locator(".dtabs").first.inner_text(); print("tabs:",tabs.replace("\n"," "))
        for i,nm in enumerate(["開球","揮桿圖"]):
            await pg.locator(f'.dtabs button:has-text("{nm}")').first.click(); await pg.wait_for_timeout(800)
            el=pg.locator(".dtabs").first
            box=await el.bounding_box(); await pg.evaluate(f"window.scrollTo(0,{box['y']}+window.scrollY-60)"); await pg.wait_for_timeout(300)
            await pg.screenshot(path=f"/home/claude/shot/site_{i}.png")
        print(errs)
asyncio.run(main())
