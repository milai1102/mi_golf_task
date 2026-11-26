import asyncio, sys
sys.path.insert(0, "/home/claude/shot"); import cadlib
from playwright.async_api import async_playwright
d = lambda v: v*180/2**31
TEE=(d(276218362),d(1436102968)); S2=(d(276233641),d(1436103681)); S3=(d(276242542),d(1436101041))
async def main():
    sch = sys.argv[1] if len(sys.argv)>1 else "dark"
    async with async_playwright() as p:
        b=await p.chromium.launch()
        ctx=await b.new_context(viewport={"width":393,"height":760},device_scale_factor=2,color_scheme=sch,permissions=["geolocation"],geolocation={"latitude":TEE[0],"longitude":TEE[1],"accuracy":5})
        pg=await ctx.new_page(); await pg.add_init_script("(()=>{const d=new Date(),p=v=>String(v).padStart(2,'0');if(!localStorage.getItem('mi-caddie-mode'))localStorage.setItem('mi-caddie-mode',JSON.stringify({day:d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate()),m:'play',man:true}))})()"); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(2500)
        async def shot(n): await pg.screenshot(path=f"/home/claude/shot/st_{sch}_{n}.png")
        await shot(1)
        await pg.click(".cad-add"); await pg.wait_for_timeout(300); await shot(2)
        await pg.click('.cad-sheet .clubs3 button[data-c="1W"]'); await pg.wait_for_timeout(500); await shot(3)
        await ctx.set_geolocation({"latitude":S2[0],"longitude":S2[1],"accuracy":6}); await pg.wait_for_timeout(5000); await shot(4)
        await pg.click(".cad-add"); await pg.wait_for_timeout(300); await pg.click('.cad-sheet .lies button[data-l="W"]'); await pg.wait_for_timeout(150); await shot(5)
        await pg.click('.cad-sheet .clubs3 button[data-c="6i"]'); await pg.wait_for_timeout(500); await shot(6)
        await ctx.set_geolocation({"latitude":S3[0],"longitude":S3[1],"accuracy":6}); await pg.wait_for_timeout(5000); await shot(7)
        m = await pg.locator(".cad-map > svg").bounding_box(); cx, cy = m["x"]+m["width"]/2, m["y"]+m["height"]/2
        await pg.mouse.move(cx, cy); await pg.mouse.wheel(0, -300); await pg.wait_for_timeout(500); await shot(8)
        await pg.click(".cad-card"); await pg.wait_for_timeout(300); await shot(9)
        print(errs); await b.close()
asyncio.run(main())
