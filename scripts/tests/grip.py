import json, sys
from playwright.sync_api import sync_playwright
U="http://localhost:8765/reports/caddie.html"
with sync_playwright() as p:
    b=p.chromium.launch()
    for sch in ["light","dark"]:
        c=b.new_context(viewport={"width":393,"height":760},device_scale_factor=2,is_mobile=True,has_touch=True,color_scheme=sch,geolocation={"latitude":25.03,"longitude":121.56},permissions=["geolocation"])
        pg=c.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        # yesterday's data to be archived
        y={"day":"2026-11-26","holes":{"南寶|0|1":{"s":[{"p":[100,500],"c":"一號木桿","t":1,"f":[23.16,120.36,6,1]}],"on":3,"putts":2,"steps":4}}}
        pg.add_init_script("if(!sessionStorage.getItem('x')){sessionStorage.setItem('x',1);localStorage.setItem('mi-caddie-shots',%s)}"%json.dumps(json.dumps(y)))
        pg.goto(U); pg.wait_for_timeout(1500)
        tabs=pg.eval_on_selector(".ptabs","e=>e.getBoundingClientRect().top")
        grip=pg.eval_on_selector(".tabgrip","e=>e.getBoundingClientRect().top")
        foot=pg.eval_on_selector(".cad-foot","e=>e.getBoundingClientRect().bottom")
        print(sch,"tabs top",tabs,"grip top",grip,"foot bottom",foot,"scrollH",pg.evaluate("document.documentElement.scrollHeight"),"view?",pg.evaluate("document.querySelector('.cad-view')!=null"))
        pg.screenshot(path=f"/tmp/grip_{sch}.png")
        pg.click(".tabgrip"); pg.wait_for_timeout(400)
        print(" after tap tabs top",pg.eval_on_selector(".ptabs","e=>e.getBoundingClientRect().top"))
        pg.screenshot(path=f"/tmp/grip_on_{sch}.png")
        pg.mouse.click(200,300); pg.wait_for_timeout(400)
        print(" after outside tabs top",pg.eval_on_selector(".ptabs","e=>e.getBoundingClientRect().top"))
        print(" days",pg.evaluate("localStorage.getItem('mi-caddie-days')")[:80],"| shots",pg.evaluate("localStorage.getItem('mi-caddie-shots')"))
        pg.click(".cad-modebar button.lg"); pg.wait_for_timeout(300); pg.screenshot(path=f"/tmp/grip_log_{sch}.png")
        print(" sheet:",pg.inner_text(".cad-sheet .box")[:120].replace("\n"," "))
        pk=pg.evaluate("(()=>{let s=null;const o=navigator.clipboard;return 1})()")
        print(" errs",errs); c.close()
    b.close()
