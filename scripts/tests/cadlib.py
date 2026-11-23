async def club(pg, sel="sg"):
    await pg.click(".cad-add"); await pg.wait_for_timeout(120)
    q = ".cad-sheet .clubs button.sg" if sel == "sg" else f".cad-sheet .clubs button:nth-of-type({sel})" if isinstance(sel, int) else f'.cad-sheet .clubs button[data-c="{sel}"]'
    if await pg.locator(q).count() == 0: q = ".cad-sheet .clubs button:nth-of-type(1)"
    await pg.locator(q).first.click()
ROW = "[document.querySelector('.cad-add').textContent, document.querySelector('.cad-finb').textContent].join(' | ')"
