import asyncio, datetime, json
from playwright.async_api import async_playwright
day=datetime.date.today().isoformat()
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); out=[]
        for W in [360,393]:
            ctx=await b.new_context(viewport={"width":W,"height":700},device_scale_factor=2,color_scheme="dark")
            pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1200)
            data=await pg.evaluate("""()=>REPORT.sections.find(s=>s.kind==='caddie').data.map(c=>[c.short,c.layouts.length,c.layouts.every(L=>L.holes.length<=9)])""")
            for ci,(sh,n,nine) in enumerate(data):
                if not(nine and n>1): continue
                await pg.evaluate("""([day,ci,sh])=>{localStorage.clear();localStorage.setItem('mi-caddie-mode',JSON.stringify({day,sim:false,man:true}));localStorage.setItem('mi-caddie',JSON.stringify([ci,0,sh,0,day]));localStorage.setItem('mi-caddie-combo',JSON.stringify({day,sh,f:0,b:1}));localStorage.__cad_noask=1}""",[day,ci,sh])
                await pg.reload(); await pg.wait_for_timeout(900)
                r=await pg.evaluate("""()=>{const t=document.querySelector('.cad-top');const bad=[...t.querySelectorAll('button,b')].filter(e=>e.scrollWidth>e.clientWidth+1).map(e=>e.textContent);return [t.innerText.replace(/\\n/g,' '),bad,t.scrollWidth>t.clientWidth]}""")
                out.append((W,sh,r))
                if sh=='斑芝花': await pg.screenshot(path=f"/home/claude/shot/seg2_{W}.png",clip={"x":0,"y":0,"width":W,"height":260})
            print(errs); await ctx.close()
        for o in out: print(o)
asyncio.run(main())
