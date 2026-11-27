import asyncio, sys
from playwright.async_api import async_playwright
from PIL import Image
PHOTO = len(sys.argv) > 1 and sys.argv[1] == "photo"
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 393, "height": 800}, device_scale_factor=1, color_scheme="dark")
        pg = await ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html")
        await pg.evaluate(f"localStorage.setItem('mi-caddie-photo', '{1 if PHOTO else 0}')")
        await pg.reload(); await pg.wait_for_timeout(800)
        idx = await pg.evaluate("[...document.querySelector('.cad-top select').options].findIndex(o=>o.text.startsWith('楊梅'))")
        await pg.evaluate(f"(()=>{{const s=document.querySelector('.cad-top select'); s.value=s.options[{idx}].value; s.dispatchEvent(new Event('change'))}})()"); await pg.wait_for_timeout(500)
        shots = []
        for n in range(18):
            await pg.evaluate(f"document.querySelectorAll('.cad-holes button')[{n}].click()"); await pg.wait_for_timeout(1500 if PHOTO else 400)
            el = await pg.query_selector(".cad-map"); f = f"/home/claude/shot/ym_{n+1:02d}.png"; await el.screenshot(path=f); shots.append(f)
            t = await pg.evaluate("document.querySelector('.cad-map .hud.br') ? document.querySelector('.cad-map .hud.br').innerText.replace(/\\n/g,' ') : ''")
            tl = await pg.evaluate("document.querySelector('.cad-map .hud.tl') ? document.querySelector('.cad-map .hud.tl').innerText.replace(/\\n/g,' ') : ''")
            print(n+1, tl, '|', t)
        print("errs", errs)
        ims = [Image.open(f) for f in shots]; w, h = ims[0].size
        for s in range(3):
            sh = Image.new("RGB", (w * 3, h * 2))
            for j, im in enumerate(ims[s * 6:s * 6 + 6]): sh.paste(im, ((j % 3) * w, (j // 3) * h))
            sh.save(f"/home/claude/shot/ym_sheet{s}{'p' if PHOTO else ''}.jpg", quality=80)
        await b.close()
asyncio.run(main())
