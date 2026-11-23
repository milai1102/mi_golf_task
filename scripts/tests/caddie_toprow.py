import asyncio
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for w in (360,393):
            pg=await b.new_page(viewport={"width":w,"height":760},device_scale_factor=2)
            await pg.goto("http://localhost:8765/reports/caddie.html"); await pg.wait_for_timeout(900)
            n=await pg.evaluate("document.querySelector('.cad-top select').options.length"); bad=[]
            for i in range(n):
                await pg.select_option(".cad-top select >> nth=0", index=i); await pg.wait_for_timeout(100)
                r=await pg.evaluate("""()=>{const t=document.querySelector('.cad-top');const ss=t.querySelectorAll('select');const c=document.createElement('canvas').getContext('2d');c.font='700 17px sans-serif';const need=s=>{let m=0;for(const o of s.options)m=Math.max(m,c.measureText(o.text).width);return m+30};const hb=document.querySelector('.cad-hole');const l=t.lastElementChild.getBoundingClientRect();const o=[];if(ss[0].offsetWidth<c.measureText(ss[0].selectedOptions[0].text).width+30)o.push('course '+ss[0].offsetWidth);if(ss[1]&&ss[1].offsetWidth&&ss[1].offsetWidth<need(ss[1]))o.push('area '+ss[1].offsetWidth+'<'+Math.round(need(ss[1])));if(hb.scrollWidth>hb.clientWidth)o.push('hole '+hb.clientWidth+'<'+hb.scrollWidth);if(l.right>innerWidth-4)o.push('over');return ss[0].selectedOptions[0].text+' '+o.join(', ')+' ['+[...t.children].map(e=>Math.round(e.offsetWidth)).join(',')+']'}""")
                bad.append(r)
            print(w,"\n  "+"\n  ".join(bad))
            await pg.select_option(".cad-top select >> nth=0", label="斑芝花"); await pg.wait_for_timeout(300)
            await pg.screenshot(path=f"/home/claude/shot/nx{w}.png",clip={"x":0,"y":0,"width":w,"height":170}); await pg.close()
        await b.close()
asyncio.run(main())
