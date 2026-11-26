import asyncio, json, datetime
from playwright.async_api import async_playwright
day=datetime.date.today().isoformat()
JS="""(n)=>{const r=s=>{const e=document.querySelector(s);return e?e.getBoundingClientRect():null};const ov=(a,b)=>a&&b&&a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom;
const br=r('.hud.br'),tl=r('.hud.tl'),bl=r('.hud.bl'),g=r('.cad-here'),m=r('.cad-map');return {ov:[ov(br,tl)&&'tl',ov(br,bl)&&'bl',ov(br,g)&&'gps',br&&m&&(br.top<m.top||br.bottom>m.bottom+1)&&'out'].filter(Boolean),h:br?Math.round(br.height):0}}"""
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); bad=0; tot=0
        for vh,rec in ((640,False),(640,True),(760,False)):
            ctx=await b.new_context(viewport={"width":360,"height":vh},color_scheme="dark")
            pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1200)
            info=await pg.evaluate("()=>{const d=REPORT.sections.find(s=>s.kind==='caddie').data;const i=d.findIndex(c=>c.short==='南寶');return d[i].layouts.map((L,j)=>L.holes.map(H=>({h:H.h,tee:H.tee,pin:H.pin})))}")
            for j,L in enumerate(info):
                for H in L:
                    tee,pin=H['tee'],H['pin']; pts=[[tee[0]+(pin[0]-tee[0])*f, tee[1]+(pin[1]-tee[1])*f] for f in (0,.35,.6,.8,.9)]
                    st={"day":day,"holes":{f"南寶|{j}|{H['h']}":{"s":[{"p":[round(x) for x in q],"c":"一號木桿","t":k+1,"l":"F","pen":1 if k==2 else 0} for k,q in enumerate(pts)],"pen":0,"putts":2 if rec else None,"on":6 if rec else None,"st":9 if rec else None}}}
                    await pg.evaluate("s=>{localStorage.setItem('mi-caddie-shots',s);localStorage.setItem('mi-caddie',JSON.stringify([0,0,'南寶']))}",json.dumps(st))
                    await pg.evaluate(f"""()=>{{const d=REPORT.sections.find(s=>s.kind==='caddie').data;}}""")
                    await pg.reload(); await pg.wait_for_timeout(500)
                    await pg.click(".cad-course"); await pg.wait_for_timeout(100)
                    row=pg.locator(".cad-sheet .cr",has_text="南寶").first
                    lab=info and await pg.evaluate(f"()=>{{const d=REPORT.sections.find(s=>s.kind==='caddie').data;const c=d.find(c=>c.short==='南寶');return c.layouts[{j}].label}}")
                    await row.locator("button",has_text=lab).click(); await pg.wait_for_timeout(200)
                    await pg.click(".cad-hole"); await pg.wait_for_timeout(100)
                    await pg.locator(".cad-sheet .hgrid button").nth(H['h']-1).click(); await pg.wait_for_timeout(350)
                    r=await pg.evaluate(JS,0); tot+=1
                    if r['ov']: bad+=1; print(vh,rec,lab,H['h'],r); await pg.screenshot(path=f"hb_{vh}_{rec}_{lab}{H['h']}.png")
            print(vh,rec,'errs',errs); await ctx.close()
        print('total',tot,'bad',bad); await b.close()
asyncio.run(main())
