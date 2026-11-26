import asyncio, json
from playwright.async_api import async_playwright
d = lambda v: v*180/2**31
TEE=(d(276218362),d(1436102968)); S2=(d(276233641),d(1436103681)); S3=(d(276242542),d(1436101041))
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); ctx=await b.new_context(viewport={"width":393,"height":760},device_scale_factor=2,color_scheme="dark",permissions=["geolocation"],geolocation={"latitude":TEE[0],"longitude":TEE[1],"accuracy":5})
        pg=await ctx.new_page(); await pg.add_init_script("(()=>{const d=new Date(),p=v=>String(v).padStart(2,'0');if(!localStorage.getItem('mi-caddie-mode'))localStorage.setItem('mi-caddie-mode',JSON.stringify({day:d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate()),m:'play',man:true}))})()"); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(2500)
        await pg.click(".cad-add"); await pg.wait_for_timeout(300); await pg.click('.cad-sheet .clubs3 button[data-c="1W"]'); await pg.wait_for_timeout(400)
        # skip recording shot at S2 (forgot) -> go to S3
        await ctx.set_geolocation({"latitude":S3[0],"longitude":S3[1],"accuracy":6}); await pg.wait_for_timeout(5000)
        await pg.click(".cad-add"); await pg.wait_for_timeout(300); await pg.click('.cad-sheet .clubs3 button[data-c="A"]'); await pg.wait_for_timeout(400)
        await pg.click(".cad-finb"); await pg.wait_for_timeout(300)
        await pg.locator('.cad-sheet .nums').nth(0).get_by_text("3",exact=True).click(); await pg.locator('.cad-sheet .nums').nth(1).get_by_text("2",exact=True).click(); await pg.click('.cad-sheet .ok'); await pg.wait_for_timeout(600)
        st=lambda: pg.evaluate("()=>{const o=JSON.parse(localStorage.getItem('mi-caddie-shots'));const k=Object.keys(o.holes)[0];return o.holes[k].s.map(z=>[z.p,z.c,z.l||null,z.ins||0])}")
        print('before', await st())
        # find S2 pixel location: compute from page via toPx
        xy = await pg.evaluate("""([la,lo])=>{const sv=document.querySelector('.cad-map > svg');return null}""",[S2[0],S2[1]])
        # tap point: midpoint between shot1 and shot2 in svg coords -> screen
        sc = await pg.evaluate("""()=>{const o=JSON.parse(localStorage.getItem('mi-caddie-shots'));const k=Object.keys(o.holes)[0];const s=o.holes[k].s;const q=[(s[0].p[0]+s[1].p[0])/2,(s[0].p[1]+s[1].p[1])/2];const sv=document.querySelector('.cad-map > svg');const pt=sv.createSVGPoint();pt.x=q[0];pt.y=q[1];const r=pt.matrixTransform(sv.getScreenCTM());return [r.x,r.y]}""")
        # double tap should not prompt
        await pg.mouse.click(sc[0],sc[1]); await pg.wait_for_timeout(120); await pg.mouse.click(sc[0],sc[1]); await pg.wait_for_timeout(700)
        print('after double tap msg:', repr(await pg.inner_text('.cad-msg')))
        await pg.mouse.click(sc[0],sc[1]); await pg.wait_for_timeout(700)
        print('msg:', await pg.inner_text('.cad-msg')); await pg.screenshot(path="ins_1.png")
        await pg.click('.cad-msg button:has-text("取消")'); await pg.wait_for_timeout(300); print('after cancel', len(await st()))
        await pg.mouse.click(sc[0],sc[1]); await pg.wait_for_timeout(700)
        await pg.click('.cad-msg button:has-text("補一桿")'); await pg.wait_for_timeout(400); await pg.screenshot(path="ins_2.png")
        await pg.click('.cad-sheet .clubs3 button[data-c="H"]'); await pg.wait_for_timeout(500); await pg.screenshot(path="ins_3.png")
        print('after', await st()); print('hud', await pg.locator('.hud.br .r.done').all_inner_texts()); print(errs)
        await pg.reload(); await pg.wait_for_timeout(1500); print('reload', await st())
        await b.close()
asyncio.run(main())
