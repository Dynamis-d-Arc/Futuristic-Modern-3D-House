"""Download the CC0 models render.py uses from Poly Haven (glTF) into assets/<id>/: furniture with 2K textures, small props with 1K.

    python fetch_assets.py
"""
import hashlib, json, os, urllib.request
from urllib.parse import urlparse

IDS = ("modern_arm_chair_01 mid_century_lounge_chair dining_chair_02 Ottoman_01 coffee_table_round_01 "
       "modern_coffee_table_01 side_table_01 modern_wooden_cabinet steel_frame_shelves_03 drawer_cabinet "
       "modern_ceiling_lamp_01 potted_plant_01 potted_plant_02 potted_plant_04 ceramic_vase_01 ceramic_vase_02 "
       "hanging_picture_frame_01").split()
PROPS = ("throw_pillows_01 wicker_basket_01 wicker_basket_02 wine_bottles_01 wooden_bowl_01 lemon croissant carrot_cake tea_set_01 "
         "jug_01 wooden_cutting_board pot_enamel_01 chess_set standing_picture_frame_01 standing_picture_frame_02 alarm_clock_01 "
         "marble_bust_01 Ukulele_01 ceramic_vase_03 ceramic_vase_04 brass_vase_01 hanging_picture_frame_02 wall_clock "
         "book_encyclopedia_set_01 fishermans_hat rubber_boots").split()
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
UA = {"User-Agent": "house-render-script/1.0"}


def get(f):
    """Fetch one file listed by the API and check it against the checksum the API gave."""
    assert urlparse(f["url"]).hostname == "dl.polyhaven.org", "unexpected host " + f["url"]
    data = urllib.request.urlopen(urllib.request.Request(f["url"], headers=UA), timeout=120).read()
    assert hashlib.md5(data).hexdigest() == f["md5"], "checksum mismatch for " + f["url"]
    return data


total = 0
for i, res in [(i, "2k") for i in IDS] + [(i, "1k") for i in PROPS]:
    api = urllib.request.Request("https://api.polyhaven.com/files/" + i, headers=UA)
    g = json.load(urllib.request.urlopen(api, timeout=120))["gltf"][res]["gltf"]
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
