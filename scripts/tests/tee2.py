import asyncio, sys
sys.path.insert(0,'/home/claude/shot')
from cadlib import club, hole
from playwright.async_api import async_playwright
TEE=(23.143519,120.371213); SEC=(23.144525,120.370141); THIRD=(23.14551,120.369843)
async def setup(b, geo):
    ctx=await b.new_context(viewport={"width":393,"height":760},color_scheme="dark",permissions=["geolocation"],geolocation={"latitude":geo[0],"longitude":geo[1],"accuracy":4})
    pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
    await pg.goto("http://localhost:8765/reports/caddie.html")
    await pg.evaluate("localStorage.setItem('__cad_noask','1');localStorage.setItem('mi-caddie-mode',JSON.stringify({day:new Date().toLocaleDateString('sv'),m:'play',man:1}))")
    await pg.reload(); await pg.wait_for_timeout(2500)
    return ctx,pg,errs
async def hudtxt(pg): return (await pg.inner_text(".cad-map .hud.br")).replace("\n"," ")
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for name, pick in [("A 忘了開球、在第二桿按 H", ["H"]), ("B 忘了開球、在第二桿先補 1W 再 H", ["1W","H"])]:
            ctx,pg,errs=await setup(b, SEC)
            print(name, "| hole", await pg.inner_text(".cad-hole"), "| lie preselect check")
            for c in pick:
                await club(pg,c); await pg.wait_for_timeout(5300)
            print("  msg:", await pg.inner_text(".cad-msg") if await pg.locator(".cad-msg").count() else "")
            print("  hud:", await hudtxt(pg))
            await pg.click(".cad-hud, .cad-map .hud.br .dsum") if await pg.locator(".cad-map .hud.br .dsum").count() else None
            await pg.wait_for_timeout(300)
            if await pg.locator(".cad-sheet:not([hidden])").count(): print("  hole:", (await pg.inner_text(".cad-sheet .shots")).replace("\n"," "))
            print("  errs", errs); await ctx.close()
        # C at tee
        ctx,pg,errs=await setup(b, TEE)
        await club(pg,"1W"); await pg.wait_for_timeout(500)
        print("C at tee:", await hudtxt(pg))
        h0=await hudtxt(pg)
        # walk to second spot without pressing
        for f in [0.3,0.6,1.0]:
            await ctx.set_geolocation({"latitude":TEE[0]+(SEC[0]-TEE[0])*f,"longitude":TEE[1]+(SEC[1]-TEE[1])*f,"accuracy":4}); await pg.wait_for_timeout(1500)
        h1=await hudtxt(pg); tl=await pg.inner_text(".cad-map .hud.tl")
        print("  after walking (no press): same plan?", h0==h1, "| tl:", tl.replace("\n"," "))
        print("  again btn:", await pg.locator(".cad-again").is_visible())
        await pg.click(".cad-add"); await pg.wait_for_timeout(400)
        sg=await pg.evaluate("[...document.querySelectorAll('.cad-sheet .clubs3 button.sg')].map(b=>b.dataset.c)")
        lie=await pg.evaluate("[...document.querySelectorAll('.cad-sheet .lies button.on')].map(b=>b.textContent)")
        print("  open picker at 2nd: suggestion", sg, "lie", lie)
        await pg.locator(".cad-sheet .clubs3 button[data-c='H']").click(); await pg.wait_for_timeout(500)
        print("  after H:", await hudtxt(pg))
        print("  again btn:", await pg.locator(".cad-again").is_visible(), (await pg.inner_text(".cad-again")).replace("\n"," "))
        await pg.wait_for_timeout(5200); await pg.click(".cad-again"); await pg.wait_for_timeout(400)
        print("  after again:", await hudtxt(pg))
        await ctx.set_geolocation({"latitude":THIRD[0],"longitude":THIRD[1],"accuracy":4}); await pg.wait_for_timeout(1500)
        print("  moved to third (no press) again visible:", await pg.locator(".cad-again").is_visible())
        await pg.click(".cad-here"); await pg.wait_for_timeout(1500)
        print("  after 定位:", await hudtxt(pg), "| tl:", (await pg.inner_text(".cad-map .hud.tl")).replace("\n"," "))
        await pg.screenshot(path="/home/claude/shot/tee2.png")
        print("  errs", errs)
        await b.close()
asyncio.run(main())
