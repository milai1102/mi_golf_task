from playwright.sync_api import sync_playwright
P=["index","data","courses","plan","caddie","more"]
with sync_playwright() as p:
    b=p.chromium.launch()
    for sch in ["light","dark"]:
        c=b.new_context(viewport={"width":393,"height":760},device_scale_factor=2,color_scheme=sch,is_mobile=True,has_touch=True)
        for n in P:
            pg=c.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
            pg.goto(f"http://localhost:8765/reports/{n}.html"); pg.wait_for_timeout(1500)
            m=pg.query_selector(".cad-menu"); r=m.bounding_box() if m else None
            pg.screenshot(path=f"/tmp/ma_{n}_{sch}.png")
            pg.tap(".cad-menu"); pg.wait_for_timeout(400)
            tb=pg.eval_on_selector(".ptabs","e=>Math.round(e.getBoundingClientRect().top)")
            mb2=pg.eval_on_selector(".cad-menu","e=>Math.round(e.getBoundingClientRect().top)")
            pg.screenshot(path=f"/tmp/ma_{n}_{sch}_on.png")
            pg.tap(".cad-menu"); pg.wait_for_timeout(400)
            tb2=pg.eval_on_selector(".ptabs","e=>Math.round(e.getBoundingClientRect().bottom)")
            print(n,sch,"menu",[round(r['x']),round(r['y'])] if r else None,"open tabs top",tb,"menu then",mb2,"closed tabs bottom",tb2,errs)
            pg.close()
        c.close()
    c=b.new_context(viewport={"width":1280,"height":800}); pg=c.new_page(); pg.goto("http://localhost:8765/reports/index.html"); pg.wait_for_timeout(1000)
    print("desktop menu?", pg.query_selector(".cad-menu") is not None, pg.eval_on_selector(".ptabs","e=>getComputedStyle(e).position"))
    b.close()
