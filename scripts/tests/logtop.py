import asyncio, datetime
from playwright.async_api import async_playwright
day=datetime.date.today().isoformat()
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); ctx=await b.new_context(viewport={"width":393,"height":760},device_scale_factor=2,color_scheme="dark")
        pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1200)
        await pg.evaluate("""(day)=>{localStorage.clear();localStorage.__cad_noask=1;const h={};for(let i=1;i<=9;i++)h['南寶|0|'+i]={s:[],on:3,putts:2,st:Date.now()+i};localStorage.setItem('mi-caddie-shots',JSON.stringify({day,holes:h}));localStorage.setItem('mi-caddie-mode',JSON.stringify({day,sim:false,man:true}))}""",day)
        await pg.reload(); await pg.wait_for_timeout(1500)
        await pg.click(".cad-card"); await pg.wait_for_timeout(500)
        await pg.screenshot(path="/home/claude/shot/logtop.png")
        print(errs)
asyncio.run(main())
