from playwright.sync_api import sync_playwright
U="http://localhost:8765/reports/caddie.html"
def big(pg): return pg.inner_text(".hud.tl").split("\n")[0]
with sync_playwright() as p:
    b=p.chromium.launch()
    for sch in ["dark","light"]:
        c=b.new_context(viewport={"width":393,"height":760},device_scale_factor=2,color_scheme=sch,geolocation={"latitude":25.03,"longitude":121.56},permissions=["geolocation"])
        pg=c.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        pg.goto(U); pg.wait_for_timeout(1500)
        bx=pg.eval_on_selector(".cad-map","e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height]}")
        x,y=bx[0]+bx[2]*0.45,bx[1]+bx[3]*0.55
        t0=big(pg); pg.mouse.click(x,y); pg.wait_for_timeout(500)
        print(sch,"tee",t0,"| after tap",big(pg),"| msg",pg.inner_text(".cad-msg") if pg.query_selector(".cad-msg") else "")
        pg.mouse.move(x,y); pg.mouse.down(); pg.mouse.move(x+40,y+10,steps=5); pg.wait_for_timeout(700); pg.mouse.up(); pg.wait_for_timeout(300)
        print(" after drag-hold",big(pg))
        pg.mouse.move(x,y); pg.mouse.down(); pg.wait_for_timeout(650); pg.mouse.up(); pg.wait_for_timeout(400)
        print(" after longpress",big(pg),"| reset btn",pg.query_selector(".cad-map .cad-rs") is not None)
        pg.screenshot(path=f"/tmp/lp_{sch}.png")
        pg.click(".cad-map .cad-rs"); pg.wait_for_timeout(400)
        print(" after reset",big(pg),"| reset btn",pg.query_selector(".cad-map .cad-rs") is not None, "errs",errs)
        c.close()
    b.close()
