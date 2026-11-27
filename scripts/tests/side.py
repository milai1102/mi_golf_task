import asyncio, json
from playwright.async_api import async_playwright
from cadlib import *
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for w,h in ((393,760),(360,640)):
          for cs in ("light","dark"):
            ctx=await b.new_context(viewport={"width":w,"height":h},device_scale_factor=2,color_scheme=cs)
            pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.evaluate("localStorage.setItem('mi-caddie-mode',JSON.stringify({day:new Date().toLocaleDateString('sv'),m:'play',man:1}))"); await pg.reload(); await pg.wait_for_timeout(900)
            async def st():
                return await pg.evaluate("""()=>{const R=e=>e&&!e.hidden&&e.offsetParent?e.getBoundingClientRect():null;const m=document.querySelector('.cad-map');const nx=document.querySelector('.cad-side.nx'),pv=document.querySelector('.cad-side.pv');const o=[];const ov=(a,b)=>a&&b&&a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom;const hs=[...m.querySelectorAll('.hud,.cad-ph,.cad-here,.cad-rs')].map(e=>[e.className,R(e)]);for(const [n,r] of hs){if(ov(R(nx),r))o.push('nx~'+n);if(ov(R(pv),r))o.push('pv~'+n);}return {hole:document.querySelector('.cad-hole b').textContent,nx:!!R(nx),pv:!!R(pv),o,top:[...document.querySelector('.cad-top').children].filter(e=>e.offsetWidth).map(e=>e.className+':'+e.offsetWidth)}}""")
            print(w,h,cs,await st())
            await pg.screenshot(path=f"side_{w}_{cs}.png")
            await pg.click(".cad-side.nx"); await pg.wait_for_timeout(400); print(' after nx',await st())
            await pg.click(".cad-side.nx"); await pg.wait_for_timeout(400)
            await pg.click(".cad-side.pv"); await pg.wait_for_timeout(400); print(' after pv',await st())
            n=await pg.evaluate("document.querySelectorAll('.cad-holes button').length")
            await pg.evaluate(f"document.querySelectorAll('.cad-holes button')[{n-1}].click()"); await pg.wait_for_timeout(400); print(' last',await st())
            if w==393 and cs=="dark": await pg.screenshot(path="side_last.png")
            print(' errs',errs, 'shots', await pg.evaluate("Object.keys((JSON.parse(localStorage.getItem('mi-caddie-shots')||'{}').holes)||{}).length"))
            await ctx.close()
        await b.close()
asyncio.run(main())
