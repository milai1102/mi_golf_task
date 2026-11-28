import asyncio
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); ctx=await b.new_context(viewport={"width":393,"height":760},device_scale_factor=2,color_scheme="dark")
        pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1200)
        await pg.evaluate("()=>{localStorage.clear();localStorage.__cad_noask=1}"); await pg.reload(); await pg.wait_for_timeout(1200)
        shots=[]
        await pg.click(".cad-course"); await pg.wait_for_timeout(300); await pg.screenshot(path="/home/claude/shot/cs_main.png")
        for nm in ["南一","大衛營","第一"]:
            await pg.click(".combo .cname"); await pg.wait_for_timeout(200)
            if nm=="南一": await pg.screenshot(path="/home/claude/shot/cs_list.png")
            await pg.locator(f'.clist .cr:has-text("{nm}")').click(); await pg.wait_for_timeout(300)
            print(nm, (await pg.locator(".combo").inner_text()).replace("\n"," "))
            await pg.screenshot(path=f"/home/claude/shot/cs_{nm}.png")
        print(errs)
asyncio.run(main())
