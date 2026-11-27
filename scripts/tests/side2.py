import asyncio
from playwright.async_api import async_playwright
from cadlib import *
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for h in (640,760):
            ctx=await b.new_context(viewport={"width":360,"height":h},device_scale_factor=2,color_scheme="dark")
            pg=await ctx.new_page()
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.evaluate("localStorage.setItem('mi-caddie-mode',JSON.stringify({day:new Date().toLocaleDateString('sv'),m:'play',man:1}));localStorage.setItem('__cad_tap','1')"); await pg.reload(); await pg.wait_for_timeout(900)
            for i in range(6):
                await club(pg,"H"); await pg.wait_for_timeout(5300)
            await pg.click(".cad-side.nx"); await pg.click(".cad-side.pv"); await pg.wait_for_timeout(500)
            r=await pg.evaluate("""()=>{const a=document.querySelector('.cad-side.nx').getBoundingClientRect(),b=document.querySelector('.hud.br').getBoundingClientRect();return [Math.round(a.bottom),Math.round(b.top),document.querySelector('.hud.br').scrollHeight>document.querySelector('.hud.br').clientHeight]}""")
            print(h,r); await pg.screenshot(path=f"side2_{h}.png"); await ctx.close()
        await b.close()
asyncio.run(main())
