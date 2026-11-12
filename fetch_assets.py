"""Download the CC0 furniture models render.py uses from Poly Haven (glTF, 2K textures) into assets/<id>/.

    python fetch_assets.py
"""
import hashlib, json, os, urllib.request
from urllib.parse import urlparse

IDS = ("modern_arm_chair_01 mid_century_lounge_chair dining_chair_02 Ottoman_01 coffee_table_round_01 "
       "modern_coffee_table_01 side_table_01 modern_wooden_cabinet steel_frame_shelves_03 drawer_cabinet "
       "modern_ceiling_lamp_01 potted_plant_01 potted_plant_02 potted_plant_04 ceramic_vase_01 ceramic_vase_02 "
       "hanging_picture_frame_01").split()
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
UA = {"User-Agent": "house-render-script/1.0"}


def get(f):
    """Fetch one file listed by the API and check it against the checksum the API gave."""
    assert urlparse(f["url"]).hostname == "dl.polyhaven.org", "unexpected host " + f["url"]
    data = urllib.request.urlopen(urllib.request.Request(f["url"], headers=UA), timeout=120).read()
    assert hashlib.md5(data).hexdigest() == f["md5"], "checksum mismatch for " + f["url"]
    return data


total = 0
for i in IDS:
    api = urllib.request.Request("https://api.polyhaven.com/files/" + i, headers=UA)
    g = json.load(urllib.request.urlopen(api, timeout=120))["gltf"]["2k"]["gltf"]
    folder = os.path.join(OUT, i)
    os.makedirs(folder, exist_ok=True)
    doc = json.loads(get(g))
    # Texture names are stored as t0.jpg, t1.jpg...: the original long names push paths past Windows' 260 characters.
    short = {}
    for img in doc.get("images", []):
        short.setdefault(img["uri"], "t%d%s" % (len(short), os.path.splitext(img["uri"])[1]))
        img["uri"] = short[img["uri"]]
    for rel, f in g.get("include", {}).items():
        path = os.path.join(folder, short.get(rel) or os.path.basename(rel))
        if not (os.path.exists(path) and os.path.getsize(path) == f["size"]):
            with open(path, "wb") as fh:
                fh.write(get(f))
        total += f["size"]
    with open(os.path.join(folder, i + ".gltf"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    print("ok", i, 1 + len(g.get("include", {})), "files")
print("DONE %.0f MB" % (total / 1e6))
