"""照 data/photos/plan.json 下載空拍圖磚，每洞拼成一張 JPEG：data/photos/<球場代碼>_<洞號>.jpg，
並更新 data/photos/index.json。在 GitHub Actions 上跑（雲端開發環境連不到圖磚網站）。已有的照片不重抓。"""
import io, json, pathlib, time, urllib.request
from PIL import Image
ROOT = pathlib.Path(__file__).resolve().parent.parent
D = ROOT / "data/photos"
URL = {"nlsc": "https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/{z}/{y}/{x}",
       "esri": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"}

def get(u):
    for i in range(4):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "mi_golf_task/1.0 (personal golf stats)"})
            return Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=30).read())).convert("RGB")
        except Exception as e:
            err = e; time.sleep(2 * (i + 1))
    print("失敗", u, err); return None

def main():
    plan = json.loads((D / "plan.json").read_text())
    idx = json.loads((D / "index.json").read_text()) if (D / "index.json").exists() else {}
    for key, p in sorted(plan.items()):
        f = D / f"{key}.jpg"
        if key in idx and f.exists() and all(idx[key].get(k) == p[k] for k in ("z", "ax", "ay", "nx", "ny")): continue
        im = Image.new("RGB", (256 * p["nx"], 256 * p["ny"]), (40, 70, 40)); ok = 0
        for i in range(p["nx"]):
            for j in range(p["ny"]):
                t = get(URL[p["src"]].format(z=p["z"], x=p["ax"] + i, y=p["ay"] + j))
                if t is None and p["src"] == "nlsc": t = get(URL["esri"].format(z=p["z"], x=p["ax"] + i, y=p["ay"] + j))
                if t: im.paste(t.resize((256, 256)), (256 * i, 256 * j)); ok += 1
                time.sleep(0.15)
        if ok < p["nx"] * p["ny"] * 0.7: print("略過", key, ok); continue
        im.save(f, "JPEG", quality=72, optimize=True, progressive=True)
        idx[key] = {k: p[k] for k in ("z", "ax", "ay", "nx", "ny", "src")}
        print(key, im.size, f.stat().st_size // 1024, "KB")
    (D / "index.json").write_text(json.dumps(idx, sort_keys=True, indent=0))

if __name__ == "__main__":
    main()
