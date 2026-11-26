import asyncio
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); pg=await b.new_page(viewport={"width":393,"height":760},device_scale_factor=2,color_scheme="dark"); await pg.add_init_script("(()=>{const d=new Date(),p=v=>String(v).padStart(2,'0');if(!localStorage.getItem('mi-caddie-mode'))localStorage.setItem('mi-caddie-mode',JSON.stringify({day:d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate()),m:'play',man:true}))})()")
        errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(800)
        vb=lambda: pg.evaluate("document.querySelector('.cad-map > svg').getAttribute('viewBox')")
        tl=lambda: pg.inner_text(".hud.tl")
        m=await pg.locator(".cad-map > svg").bounding_box(); cx,cy=m["x"]+m["width"]/2,m["y"]+m["height"]/2
        print("start",await vb(),await tl())
        await pg.mouse.move(cx,cy); await pg.mouse.wheel(0,-400); await pg.wait_for_timeout(400); print("wheel",await vb(), await pg.locator(".hud.bl").count())
        await pg.mouse.move(cx,cy); await pg.mouse.down(); await pg.mouse.move(cx+60,cy+90,steps=6); await pg.mouse.up(); await pg.wait_for_timeout(400); print("pan",await vb(),await tl())
        await pg.mouse.dblclick(cx,cy); await pg.wait_for_timeout(400); print("dbl",await vb(),await tl(), await pg.locator(".hud.bl").count())
        await pg.screenshot(path="zoomt.png"); print(errs); await b.close()
asyncio.run(main())
