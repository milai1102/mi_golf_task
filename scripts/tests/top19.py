import asyncio, json
from playwright.async_api import async_playwright
JS = """async () => { const out = []; const selC = document.querySelectorAll('.cad-top select')[0], selL = document.querySelectorAll('.cad-top select')[1]; const nC = selC.options.length; for (let i = 0; i < nC; i++) { selC.value = i; selC.dispatchEvent(new Event('change')); await new Promise(r => setTimeout(r, 30)); const nL = selL.options.length; for (let j = 0; j < nL; j++) { selL.value = j; selL.dispatchEvent(new Event('change')); await new Promise(r => setTimeout(r, 30)); let guard = 0; while (guard++ < 30) { const cb = document.querySelector('.cad-course b'), hb = document.querySelector('.cad-hole b'), mb = document.querySelector('.cad-mode'); const bad = [cb, hb, mb].filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent); const top = document.querySelector('.cad-top'); const raw = /Country|Club|Golf/i.test(top.textContent); const tr = top.getBoundingClientRect(); out.push({ c: cb.textContent, h: hb.textContent, bad, raw, h2: Math.round(tr.height) }); const nx = document.querySelector('.cad-side.nx'); const sp = nx.querySelector('span').textContent; if (nx.disabled || sp !== '下一洞') { out[out.length-1].end = nx.disabled ? 'none' : sp; break; } nx.click(); await new Promise(r => setTimeout(r, 30)); } } } return out; }"""
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for W in (360, 393):
            pg = await (await b.new_context(viewport={"width": W, "height": 760})).new_page(); errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1500)
            r = await pg.evaluate(JS)
            bad = [x for x in r if x['bad'] or x['raw'] or x['h2'] > 46]
            labels = sorted(set((x['c'], x['h']) for x in r))
            ends = [(x['c'], x['h'], x['end']) for x in r if 'end' in x]
            print(W, 'checked', len(r), 'bad', bad[:10], 'errs', errs)
            if W == 393: print(json.dumps(ends, ensure_ascii=False)); print(sorted(set(x['c'] for x in r)))
        await b.close()
asyncio.run(main())
