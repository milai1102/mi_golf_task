import sys; sys.path.insert(0, "/home/claude/shot"); import cadlib
# 最基本的保證：完全不靠 GPS（或 GPS 亂指），手動點洞號也能把 18 洞的成績記完
import asyncio, json, sys
from playwright.async_api import async_playwright
async def one(br, label, **kw):
    ctx = await br.new_context(viewport={"width": 393, "height": 683}, **kw)
    pg = await ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(900)
    await pg.evaluate("""(()=>{const s=document.querySelectorAll('.cad-top select')[0];s.value=[...s.options].find(o=>o.textContent.startsWith('南寶')).value;s.dispatchEvent(new Event('change'));})()""")
    await pg.wait_for_timeout(400); n = 0
    for area in ("B", "C"):
        await pg.evaluate("""(a)=>{const s=document.querySelectorAll('.cad-top select')[1];s.value=[...s.options].find(o=>o.textContent===a).value;s.dispatchEvent(new Event('change'));}""", area)
        await pg.wait_for_timeout(300)
        for h in range(1, 10):
            await cadlib.hole(pg, h); await pg.wait_for_timeout(250)
            cur = await pg.inner_text(".cad-holes .on")
            await pg.click(".cad-finb"); await pg.wait_for_timeout(120)
            hd = await pg.inner_text(".cad-sheet .hd")
            assert f"第 {h} 洞" in hd and cur == str(h), (area, h, hd, cur)
            N = pg.locator(".cad-sheet .nums")
            await N.nth(0).get_by_text(str(2 + h % 3), exact=True).click(); await N.nth(1).get_by_text(str(1 + h % 3), exact=True).click(); await N.nth(2).get_by_text(str(h), exact=True).click()
            await pg.click(".cad-sheet .ok"); await pg.wait_for_timeout(120); n += 1
            if h in (3, 7): await pg.reload(); await pg.wait_for_timeout(1500); assert (await pg.inner_text(".cad-holes .on")) == str(h), ("重新整理後跳洞", area, h, await pg.inner_text(".cad-holes .on"), await pg.inner_text(".cad-msg"))
    await pg.reload(); await pg.wait_for_timeout(1000)
    st = json.loads(await pg.evaluate("localStorage.getItem('mi-caddie-shots')"))['holes']
    ok = sum(1 for k, r in st.items() if r.get('putts') is not None and r.get('on') is not None and r.get('steps') is not None)
    lg = json.loads(await pg.evaluate("localStorage.getItem('mi-caddie-log')")); sc = sum(1 for e in lg if e['ev'] == 'score')
    print(f"{label}：記了 {n} 洞，存檔完整 {ok} 洞｜上方：{(await pg.inner_text('.cad-run')).replace(chr(10), ' ')}｜紀錄裡的 score 事件 {sc}｜JS 錯誤 {errs}")
    await pg.locator("details.cad-day:not(.cad-shots) summary").click(); print("   ", (await pg.inner_text("details.cad-day:not(.cad-shots)")).replace("\n", " ")[:260])
    await ctx.close(); return ok == 18 and not errs
async def main():
    async with async_playwright() as p:
        br = await p.chromium.launch(); r = []
        r.append(await one(br, "沒有定位權限"))
        r.append(await one(br, "定位在別的地方（台北）", permissions=["geolocation"], geolocation={"latitude": 25.03, "longitude": 121.56, "accuracy": 8}))
        r.append(await one(br, "定位一直指在南寶 A 區第 1 洞", permissions=["geolocation"], geolocation={"latitude": 276219116*180/2**31, "longitude": 1436102553*180/2**31, "accuracy": 6}))
        r.append(await one(br, "定位很不準（誤差 200 公尺）", permissions=["geolocation"], geolocation={"latitude": 276219116*180/2**31, "longitude": 1436102553*180/2**31, "accuracy": 200}))
        print("全部通過" if all(r) else "有失敗", r); await br.close()
asyncio.run(main())
