"""照 data/areas/plan.json 下載一整個球場範圍的空拍照片（拼成一張）與 OpenStreetMap 的高爾夫圖徵。
在 GitHub Actions 上跑（雲端開發環境連不到這些網站）。用在沒打過、沒有 Garmin 圖資的球場。
輸出 data/areas/<名稱>/photo_z<z>.jpg、photo_z<z>.json（左上角圖磚與張數）、osm.json。已有的不重抓。"""
import io, json, math, pathlib, time, urllib.parse, urllib.request
from PIL import Image
ROOT = pathlib.Path(__file__).resolve().parent.parent
D = ROOT / "data/areas"
URL = {"nlsc": "https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/{z}/{y}/{x}",
       "esri": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"}
UA = {"User-Agent": "mi_golf_task/1.0 (personal golf stats)"}

def tile(lat, lon, z):
    n = 2 ** z
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)

def fetch(u, data=None, img=True):
    for i in range(5):
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, data=data, headers=UA), timeout=90).read()
            return Image.open(io.BytesIO(r)).convert("RGB") if img else r
        except Exception as e:
            err = e; time.sleep(3 * (i + 1))
    print("失敗", u[:120], err); return None

def main():
    plan = json.loads((D / "plan.json").read_text())
    for name, p in plan.items():
        out = D / name; out.mkdir(exist_ok=True)
        s, w, n, e = p["bbox"]
        for z in p.get("zooms", [17]):
            f = out / f"photo_z{z}.jpg"
            x0, y0 = tile(n, w, z); x1, y1 = tile(s, e, z)
            meta = {"z": z, "ax": x0, "ay": y0, "nx": x1 - x0 + 1, "ny": y1 - y0 + 1, "src": p.get("src", "nlsc")}
            mf = out / f"photo_z{z}.json"
            if f.exists() and mf.exists() and json.loads(mf.read_text()) == meta: continue
            im = Image.new("RGB", (256 * meta["nx"], 256 * meta["ny"]), (40, 70, 40)); ok = 0
            for i in range(meta["nx"]):
                for j in range(meta["ny"]):
                    t = fetch(URL[meta["src"]].format(z=z, x=x0 + i, y=y0 + j))
                    if t is None and meta["src"] == "nlsc": t = fetch(URL["esri"].format(z=z, x=x0 + i, y=y0 + j))
                    if t: im.paste(t.resize((256, 256)), (256 * i, 256 * j)); ok += 1
                    time.sleep(0.1)
            im.save(f, "JPEG", quality=80, optimize=True, progressive=True)
            mf.write_text(json.dumps(meta)); print(name, z, im.size, ok, "tiles")
        if p.get("osm") and not (out / "osm.json").exists():
            q = f'[out:json][timeout:90];(nwr["golf"]({s},{w},{n},{e});nwr["leisure"="golf_course"]({s},{w},{n},{e});nwr["natural"="water"]({s},{w},{n},{e});nwr["landuse"="reservoir"]({s},{w},{n},{e}););out geom;'
            for host in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"):
                r = fetch(host, data=urllib.parse.urlencode({"data": q}).encode(), img=False)
                if r: (out / "osm.json").write_bytes(r); print("osm", len(r)); break

if __name__ == "__main__":
    main()
