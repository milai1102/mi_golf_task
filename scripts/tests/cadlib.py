BAG = ["1W", "H", "6i", "8i", "P", "A", "54°"]
async def club(pg, sel="sg", lie=None):
    await pg.click(".cad-add"); await pg.wait_for_timeout(120)
    if lie: await pg.locator(f'.cad-sheet .lies button[data-l="{lie}"]').click()
    if isinstance(sel, int): sel = BAG[sel - 1] if sel <= len(BAG) else BAG[0]
    q = ".cad-sheet .clubs3 button.sg" if sel == "sg" else f'.cad-sheet .clubs3 button[data-c="{sel}"]'
    if await pg.locator(q).count() == 0: q = '.cad-sheet .clubs3 button[data-c="H"]'
    await pg.locator(q).first.click()
ROW = "[document.querySelector('.cad-edit').hidden ? '' : document.querySelector('.cad-edit').textContent, document.querySelector('.cad-add').textContent, document.querySelector('.cad-finb').textContent].join(' | ')"

async def hole(pg, n):
    await pg.click(".cad-hole"); await pg.wait_for_timeout(120)
    await pg.locator(f".cad-sheet .hgrid button:nth-of-type({n})").click()
