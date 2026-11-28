import asyncio, json
from playwright.async_api import async_playwright
d = lambda v: v*180/2**31
TEE=(d(276218362),d(1436102968)); S2=(d(276233641),d(1436103681))
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        ctx=await b.new_context(viewport={"width":393,"height":760},permissions=["geolocation"],geolocation={"latitude":TEE[0],"longitude":TEE[1],"accuracy":5})
        pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(2500)
        async def add(c, lie=None):
            await pg.click(".cad-add"); await pg.wait_for_timeout(300)
            if lie: await pg.click(f'.cad-sheet .lies button[data-l="{lie}"]')
            await pg.click(f'.cad-sheet .clubs3 button[data-c="{c}"]'); await pg.wait_for_timeout(400)
        await add("1W")
        await ctx.set_geolocation({"latitude":S2[0],"longitude":S2[1],"accuracy":6}); await pg.wait_for_timeout(4000)
        await add("H","W")
        await pg.locator('.hud.br .dsum').click(); await pg.wait_for_timeout(300)
        await pg.locator('.cad-sheet .shots button').nth(1).click(); await pg.wait_for_timeout(300)
        await pg.click('.cad-sheet .lies button[data-l="O1"]'); await pg.wait_for_timeout(300)
        on=await pg.evaluate("[...document.querySelectorAll('.cad-sheet .lies button.on')].map(b=>b.dataset.l)")
        await pg.click('.cad-sheet .hd .x'); await pg.reload(); await pg.wait_for_timeout(2000)
        st=json.loads(await pg.evaluate("localStorage.getItem('mi-caddie-shots')"))
        s=list(st['holes'].values())[0]['s']
        print('selected',on,'saved',[(x.get('l'),x.get('pen')) for x in s],errs)
        await b.close()
asyncio.run(main())
