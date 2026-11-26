from playwright.sync_api import sync_playwright
U="http://localhost:8765/reports/caddie.html"
with sync_playwright() as p:
    b=p.chromium.launch()
    for w,sch in [(393,"light"),(360,"dark")]:
        c=b.new_context(viewport={"width":w,"height":740},device_scale_factor=2,color_scheme=sch,is_mobile=True,has_touch=True)
        pg=c.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        pg.goto(U); pg.wait_for_timeout(1500)
        r=lambda s:pg.eval_on_selector(s,"e=>{const r=e.getBoundingClientRect();return [Math.round(r.left),Math.round(r.right),Math.round(r.top),Math.round(r.bottom)]}")
        print(w,sch,"menu",r(".cad-menu"),"tabs",r(".ptabs"),"foot",r(".cad-foot"),"scrollH",pg.evaluate("document.documentElement.scrollHeight"))
        print(" top texts",[pg.eval_on_selector_all(".cad-top > *","es=>es.filter(e=>e.offsetWidth).map(e=>e.innerText.replace(/\\n/g,' ')+':'+(e.scrollWidth>e.clientWidth?'CUT':'ok'))")])
        pg.screenshot(path=f"/tmp/menu_{w}.png")
        pg.tap(".cad-menu"); pg.wait_for_timeout(400); print(" open tabs",r(".ptabs")); pg.screenshot(path=f"/tmp/menu_on_{w}.png")
        pg.tap(".cad-menu"); pg.wait_for_timeout(400); print(" closed tabs",r(".ptabs"),errs)
        c.close()
    b.close()
