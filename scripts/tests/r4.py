import asyncio, sys
from playwright.async_api import async_playwright
d = lambda v: v*180/2**31
TEE=(d(276218362),d(1436102968)); S2=(d(276233641),d(1436103681)); S3=(d(276242542),d(1436101041))
OUT="/home/claude/shot/r4_"
async def main():
    sch = sys.argv[1] if len(sys.argv)>1 else "dark"; W=int(sys.argv[2]) if len(sys.argv)>2 else 393
    async with async_playwright() as p:
        b=await p.chromium.launch()
        ctx=await b.new_context(viewport={"width":W,"height":760},device_scale_factor=2,color_scheme=sch,permissions=["geolocation"],geolocation={"latitude":TEE[0],"longitude":TEE[1],"accuracy":5})
        pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(2500)
        async def shot(n): await pg.screenshot(path=f"{OUT}{sch}{W}_{n}.png")
        print('top:', await pg.locator('.cad-top').inner_text())
        await shot(0)
        async def add(c, lie=None):
            await pg.click(".cad-add"); await pg.wait_for_timeout(300)
            if lie: await pg.click(f'.cad-sheet .lies button[data-l="{lie}"]')
            await pg.click(f'.cad-sheet .clubs3 button[data-c="{c}"]'); await pg.wait_for_timeout(500)
        await add("1W")
        await ctx.set_geolocation({"latitude":S2[0],"longitude":S2[1],"accuracy":6}); await pg.wait_for_timeout(5000)
        await add("H")
        await ctx.set_geolocation({"latitude":S3[0],"longitude":S3[1],"accuracy":6}); await pg.wait_for_timeout(5000)
        await add("A")
        await shot(1)
        # score: 5 ON 2 putts -> 2 est
        await pg.click(".cad-finb"); await pg.wait_for_timeout(300)
        await pg.click('.cad-sheet .nums >> nth=0 >> text="5"'); await pg.click('.cad-sheet .nums >> nth=1 >> text="2"'); await pg.click('.cad-sheet .nums >> nth=2 >> text="5"')
        await pg.click('.cad-sheet .ok'); await pg.wait_for_timeout(700); await shot(2)
        print('msg:', await pg.locator('.cad-msg').inner_text())
        print('hud:', await pg.locator('.hud.br').inner_text())
        print('gps row hidden:', await pg.locator('.cad-gps').is_hidden(), 'fin:', await pg.locator('.cad-finb').inner_text())
        # edit shot 2
        await pg.locator('.hud.br .r.done').nth(1).click(); await pg.wait_for_timeout(300); await shot(3)
        await pg.click('.cad-sheet .acts3 button >> nth=0'); await pg.wait_for_timeout(600); await shot(4)
        m = await pg.locator(".cad-map > svg").bounding_box()
        await pg.mouse.click(m["x"]+m["width"]*0.4, m["y"]+m["height"]*0.55); await pg.wait_for_timeout(400); await shot(5)
        await pg.click('.cad-fill .ok'); await pg.wait_for_timeout(500)
        print('after move msg:', await pg.locator('.cad-msg').inner_text())
        # insert after shot 1
        await pg.locator('.hud.br .r.done').nth(0).click(); await pg.wait_for_timeout(300)
        await pg.click('.cad-sheet .acts3 button >> nth=2'); await pg.wait_for_timeout(600); await shot(6)
        await pg.click('.cad-fill .ok'); await pg.wait_for_timeout(500); await shot(7)
        print('hud2:', await pg.locator('.hud.br').inner_text())
        # hazard: set shot 3 lie W
        await pg.locator('.hud.br .r.done').nth(2).click(); await pg.wait_for_timeout(300)
        await pg.click('.cad-sheet .lies button[data-l="W"]'); await pg.click('.cad-sheet .clubs3 button[data-c="H"]'); await pg.wait_for_timeout(500); await shot(8)
        print('hud3:', await pg.locator('.hud.br').inner_text())
        # mode button
        await pg.click('.cad-mode'); await pg.wait_for_timeout(300); await shot(9)
        await pg.click('.cad-sheet .ok >> nth=0'); await pg.wait_for_timeout(500); await shot(10)
        print('top sim:', await pg.locator('.cad-top').inner_text(), 'shots:', await pg.locator('.hud.br').inner_text())
        # hole grid
        await pg.click('.cad-hole'); await pg.wait_for_timeout(300); await shot(11)
        print(errs); await b.close()
asyncio.run(main())
