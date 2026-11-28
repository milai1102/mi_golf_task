import asyncio
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for pg_name in ["index","data","caddie"]:
            ctx=await b.new_context(viewport={"width":393,"height":760},color_scheme="light")
            pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
            await pg.goto(f"http://localhost:8765/reports/{pg_name}.html"); await pg.wait_for_timeout(1200)
            await pg.mouse.wheel(0,600); await pg.wait_for_timeout(400)
            r=await pg.evaluate("JSON.stringify(document.querySelector('.ptabs').getBoundingClientRect())")
            print(pg_name, r, await pg.evaluate("document.querySelector('.ptabs .on, .ptabs [aria-current]') ? (document.querySelector('.ptabs .on, .ptabs [aria-current]').textContent) : 'none'"), errs)
            await pg.screenshot(path=f"/home/claude/shot/tab_{pg_name}.png")
            await ctx.close()
        await b.close()
asyncio.run(main())
