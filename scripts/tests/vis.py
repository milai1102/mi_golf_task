import asyncio, json, datetime
from playwright.async_api import async_playwright
day=datetime.date.today().isoformat()
def store(short, groups):
    H={}; t=1
    for j,(pars,res) in groups.items():
        for n,(on,pu) in enumerate(res):
            H[f"{short}|{j}|{n+1}"]={"s":[{"p":[300,500],"c":"一號木桿","t":t,"l":"T"}],"pen":0,"on":on,"putts":pu,"steps":5,"st":t}; t+=1
    return {"day":day,"holes":H}
cases={
 "nanbao": ("南寶", 0, {1:(None,[(3,2),(4,3),(2,1),(5,4),(3,2),(4,2),(6,3),(3,2),(4,2)]),0:(None,[(3,2),(4,2),(2,2)])}),
 "banzhi": ("斑芝花", 0, {0:(None,[(3,2),(4,3),(2,1),(5,4),(3,2),(9,3),(3,2),(4,2),(2,2)]),1:(None,[(3,2),(2,1)])}),
}
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for w in (360,393):
            for sch in ("dark","light"):
                for name,(short,ci,gr) in cases.items():
                    ctx=await b.new_context(viewport={"width":w,"height":760},device_scale_factor=3,color_scheme=sch)
                    st=store(short,gr)
                    await ctx.add_init_script("localStorage.setItem('mi-caddie-shots',%s)"%json.dumps(json.dumps(st)))
                    pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
                    await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1500)
                    # select course
                    await pg.click(".cad-course"); await pg.wait_for_timeout(200)
                    row=pg.locator(".cad-sheet .cr",has_text=short).first
                    await row.locator("button").nth(1 if name=="banzhi" else 2 if False else 0).click(); await pg.wait_for_timeout(600)
                    f=await pg.locator(".cad-foot").bounding_box()
                    await pg.screenshot(path=f"v_{name}_{w}_{sch}_foot.png",clip={"x":0,"y":f["y"]-4,"width":w,"height":f["height"]+8})
                    await pg.click(".cad-card"); await pg.wait_for_timeout(300)
                    await pg.screenshot(path=f"v_{name}_{w}_{sch}_round.png")
                    await pg.locator(".cad-sheet .fsc .c").nth(3).click(); await pg.wait_for_timeout(300)
                    await pg.screenshot(path=f"v_{name}_{w}_{sch}_hole.png")
                    print(name,w,sch,errs); await ctx.close()
        await b.close()
asyncio.run(main())
