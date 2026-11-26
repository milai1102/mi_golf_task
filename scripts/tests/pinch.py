import asyncio
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); ctx=await b.new_context(viewport={"width":393,"height":760},has_touch=True,is_mobile=True,device_scale_factor=2); pg=await ctx.new_page(); await pg.add_init_script("(()=>{const d=new Date(),p=v=>String(v).padStart(2,'0');if(!localStorage.getItem('mi-caddie-mode'))localStorage.setItem('mi-caddie-mode',JSON.stringify({day:d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate()),m:'play',man:true}))})()")
        errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(800)
        vb=lambda: pg.evaluate("document.querySelector('.cad-map > svg').getAttribute('viewBox')")
        m=await pg.locator(".cad-map > svg").bounding_box(); cx,cy=m["x"]+m["width"]/2,m["y"]+m["height"]/2
        cdp=await ctx.new_cdp_session(pg); print("start",await vb())
        tp=lambda d:[{"x":cx-d,"y":cy,"id":1},{"x":cx+d,"y":cy,"id":2}]
        await cdp.send("Input.dispatchTouchEvent",{"type":"touchStart","touchPoints":tp(30)})
        for d in range(35,121,10): await cdp.send("Input.dispatchTouchEvent",{"type":"touchMove","touchPoints":tp(d)}); await pg.wait_for_timeout(30)
        await cdp.send("Input.dispatchTouchEvent",{"type":"touchEnd","touchPoints":[]}); await pg.wait_for_timeout(400)
        print("pinch",await vb(), await pg.locator(".hud.bl").count(), await pg.evaluate("scrollY"), errs)
        await pg.screenshot(path="pinch.png"); await b.close()
asyncio.run(main())
