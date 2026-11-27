import asyncio, sys
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for w,h in ((1280,800),(1440,900)):
          for cs in ("dark","light"):
            ctx = await b.new_context(viewport={"width": w, "height": h}, color_scheme=cs)
            pg = await ctx.new_page(); errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html")
            await pg.evaluate("localStorage.setItem('mi-caddie-mode',JSON.stringify({day:new Date().toLocaleDateString('sv'),m:'play',man:1}))"); await pg.reload(); await pg.wait_for_timeout(1200)
            r = await pg.evaluate("""()=>{const R=s=>{const e=document.querySelector(s);if(!e)return null;const b=e.getBoundingClientRect();return [Math.round(b.x),Math.round(b.y),Math.round(b.width),Math.round(b.height)]};return {map:R('.cad-map'),add:R('.cad-add'),card:R('.cad-card'),fin:R('.cad-finb'),top:R('.cad-top'),bar:R('.cad-bar'),sh:document.documentElement.scrollHeight}}""")
            print(w,h,cs,r,errs)
            await pg.screenshot(path=f"/home/claude/shot/pc_{w}_{cs}.png")
            await ctx.close()
        await b.close()
asyncio.run(main())
