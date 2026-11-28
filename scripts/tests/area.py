import asyncio, json
from playwright.async_api import async_playwright
async def setcourse(pg, short):
    await pg.evaluate("""(s)=>{const o=[...document.querySelectorAll('.cad-top select')][0]; const i=[...o.options].findIndex(x=>x.textContent.startsWith(s)); o.value=i; o.dispatchEvent(new Event('change'));}""", short)
    await pg.wait_for_timeout(300)
async def score(pg, on, pt):
    await pg.click(".cad-finb"); await pg.wait_for_timeout(150)
    await pg.locator(".cad-sheet .nums").nth(0).get_by_text(str(on), exact=True).click()
    await pg.locator(".cad-sheet .nums").nth(1).get_by_text(str(pt), exact=True).click()
    await pg.click(".cad-sheet .ok"); await pg.wait_for_timeout(150)
    if await pg.locator(".cad-msg button >> text=之後再說").count(): await pg.click(".cad-msg button >> text=之後再說")
async def state(pg):
    return await pg.evaluate("[document.querySelector('.cad-course').innerText.replace(/\\n/g,''), document.querySelector('.cad-hole').innerText.replace(/\\n/g,''), document.querySelector('.cad-side.nx')? (getComputedStyle(document.querySelector('.cad-side.nx')).display+':'+document.querySelector('.cad-side.nx').innerText.replace(/\\n/g,'')):'-']")
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); res={}
        for short in ["南寶","楊梅","第一","斑芝花"]:
            ctx=await b.new_context(viewport={"width":393,"height":760},color_scheme="dark")
            pg=await ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(800)
            await setcourse(pg, short)
            await pg.click(".cad-modebar button >> text=開始記錄這一場"); await pg.wait_for_timeout(500)
            sh = await pg.locator(".cad-sheet:not([hidden]) .areas").count()
            title = (await pg.inner_text(".cad-sheet .hd b")) if sh else "-"
            opts = (await pg.inner_text(".cad-sheet .areas")).replace("\n"," ") if sh else "-"
            print(short, "sheet:", sh, title, "|", opts)
            if sh: await pg.locator(".cad-sheet .areas button").nth(0).click(); await pg.wait_for_timeout(300)
            print("  start:", await state(pg))
            seen=[]
            for k in range(20):
                await score(pg, 4, 2)
                st=await state(pg); seen.append(st[1])
                nx=pg.locator(".cad-side.nx")
                if not await nx.is_visible(): print("  no next at", st); break
                await nx.click(); await pg.wait_for_timeout(250)
                if await pg.locator(".cad-sheet:not([hidden]) .areas").count():
                    t2=await pg.inner_text(".cad-sheet .hd b"); o2=(await pg.inner_text(".cad-sheet .areas")).replace("\n"," ")
                    print("  at", st, "->", t2, "|", o2)
                    await pg.locator(".cad-sheet .areas button").nth(0).click(); await pg.wait_for_timeout(300)
                    print("  after choose:", await state(pg))
            print("  holes visited:", " ".join(x.replace("第","").replace("洞▾","") for x in seen))
            card=(await pg.inner_text(".cad-card")).replace("\n"," ")
            print("  card:", card)
            await pg.reload(); await pg.wait_for_timeout(800)
            print("  reload sheet:", await pg.locator(".cad-sheet:not([hidden]) .areas").count(), await state(pg))
            await pg.click(".cad-card"); await pg.wait_for_timeout(300)
            txt=(await pg.inner_text(".cad-sheet")).replace("\n"," ")
            print("  round:", txt[:160], "| 還沒記" in txt)
            await pg.screenshot(path=f"/home/claude/shot/area_{short}.png")
            print("  errs", errs)
            await ctx.close()
        await b.close()
asyncio.run(main())
