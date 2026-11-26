import asyncio
from playwright.async_api import async_playwright
JS = """(() => { const m=document.querySelector('.cad-map'), s=m.querySelector(':scope > svg'), bb=s.getBoundingClientRect(); let out=0; s.querySelectorAll('text').forEach(x=>{const r=x.getBoundingClientRect(); if(r.left<bb.left-1||r.right>bb.right+1||r.top<bb.top-1||r.bottom>bb.bottom+1) out++;});
 const R=e=>e&&e.getBoundingClientRect(), tl=R(m.querySelector('.hud.tl')), br=R(m.querySelector('.hud.br')), gp=R(m.querySelector('.cad-here')), mb=R(m); const ov=(a,b)=>a&&b&&a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top; const inside=a=>!a||(a.left>=mb.left-1&&a.right<=mb.right+1&&a.top>=mb.top-1&&a.bottom<=mb.bottom+1);
 return [m.innerText+'|'+[...s.querySelectorAll('text')].map(x=>x.textContent).join('|'), out, ov(tl,br)||ov(tl,gp)||ov(br,gp), inside(tl)&&inside(br)&&inside(gp)]; })()"""
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width":393,"height":740}, device_scale_factor=1, color_scheme="dark")
        errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(500)
        names = await pg.evaluate("[...document.querySelectorAll('.cad-top select')[0].options].map(o=>o.textContent)")
        tot=0; bad=[]
        for ci,nm in enumerate(names):
            await pg.evaluate(f"(() => {{ const s=document.querySelectorAll('.cad-top select')[0]; s.value={ci}; s.dispatchEvent(new Event('change')); }})()")
            nl = await pg.evaluate("document.querySelectorAll('.cad-top select')[1].options.length")
            for li in range(nl):
                await pg.evaluate(f"(() => {{ const s=document.querySelectorAll('.cad-top select')[1]; s.value={li}; s.dispatchEvent(new Event('change')); }})()")
                n = await pg.locator(".cad-holes .fchip").count()
                for hi in range(n):
                    await pg.evaluate("i => document.querySelectorAll('.cad-holes .fchip')[i].click()", hi)
                    r = await pg.evaluate(JS); tot+=1
                    if r[1] or r[2] or not r[3] or '桿' not in r[0] or 'NaN' in r[0] or 'undefined' in r[0]: bad.append((nm,li,hi+1,r[1:],r[0][:80]))
        print(tot, len(bad), bad[:8], errs[:3]); await b.close()
asyncio.run(main())
