"""用球洞資料合成假 GPS，整場走一遍：發球台→每一桿位置→果嶺記成績→下一洞發球台。python3 synth.py 楊梅 [分區索引]"""
import asyncio, json, re, sys, math
sys.path.insert(0,'/home/claude/shot')
from cadlib import club
from playwright.async_api import async_playwright
short=sys.argv[1]; lay=int(sys.argv[2]) if len(sys.argv)>2 else 0
s=open('/home/claude/mi_golf_task/reports/caddie.html').read()
R=json.loads(re.search(r"const REPORT = (\{.*?\});\n", s, re.S).group(1))
sec=[x for x in R["sections"] if x.get("kind")=="caddie"][0]
ci=[i for i,c in enumerate(sec["data"]) if c["short"]==short][0]; C=sec["data"][ci]; L=C["layouts"][lay]
def fromPx(H,p):
    g=H["ll"]; X=p[0]-g[4]; Y=p[1]-g[7]; det=g[2]*g[6]-g[3]*g[5]
    return (g[0]+(-g[5]*X+g[2]*Y)/det, g[1]+(g[6]*X-g[3]*Y)/det)
def along(H, yd):
    P=H.get("path") or [H["tee"],H["pin"]]; k=H["k"]; acc=0
    for a,b in zip(P,P[1:]):
        d=math.hypot(b[0]-a[0],b[1]-a[1])*k
        if acc+d>=yd: t=(yd-acc)/d; return [a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t]
        acc+=d
    return P[-1]
def total(H):
    P=H.get("path") or [H["tee"],H["pin"]]; return sum(math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(P,P[1:]))*H["k"]
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        H0=L["holes"][0]; t0=fromPx(H0,H0["tee"])
        ctx=await b.new_context(viewport={"width":393,"height":760},color_scheme="dark",permissions=["geolocation"],geolocation={"latitude":t0[0],"longitude":t0[1],"accuracy":4})
        pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(1500)
        # arrive at course in view mode -> should auto switch to play and maybe ask area
        await pg.evaluate("(i)=>{const o=document.querySelectorAll('.cad-top select')[0]; o.value=i; o.dispatchEvent(new Event('change'));}", ci); await pg.wait_for_timeout(500)
        btn=pg.locator(".cad-modebar button >> text=開始記錄這一場")
        if await btn.is_visible(): await btn.click()
        await pg.wait_for_timeout(800)
        if await pg.locator(".cad-sheet:not([hidden]) .areas").count():
            print("area sheet:", (await pg.inner_text(".cad-sheet .areas")).replace("\n"," "))
            await pg.locator(".cad-sheet .areas button").filter(has_text=L.get("label") or "").first.click(); await pg.wait_for_timeout(400)
        ok=0; bad=[]; tot=0
        for n,H in enumerate(L["holes"]):
            T=total(H); pts=[0]
            first=170 if H["par"]>=4 else min(T-5,150)
            x=first
            while x < T-25: pts.append(x); x+=130
            pts.append(max(T-15, pts[-1]+5))
            clubs=["1W" if H["par"]>=4 else "H"]+["H"]*(len(pts)-2)+["A"]
            for k,(yd,c) in enumerate(zip(pts,clubs)):
                ll=fromPx(H, along(H,yd)); await ctx.set_geolocation({"latitude":ll[0],"longitude":ll[1],"accuracy":4})
                await pg.wait_for_timeout(1300 if k==0 else 900)
                hl=await pg.inner_text(".cad-hole")
                if k==0 and f"第 {H['h']} 洞" not in hl: bad.append((H['h'],"tee shows",hl.replace("\n","")))
                try:
                    await club(pg,c); await pg.wait_for_timeout(250)
                except Exception as e:
                    st=await pg.evaluate("[document.querySelector('.cad-hole').innerText, document.querySelector('.cad-add').innerText, document.querySelector('.cad-map .hud.tl').innerText, document.querySelector('.cad-sheet').hidden]")
                    print("FAIL hole",H['h'],"step",k,c,st); await pg.screenshot(path='/home/claude/shot/synth_fail.png'); raise
            # green: score
            ll=fromPx(H,H["pin"]); await ctx.set_geolocation({"latitude":ll[0],"longitude":ll[1],"accuracy":4}); await pg.wait_for_timeout(900)
            on=len(pts); await pg.click(".cad-finb"); await pg.wait_for_timeout(200)
            await pg.locator(".cad-sheet .nums").nth(0).get_by_text(str(on), exact=True).click()
            await pg.locator(".cad-sheet .nums").nth(1).get_by_text("2", exact=True).click()
            await pg.click(".cad-sheet .ok"); await pg.wait_for_timeout(200)
            tot+=on+2
        card=(await pg.inner_text(".cad-card")).replace("\n"," ")
        await pg.reload(); await pg.wait_for_timeout(1200)
        st=await pg.evaluate("(()=>{const o=JSON.parse(localStorage.getItem('mi-caddie-shots')||'{}'); return Object.entries(o.holes||{}).map(([k,v])=>[k,(v.s||[]).length,v.on,v.putts])})()")
        print(short, L.get("label"), "holes", len(L["holes"]), "expected total", tot)
        print("card:", card)
        print("stored holes:", len(st), "with score", sum(1 for x in st if x[3] is not None), "shots", sum(x[1] for x in st))
        print("hole problems:", bad or "none")
        print("errs:", errs)
        await pg.screenshot(path=f"/home/claude/shot/synth_{short}.png")
        await b.close()
asyncio.run(main())
