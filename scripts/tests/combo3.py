import asyncio, datetime
from playwright.async_api import async_playwright
day=datetime.date.today().isoformat()
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); ctx=await b.new_context(viewport={"width":393,"height":760},device_scale_factor=2,color_scheme="dark")
        pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1200)
        await pg.evaluate("()=>{localStorage.clear();localStorage.__cad_noask=1}"); await pg.reload(); await pg.wait_for_timeout(1200)
        await pg.click(".cad-course"); await pg.wait_for_timeout(300)
        await pg.click(".combo .cname"); await pg.wait_for_timeout(200); await pg.screenshot(path="/home/claude/shot/cs_list.png"); await pg.locator('.clist .cr:has-text("斑芝花")').click(); await pg.wait_for_timeout(300)
        await pg.click('.combo .cbr >> nth=1 >> button >> nth=0'); await pg.wait_for_timeout(500)
        print("open:", await pg.locator(".cad-sheet:not([hidden])").count(), (await pg.locator(".cad-top").inner_text()).replace("\n"," "))
        await pg.click('.combo .cbr >> nth=2 >> button >> nth=1'); await pg.wait_for_timeout(400)
        await pg.screenshot(path="/home/claude/shot/combo3.png")
        await pg.click(".cad-sheet .hd .x"); await pg.wait_for_timeout(300)
        print("closed top:", (await pg.locator(".cad-top").inner_text()).replace("\n"," "), errs)
asyncio.run(main())
