import asyncio, sys
from playwright.async_api import async_playwright
d = lambda v: v*180/2**31
TEE=(d(276218362),d(1436102968)); S2=(d(276233641),d(1436103681)); S3=(d(276242542),d(1436101041))
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for sch,W,H in (("dark",393,760),("light",360,640)):
            ctx=await b.new_context(viewport={"width":W,"height":H},device_scale_factor=2,color_scheme=sch,permissions=["geolocation"],geolocation={"latitude":TEE[0],"longitude":TEE[1],"accuracy":5})
            pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(2500)
            shots=[]
            async def snap(n): 
                f=f"/home/claude/shot/lay_{sch}{W}_{n}.png"; await pg.screenshot(path=f); shots.append(f)
            async def add(c):
                await pg.click(".cad-add"); await pg.wait_for_timeout(300); await pg.click(f'.cad-sheet .clubs3 button[data-c="{c}"]'); await pg.wait_for_timeout(400)
            await add("1W"); await snap(1)
            await ctx.set_geolocation({"latitude":S2[0],"longitude":S2[1],"accuracy":6}); await pg.wait_for_timeout(4000)
            await add("H"); await snap(2)
            # overlap check
            ov=await pg.evaluate("""()=>{const q=s=>{const e=document.querySelector(s);if(!e||e.offsetParent===null)return null;const r=e.getBoundingClientRect();return [s,r.left,r.top,r.right,r.bottom]};const els=['.cad-map .cad-here','.cad-map .cad-ph','.cad-map .cad-side.pv','.cad-map .cad-side.nx','.cad-map .hud.br','.cad-map .hud.tl','.cad-map .hud.bl','.cad-map .cad-msg'].map(q).filter(Boolean);const out=[];for(let i=0;i<els.length;i++)for(let j=i+1;j<els.length;j++){const a=els[i],b=els[j];if(a[1]<b[3]&&a[3]>b[1]&&a[2]<b[4]&&a[4]>b[2])out.push(a[0]+' x '+b[0]);}return out;}""")
            await pg.click(".cad-finb"); await pg.wait_for_timeout(300)
            await pg.click('.cad-sheet .nums >> nth=0 >> text="3"'); await pg.click('.cad-sheet .nums >> nth=1 >> text="2"'); await pg.click('.cad-sheet .ok'); await pg.wait_for_timeout(600); await snap(3)
            ov2=await pg.evaluate("""()=>{const r=s=>{const e=document.querySelector(s);return e&&e.offsetParent!==null?e.getBoundingClientRect():null};const a=r('.cad-map .hud.br'),b=r('.cad-map .cad-side.nx');return a&&b?[Math.round(a.top),Math.round(b.bottom)]:null}""")
            print(sch,W,'overlap',ov,'br top vs next bottom',ov2,errs)
        await b.close()
asyncio.run(main())
