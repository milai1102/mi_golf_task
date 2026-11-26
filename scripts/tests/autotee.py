import asyncio, json
from playwright.async_api import async_playwright
d = lambda v: v*180/2**31
TEE=(d(276218362),d(1436102968)); S2=(d(276233641),d(1436103681)); NEAR=(d(276220362),d(1436102968))
async def run(label, steps):
    async with async_playwright() as p:
        b=await p.chromium.launch(); ctx=await b.new_context(viewport={"width":393,"height":760},permissions=["geolocation"],geolocation={"latitude":TEE[0],"longitude":TEE[1],"accuracy":5}); pg=await ctx.new_page(); await pg.add_init_script("(()=>{const d=new Date(),p=v=>String(v).padStart(2,'0');if(!localStorage.getItem('mi-caddie-mode'))localStorage.setItem('mi-caddie-mode',JSON.stringify({day:d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate()),m:'play',man:true}))})()")
        errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(2000)
        for skew, ll in steps:
            await pg.evaluate("k=>localStorage.setItem('__cad_skew',k)", str(skew)); await ctx.set_geolocation({"latitude":ll[0],"longitude":ll[1],"accuracy":5}); await pg.wait_for_timeout(5000)
        st=json.loads(await pg.evaluate("localStorage.getItem('mi-caddie-shots')") or '{}').get('holes',{})
        print(label, {k:[(x.get('c'),x.get('auto'),x.get('l')) for x in v['s']] for k,v in st.items()}, "|", await pg.inner_text(".cad-gps"), "|", await pg.inner_text(".cad-msg"), errs)
        await b.close()
async def main():
    await run("stay 25s then walk", [(25000, S2)])
    await run("walk at once", [(3000, S2)])
asyncio.run(main())
