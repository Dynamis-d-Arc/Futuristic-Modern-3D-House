"""Orbis House: builds a furnished three-level house with its garden and renders
the exterior plus one interior per level. Some furniture is CC0 models from Poly Haven.

    python fetch_assets.py        (once: downloads the models into assets/)
    blender -b -P render.py -- [--draft] [--only=exterior,level,tour,tour-living]

Writes the four stills, floors.js (level outlines for index.html), and for the walk-through
tour/<spot>.jpg (360 degree panoramas) plus tour.js (where each spot is and what it links to).
"""
import bpy, bmesh, json, math, os, random, sys, time
from mathutils import Matrix, Vector
from mathutils.geometry import convex_hull_2d
from bpy_extras.object_utils import world_to_camera_view

DRAFT = "--draft" in sys.argv
ONLY = next((a[7:].split(",") for a in sys.argv if a.startswith("--only=")), None)
OUT = os.path.dirname(os.path.abspath(__file__))
R = random.Random(3)
T0 = time.time()
LUX = 1.7  # interior light boost so the rooms glow against the twilight sky

bpy.ops.wm.read_factory_settings(use_empty=True)
S = bpy.context.scene
C = S.collection

# ---------------------------------------------------------------- render setup
S.render.engine = "CYCLES"
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "OPTIX"
    prefs.get_devices()
    for d in prefs.devices:
        d.use = d.type == "OPTIX"
    S.cycles.device = "GPU"
except Exception:
    S.cycles.device = "CPU"
S.cycles.use_adaptive_sampling = True
S.cycles.adaptive_threshold = 0.04 if DRAFT else 0.015
S.cycles.use_denoising = True
S.cycles.max_bounces = 8
S.cycles.transparent_max_bounces = 32  # a ray through the house crosses many glass faces
S.cycles.caustics_reflective = S.cycles.caustics_refractive = False
S.cycles.sample_clamp_indirect = 6
S.render.resolution_x, S.render.resolution_y = 1920, 1080
S.render.resolution_percentage = 50 if DRAFT else 100
S.render.image_settings.file_format = "JPEG"
S.render.image_settings.quality = 92
S.render.use_persistent_data = True
try:
    S.view_settings.look = "AgX - Medium High Contrast"
except TypeError:
    pass

# ---------------------------------------------------------------- materials
def pbr(name, color, rough=0.5, metal=0.0, **inputs):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    for k, v in inputs.items():
        b.inputs[k.replace("_", " ")].default_value = v
    return m


def glow(name, color, strength):
    return pbr(name, color, Emission_Color=(*color, 1), Emission_Strength=strength)


def ramp(nt, cols, lo=0.15, hi=0.85):
    r = nt.nodes.new("ShaderNodeValToRGB")
    e = r.color_ramp.elements
    while len(e) < len(cols):
        e.new(0.5)
    for i, c in enumerate(cols):
        e[i].position = lo + (hi - lo) * i / (len(cols) - 1)
        e[i].color = (*c, 1)
    return r


def calc(nt, op, a, b=0.0, clamp=False):
    """Math node; a and b are numbers or output sockets."""
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    n.use_clamp = clamp
    for i, v in enumerate((a, b)):
        if isinstance(v, (int, float)):
            n.inputs[i].default_value = v
        else:
            nt.links.new(v, n.inputs[i])
    return n.outputs[0]


def tex(m, c2, kind="noise", scale=(1, 1, 1), size=6.0, bump=0.1, bevel=0.0):
    """World-space colour variation and bump, so neighbouring boxes line up."""
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    L = nt.links.new
    c1 = tuple(b.inputs["Base Color"].default_value)[:3]
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = scale
    L(geo.outputs["Position"], mp.inputs["Vector"])
    bp = nt.nodes.new("ShaderNodeBump")
    bp.inputs["Strength"].default_value = bump
    if kind == "tile":
        t = nt.nodes.new("ShaderNodeTexBrick")
        t.inputs["Color1"].default_value = (*c1, 1)
        t.inputs["Color2"].default_value = (*c2, 1)
        t.inputs["Mortar"].default_value = (*(x * 0.45 for x in c1), 1)
        t.inputs["Scale"].default_value = 1
        t.inputs["Mortar Size"].default_value = 0.006
        t.inputs["Brick Width"].default_value = size
        t.inputs["Row Height"].default_value = size / 2
        L(mp.outputs[0], t.inputs["Vector"])
        L(t.outputs[0], b.inputs["Base Color"])
        L(t.outputs[1], bp.inputs["Height"])
        bp.invert = True
    else:
        t = nt.nodes.new("ShaderNodeTexNoise")
        t.inputs["Scale"].default_value = size
        t.inputs["Detail"].default_value = 10
        L(mp.outputs[0], t.inputs["Vector"])
        r = ramp(nt, (c1, c2), 0.3, 0.7)
        L(t.outputs[0], r.inputs[0])
        L(r.outputs[0], b.inputs["Base Color"])
        L(t.outputs[0], bp.inputs["Height"])
    if bevel:  # rounded edge highlights without extra geometry
        bv = nt.nodes.new("ShaderNodeBevel")
        bv.samples = 2
        bv.inputs["Radius"].default_value = bevel
        L(bv.outputs[0], bp.inputs["Normal"])
    L(bp.outputs[0], b.inputs["Normal"])
    return m


def foliage(name, cols, hair=False):
    """Per-leaf (or per-blade) colour with light passing through."""
    m = pbr(name, cols[0], 0.55)
    nt = m.node_tree
    L = nt.links.new
    b = nt.nodes["Principled BSDF"]
    src = nt.nodes.new("ShaderNodeHairInfo" if hair else "ShaderNodeNewGeometry")
    r = ramp(nt, cols)
    L(src.outputs["Random" if hair else "Random Per Island"], r.inputs[0])
    L(r.outputs[0], b.inputs["Base Color"])
    tl = nt.nodes.new("ShaderNodeBsdfTranslucent")
    mx = nt.nodes.new("ShaderNodeMixShader")
    mx.inputs[0].default_value = 0.35
    L(r.outputs[0], tl.inputs[0])
    L(b.outputs[0], mx.inputs[1])
    L(tl.outputs[0], mx.inputs[2])
    L(mx.outputs[0], nt.nodes["Material Output"].inputs[0])
    return m


def glass_mat():
    # A mirror layer whose opacity follows Schlick's Fresnel on |N.I|, so light and shadow rays pass
    # straight through. The built-in Fresnel node turns fully mirror on back faces past 36 degrees.
    m = pbr("glass", (1, 1, 1), 0.0, 1.0)
    nt = m.node_tree
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    dot = nt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    nt.links.new(geo.outputs["Normal"], dot.inputs[0])
    nt.links.new(geo.outputs["Incoming"], dot.inputs[1])
    grazing = calc(nt, "SUBTRACT", 1.0, calc(nt, "ABSOLUTE", dot.outputs["Value"]))
    alpha = calc(nt, "ADD", calc(nt, "MULTIPLY", calc(nt, "POWER", grazing, 5), 0.96), 0.04)
    nt.links.new(alpha, nt.nodes["Principled BSDF"].inputs["Alpha"])
    return m


def water_mat():
    m = pbr("water", (0.75, 0.94, 0.96), 0.02, IOR=1.33, Transmission_Weight=1.0)
    nt = m.node_tree
    L = nt.links.new
    b = nt.nodes["Principled BSDF"]
    n = nt.nodes.new("ShaderNodeTexNoise")
    n.inputs["Scale"].default_value = 1.6
    n.inputs["Detail"].default_value = 3
    bp = nt.nodes.new("ShaderNodeBump")
    bp.inputs["Strength"].default_value = 0.06
    L(n.outputs[0], bp.inputs["Height"])
    L(bp.outputs[0], b.inputs["Normal"])
    # Shadow rays pass through so the pool lights and the sky reach the tiles.
    lp = nt.nodes.new("ShaderNodeLightPath")
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    tr.inputs[0].default_value = (0.8, 0.95, 0.96, 1)
    mx = nt.nodes.new("ShaderNodeMixShader")
    L(lp.outputs["Is Shadow Ray"], mx.inputs[0])
    L(b.outputs[0], mx.inputs[1])
    L(tr.outputs[0], mx.inputs[2])
    L(mx.outputs[0], nt.nodes["Material Output"].inputs[0])
    return m


WHITE = tex(pbr("white", (0.82, 0.82, 0.80), 0.4), (0.76, 0.76, 0.74), size=2, bump=0.02, bevel=0.015)
PLASTER = pbr("plaster", (0.8, 0.79, 0.76), 0.6)
SOFFIT = tex(pbr("soffit", (0.42, 0.26, 0.13), 0.45), (0.28, 0.16, 0.08), scale=(0.4, 9, 9), bump=0.05)
WOODV = tex(pbr("woodV", (0.36, 0.21, 0.10), 0.45), (0.22, 0.12, 0.06), scale=(9, 9, 0.4), bump=0.05)
WOODF = tex(pbr("walnut", (0.20, 0.11, 0.055), 0.35), (0.12, 0.065, 0.03), scale=(0.6, 12, 12), bump=0.03)
FLOORW = tex(pbr("floor_wood", (0.38, 0.24, 0.13), 0.3), (0.27, 0.16, 0.085), scale=(0.5, 10, 10), bump=0.02)
FLOOR1 = tex(pbr("floor_stone", (0.62, 0.60, 0.57), 0.14), (0.56, 0.54, 0.51), "tile", size=1.6, bump=0.03)
DECK = tex(pbr("deck", (0.50, 0.48, 0.45), 0.55), (0.43, 0.41, 0.39), "tile", size=1.2, bump=0.15, bevel=0.01)
PAVER = tex(pbr("paver", (0.42, 0.41, 0.39), 0.65), (0.33, 0.32, 0.30), size=3, bump=0.2, bevel=0.015)
DECKW = tex(pbr("deck_wood", (0.30, 0.19, 0.11), 0.5), (0.2, 0.12, 0.07), scale=(0.5, 14, 14), bump=0.1)
CORE = tex(pbr("core", (0.27, 0.155, 0.075), 0.4), (0.15, 0.085, 0.04), scale=(30, 30, 0.2), size=4, bump=0.5, bevel=0.01)  # fluted timber
STONE = tex(pbr("stone", (0.48, 0.44, 0.38), 0.6), (0.36, 0.32, 0.27), scale=(0.3, 0.3, 7), size=3, bump=0.25, bevel=0.01)
MARBLE = tex(pbr("marble", (0.86, 0.85, 0.83), 0.12), (0.58, 0.57, 0.56), size=1.5, bump=0.0)
TILE = tex(pbr("pool_tile", (0.16, 0.62, 0.70), 0.2), (0.10, 0.48, 0.60), "tile", size=0.3, bump=0.05)
FRAME = pbr("bronze", (0.20, 0.14, 0.09), 0.35, 1.0)
STEEL = pbr("steel", (0.62, 0.62, 0.64), 0.28, 1.0)
BLACK = pbr("black", (0.02, 0.02, 0.02), 0.4)
CAB = pbr("cabinet", (0.2, 0.16, 0.13), 0.45)
SOFA = tex(pbr("sofa", (0.70, 0.67, 0.61), 0.95, Sheen_Weight=0.5), (0.6, 0.57, 0.52), size=250, bump=0.3)
ACC = tex(pbr("accent", (0.07, 0.19, 0.21), 0.9, Sheen_Weight=0.5), (0.05, 0.14, 0.16), size=250, bump=0.3)
OUTF = tex(pbr("outdoor_fabric", (0.55, 0.56, 0.55), 0.9), (0.47, 0.48, 0.47), size=200, bump=0.3)
LEATHER = pbr("leather", (0.22, 0.11, 0.055), 0.45)
LINEN = tex(pbr("linen", (0.85, 0.84, 0.80), 0.9), (0.78, 0.77, 0.73), size=120, bump=0.2)
DUVET = tex(pbr("duvet", (0.28, 0.34, 0.38), 0.9), (0.22, 0.27, 0.31), size=120, bump=0.2)
HEAD = tex(pbr("headboard", (0.36, 0.30, 0.24), 0.9), (0.3, 0.25, 0.2), size=200, bump=0.3)
RUG = tex(pbr("rug", (0.55, 0.50, 0.42), 1.0), (0.45, 0.41, 0.34), size=40, bump=0.4)
POT = pbr("pot", (0.7, 0.68, 0.65), 0.6)
BOOKS = [pbr("book%d" % i, c, 0.7) for i, c in enumerate(((0.5, 0.42, 0.3), (0.12, 0.2, 0.25), (0.75, 0.72, 0.65), (0.35, 0.1, 0.07)))]
BARK = tex(pbr("bark", (0.12, 0.08, 0.05), 0.9), (0.06, 0.04, 0.025), scale=(8, 8, 1), bump=0.6)
LEAF = foliage("leaf", ((0.02, 0.08, 0.015), (0.10, 0.25, 0.04), (0.24, 0.36, 0.07)))
LEAF2 = foliage("leaf_dark", ((0.01, 0.05, 0.02), (0.05, 0.15, 0.05), (0.12, 0.24, 0.08)))
BLOSSOM = foliage("blossom", ((0.75, 0.42, 0.48), (0.95, 0.80, 0.82), (0.92, 0.62, 0.68)))
HEDGE = foliage("hedge", ((0.015, 0.06, 0.015), (0.06, 0.17, 0.035), (0.13, 0.25, 0.05)))
HEDGE_CORE = pbr("hedge_core", (0.01, 0.03, 0.01), 0.9)
GRASS = foliage("grass", ((0.04, 0.13, 0.025), (0.16, 0.36, 0.08), (0.30, 0.46, 0.12)), hair=True)
MEADOW = tex(pbr("meadow", (0.09, 0.15, 0.045), 0.9), (0.05, 0.09, 0.025), size=0.35, bump=0.5)
HILL = pbr("hill", (0.03, 0.05, 0.06), 0.95, Emission_Color=(0.2, 0.25, 0.4, 1), Emission_Strength=0.12)  # faint haze
LED = glow("led", (1.0, 0.72, 0.42), 1.8)
LAMP = glow("lamp", (1.0, 0.78, 0.5), 14)
DOWN = glow("downlight", (1.0, 0.82, 0.6), 60)
FIRE = glow("fire", (1.0, 0.33, 0.05), 30)
GLASS = glass_mat()
WATER = water_mat()

# ---------------------------------------------------------------- geometry helpers
def obj(name, bm, mats, parent=None, loc=(0, 0, 0)):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats if isinstance(mats, (list, tuple)) else [mats]:
        me.materials.append(m)
    o = bpy.data.objects.new(name, me)
    o.location = loc
    o.parent = parent
    C.objects.link(o)
    return o


def box(x0, x1, y0, y1, z0, z1, m, bevel=0.0, p=None, puff=0.0):
    """Box in local metres. `bevel` rounds the edges; `puff` also inflates it a little, for cushions."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1, matrix=Matrix.Diagonal((x1 - x0, y1 - y0, z1 - z0, 1)))
    o = obj("box", bm, m, p, ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    if bevel:
        b = o.modifiers.new("bevel", "BEVEL")
        b.width = bevel
        b.segments = 2 if puff else 3
        if puff:
            sub = o.modifiers.new("subdivide", "SUBSURF")
            sub.levels = sub.render_levels = 2
            o.modifiers.new("inflate", "CAST").factor = puff
        else:
            o.modifiers.new("normals", "WEIGHTED_NORMAL")
        o.data.shade_smooth()
    return o


def rod(p0, p1, r, m, p=None, r2=None, seg=20, cap=True):
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=cap, segments=seg, radius1=r, radius2=r if r2 is None else r2, depth=d.length)
    for f in bm.faces:
        f.smooth = len(f.verts) == 4
    o = obj("rod", bm, m, p, (p0 + p1) / 2)
    o.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    return o


def ball(x, y, z, r, m, p=None, sc=(1, 1, 1)):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=3, radius=r, matrix=Matrix.Diagonal((*sc, 1)))
    for f in bm.faces:
        f.smooth = True
    return obj("ball", bm, m, p, (x, y, z))


def rr(x0, x1, y0, y1, r):
    """Rounded rectangle outline, counter-clockwise, 16 points per corner."""
    return [(cx + r * math.cos(math.radians(a0 + i * 6)), cy + r * math.sin(math.radians(a0 + i * 6)))
            for cx, cy, a0 in ((x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180), (x1 - r, y0 + r, 270))
            for i in range(16)]


def oval(cx, cy, rx, ry, n=64):
    return [(cx + rx * math.cos(i * math.tau / n), cy + ry * math.sin(i * math.tau / n)) for i in range(n)]


def prism(pts, z0, z1, m, hole=None, flat=False):
    """Extrude an outline; with `hole` (same point count) it becomes a ring."""
    bm = bmesh.new()
    n = len(pts)

    def loop(ps, flip):
        vb = [bm.verts.new((x, y, z0)) for x, y in ps]
        vt = [bm.verts.new((x, y, z1)) for x, y in ps]
        for i in range(n):
            q = (vb[i - 1], vb[i], vt[i], vt[i - 1])
            bm.faces.new(q[::-1] if flip else q).smooth = not flat
        return vb, vt

    ob, ot = loop(pts, False)
    if hole:
        ib, it = loop(hole, True)
        for i in range(n):
            bm.faces.new((ot[i - 1], ot[i], it[i], it[i - 1]))
            bm.faces.new((ob[i], ob[i - 1], ib[i - 1], ib[i]))
    else:
        bm.faces.new(ot)
        bm.faces.new(ob[::-1])
    return obj("prism", bm, m)


def rail(pts, z0, h=1.05):
    """Frameless glass balustrade along a polyline, with a thin metal top edge."""
    bm = bmesh.new()
    rows = [[bm.verts.new((x, y, z)) for x, y in pts] for z in (z0, z0 + h - 0.04, z0 + h)]
    for k in (0, 1):
        for i in range(1, len(pts)):
            f = bm.faces.new((rows[k][i - 1], rows[k][i], rows[k + 1][i], rows[k + 1][i - 1]))
            f.material_index = k
            f.smooth = True
    return obj("rail", bm, [GLASS, FRAME])


def quad(bm, p, s, rng, mi):
    u = Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1))).normalized()
    v = u.cross(Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1)))).normalized()
    s *= rng.uniform(0.6, 1.3)
    f = bm.faces.new([bm.verts.new(p + u * a * s + v * b * s) for a, b in ((-1, 0), (0, -0.45), (1, 0), (0, 0.45))])  # leaf-shaped diamond
    f.material_index = mi


def rand_dir(rng):
    return Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1))).normalized()


def G(x, y, z, rot=0.0):
    """Parent empty for a furniture piece; children use local coordinates, front is local -Y."""
    e = bpy.data.objects.new("group", None)
    e.location = (x, y, z)
    e.rotation_euler.z = math.radians(rot)
    C.objects.link(e)
    return e


def light(kind, loc, power, size=0.1, col=(1.0, 0.84, 0.66), up=False, spot=70):
    d = bpy.data.lights.new(kind, kind)
    d.energy = power * (LUX if kind == "AREA" else 1)
    d.color = col
    if kind == "AREA":
        d.shape = "RECTANGLE"
        d.size, d.size_y = size
    else:
        d.shadow_soft_size = size
        if kind == "SPOT":
            d.spot_size = math.radians(spot)
            d.spot_blend = 0.6
    o = bpy.data.objects.new(kind, d)
    o.location = loc
    o.visible_glossy = kind != "AREA"  # no ceiling-panel rectangles mirrored in the glazing
    if up:
        o.rotation_euler.x = math.pi
    C.objects.link(o)
    return o


def room_light(loc, c, power, size):
    """Downlight panel for the floor plus a soft globe so walls and the timber ceiling are lit too."""
    light("AREA", (*loc, c - 0.05), power * 0.6, size)
    light("POINT", (*loc, c - 1.4), power * LUX * 0.5, 0.15)


def inst(me, x, y, z=0.0, s=1.0, p=None):
    o = bpy.data.objects.new(me.name, me)
    o.location = (x, y, z)
    o.scale = (s, s, s * R.uniform(0.88, 1.15))
    o.rotation_euler.z = R.random() * math.tau
    o.parent = p
    C.objects.link(o)
    return o


# ---------------------------------------------------------------- plants
def tree_mesh(seed, leaf, tall=False, n=45000):
    rng = random.Random(seed)
    bm = bmesh.new()

    def limb(a, b, r0, r1):
        d = b - a
        mat = Matrix.Translation((a + b) / 2) @ d.to_track_quat("Z", "Y").to_matrix().to_4x4()
        bmesh.ops.create_cone(bm, cap_ends=False, segments=8, radius1=r0, radius2=r1, depth=d.length, matrix=mat)

    fork = Vector((rng.uniform(-0.3, 0.3), rng.uniform(-0.3, 0.3), 2.6 if tall else 3.6))
    limb(Vector((0, 0, -0.3)), fork, 0.26, 0.17)
    blobs = []
    for i in range(14):
        if tall:
            blobs.append((Vector((rng.uniform(-0.3, 0.3), rng.uniform(-0.3, 0.3), 2.4 + i * 0.5)), 1.4 - 0.075 * i))
        else:  # blob centres fill a wide ellipsoid crown
            d = rand_dir(rng) * rng.uniform(0.35, 1) ** 0.5
            blobs.append((fork + Vector((d.x * 3.4, d.y * 3.4, 3.0 + d.z * 2.4)), rng.uniform(1.0, 1.7)))
    for c, r in blobs:
        limb(fork, c, 0.1, 0.02)
    for f in bm.faces:
        f.smooth = True
    for c, r in blobs:
        for _ in range(n // len(blobs)):
            d = rand_dir(rng) * r * rng.random() ** 0.4
            d.z *= 0.8
            quad(bm, c + d, 0.085, rng, 1)
    me = bpy.data.meshes.new("tree")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(BARK)
    me.materials.append(leaf)
    return me


def bush_mesh(leaf, n=900):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.36)
    for _ in range(n):
        d = rand_dir(R) * 0.5 * R.uniform(0.75, 1)
        d.z *= 0.85
        quad(bm, d, 0.075, R, 1)
    me = bpy.data.meshes.new("bush")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(HEDGE_CORE)
    me.materials.append(leaf)
    return me


def hedge(x0, x1, y0, y1, h, density=300):
    box(x0 + 0.1, x1 - 0.1, y0 + 0.1, y1 - 0.1, 0, h - 0.1, HEDGE_CORE)
    dx, dy = x1 - x0, y1 - y0
    faces = [(0, x0), (0, x1), (1, y0), (1, y1), (2, h)]
    wts = [dy * h, dy * h, dx * h, dx * h, dx * dy]
    bm = bmesh.new()
    for ax, v in R.choices(faces, wts, k=int(sum(wts) * density)):
        p = Vector((x0 + R.random() * dx, y0 + R.random() * dy, R.random() * h))
        p[ax] = v + R.uniform(-0.08, 0.08)
        quad(bm, p, 0.06, R, 0)
    obj("hedge", bm, HEDGE)


TREES = [tree_mesh(1, LEAF), tree_mesh(2, LEAF2), tree_mesh(3, LEAF), tree_mesh(4, LEAF2, tall=True)]
BLOSSOM_TREE = tree_mesh(5, BLOSSOM, n=30000)
BUSH = bush_mesh(HEDGE)
BUSH_FLOWER = bush_mesh(BLOSSOM)


def plant(x, y, z, h=1.8, s=1.0):
    g = G(x, y, z)
    rod((0, 0, 0), (0, 0, 0.45 * s), 0.2 * s, POT, g, r2=0.26 * s)
    rod((0, 0, 0.4 * s), (0, 0, h - 0.4), 0.025, BARK, g, seg=8)
    inst(BUSH, 0, 0, h - 0.35, 0.9 * s, g)


def shrubs(x0, x1, y0, y1, n, z=0.28):
    for _ in range(n):
        o = inst(BUSH_FLOWER if R.random() < 0.15 else BUSH, R.uniform(x0, x1), R.uniform(y0, y1), z, R.uniform(0.8, 1.5))
        o.scale.z *= 0.75


def planter(x0, x1, y0, y1, z, h=0.5):
    box(x0, x1, y0, y1, z, z + h, POT)
    along_x = x1 - x0 > y1 - y0
    n = max(1, int((x1 - x0 if along_x else y1 - y0) / 0.6))
    for i in range(n):
        f = (i + 0.5) / n
        inst(BUSH, x0 + (x1 - x0) * (f if along_x else 0.5), y0 + (y1 - y0) * (0.5 if along_x else f), z + h + 0.25, R.uniform(0.7, 1.0))


# ---------------------------------------------------------------- furniture
ASSETS = os.path.join(OUT, "assets")
MODELS = {}


def model(name, x, y, z, rot=0.0, s=1.0):
    """Place a Poly Haven model from assets/<name>/. Front faces local -Y, origin on the floor; repeats share data."""
    if name not in MODELS:
        path = os.path.join(ASSETS, name, name + ".gltf")
        assert os.path.exists(path), "missing %s: run `python fetch_assets.py` first" % path
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=path)
        MODELS[name] = bpy.data.collections.new(name)
        for o in set(bpy.data.objects) - before:
            for c in o.users_collection:
                c.objects.unlink(o)
            MODELS[name].objects.link(o)
    e = G(x, y, z, rot)
    e.scale = (s, s, s)
    e.instance_type = "COLLECTION"
    e.instance_collection = MODELS[name]
    return e


def pendant(x, y, floor, ceiling, drop=1.75):
    """Glass globe pendant whose underside hangs `drop` above the floor."""
    first = "modern_ceiling_lamp_01" not in MODELS
    model("modern_ceiling_lamp_01", x, y, floor + drop - 0.22)   # the model's origin sits 0.22 m below the globe
    rod((x, y, floor + drop + 0.94), (x, y, ceiling), 0.004, BLACK, seg=6)
    if first:  # switch the lamp on
        b = bpy.data.materials["modern_ceiling_globe"].node_tree.nodes["Principled BSDF"]
        b.inputs["Emission Color"].default_value = (1.0, 0.8, 0.55, 1)
        b.inputs["Emission Strength"].default_value = 12


def sofa(x, y, z, rot, w, m=None, d=0.95, chaise=0):
    g = G(x, y, z, rot)
    m = m or SOFA
    box(-w / 2 + 0.06, w / 2 - 0.06, -d / 2 + 0.06, d / 2 - 0.06, 0, 0.1, BLACK, 0, g)
    box(-w / 2, w / 2, -d / 2, d / 2, 0.1, 0.27, m, 0.03, g)
    box(-w / 2, w / 2, d / 2 - 0.2, d / 2, 0.27, 0.72, m, 0.06, g, 0.05)
    for sx in (-1, 1):
        box(min(sx * w / 2, sx * (w / 2 - 0.2)), max(sx * w / 2, sx * (w / 2 - 0.2)), -d / 2, d / 2, 0.27, 0.6, m, 0.06, g, 0.05)
    n = max(1, round((w - 0.4) / 0.85))
    cw = (w - 0.4) / n
    for i in range(n):
        x0 = -w / 2 + 0.2 + i * cw
        box(x0 + 0.005, x0 + cw - 0.005, -d / 2 - 0.02, d / 2 - 0.2, 0.27, 0.46, m, 0.05, g, 0.1)
        box(x0 + 0.02, x0 + cw - 0.02, d / 2 - 0.42, d / 2 - 0.2, 0.44, 0.86, m, 0.06, g, 0.16).rotation_euler.x = -0.12
    if chaise:
        x0 = min(chaise * w / 2, chaise * (w / 2 - 1.0))
        box(x0, x0 + 1.0, -d / 2 - 0.8, -d / 2 + 0.02, 0.1, 0.46, m, 0.05, g, 0.06)
    if w > 1.5:
        for sx in (-1, 1):
            c = box(sx * (w / 2 - 0.5) - 0.22, sx * (w / 2 - 0.5) + 0.22, d / 2 - 0.58, d / 2 - 0.44, 0.48, 0.9, ACC if m is SOFA else LINEN, 0.05, g, 0.3)
            c.rotation_euler.x = -0.25


def table(x, y, z, rot, w, d, h, top, leg, round_=False):
    g = G(x, y, z, rot)
    if round_:
        rod((0, 0, h - 0.04), (0, 0, h), w / 2, top, g, seg=48)
        rod((0, 0, 0.02), (0, 0, h - 0.04), w * 0.07, leg, g)
        rod((0, 0, 0), (0, 0, 0.02), w * 0.3, leg, g, seg=32)
    else:
        box(-w / 2, w / 2, -d / 2, d / 2, h - 0.05, h, top, 0.008, g)
        for sx in (-1, 1):
            box(sx * (w / 2 - 0.3) - 0.03, sx * (w / 2 - 0.3) + 0.03, -d / 2 + 0.08, d / 2 - 0.08, 0, h - 0.05, leg, 0, g)


def chair(x, y, z, rot, m=None):
    g = G(x, y, z, rot)
    m = m or LEATHER
    box(-0.24, 0.24, -0.24, 0.24, 0.42, 0.5, m, 0.03, g)
    box(-0.24, 0.24, 0.2, 0.27, 0.5, 0.9, m, 0.03, g).rotation_euler.x = -0.12
    for sx in (-1, 1):
        for sy in (-1, 1):
            rod((sx * 0.2, sy * 0.2, 0), (sx * 0.2, sy * 0.2, 0.42), 0.012, BLACK, g, seg=8)


def stool(x, y, z):
    g = G(x, y, z)
    rod((0, 0, 0.7), (0, 0, 0.76), 0.19, LEATHER, g)
    rod((0, 0, 0.02), (0, 0, 0.7), 0.02, BLACK, g, seg=8)
    rod((0, 0, 0), (0, 0, 0.02), 0.2, BLACK, g)


def sheet(x0, x1, y0, y1, z, m, n=0.035):
    """Flat cloth panel as a dense grid, ready for the cloth simulation."""
    bm = bmesh.new()
    mat = Matrix.Translation(((x0 + x1) / 2, (y0 + y1) / 2, z)) @ Matrix.Diagonal((x1 - x0, y1 - y0, 1, 1))
    bmesh.ops.create_grid(bm, x_segments=round((x1 - x0) / n), y_segments=round((y1 - y0) / n), size=0.5, matrix=mat)
    for f in bm.faces:
        f.smooth = True
    return obj("cloth", bm, m)


def drape(cloth, colliders, frames=45, thick=0.03):
    """Let a cloth panel fall onto the colliders, then freeze the result and give it thickness."""
    for o in colliders:
        if not any(m.type == "COLLISION" for m in o.modifiers):
            o.modifiers.new("collide", "COLLISION")
            o.collision.thickness_outer = 0.012
            o.collision.cloth_friction = 30
    cm = cloth.modifiers.new("cloth", "CLOTH")
    cm.settings.quality = 7
    cm.settings.mass = 0.25
    cm.settings.tension_stiffness = cm.settings.compression_stiffness = 12
    cm.settings.shear_stiffness = 6
    cm.settings.bending_stiffness = 0.4
    cm.settings.shrink_min = -0.06  # slack, so the cloth wrinkles instead of lying dead flat
    cm.collision_settings.distance_min = 0.012
    cm.collision_settings.use_self_collision = False
    for f in range(1, frames + 1):
        S.frame_set(f)
    frozen = bpy.data.meshes.new_from_object(cloth.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    cloth.modifiers.clear()
    cloth.data = frozen
    S.frame_set(1)
    so = cloth.modifiers.new("thickness", "SOLIDIFY")
    so.thickness = thick
    so.offset = 1
    sub = cloth.modifiers.new("smooth", "SUBSURF")
    sub.levels = sub.render_levels = 1
    return cloth


def pillow(x, y, z, w, d, t, m, tilt=0.0, rot=0.0):
    """A lens-shaped cushion that thins to a seam, with pointed corners and a little unevenness."""
    bm = bmesh.new()
    n = 24
    grid = {}
    for side in (1, -1):
        for i in range(n + 1):
            for j in range(n + 1):
                u, v = i / n * 2 - 1, j / n * 2 - 1
                if side == -1 and max(abs(u), abs(v)) == 1:
                    grid[(side, i, j)] = grid[(1, i, j)]  # both faces share the seam
                    continue
                h = ((1 - u * u) * (1 - v * v)) ** 0.42
                pinch = 1 - 0.1 * (1 - abs(u * v)) * max(abs(u), abs(v)) ** 6  # sides pull in between the corners
                wob = 1 + 0.06 * math.sin(3.1 * u + 1.7 * v + side) * math.sin(2.3 * v - u)
                grid[(side, i, j)] = bm.verts.new((u * w / 2 * pinch, v * d / 2 * pinch, side * t / 2 * h * wob))
        for i in range(n):
            for j in range(n):
                q = [grid[(side, i, j)], grid[(side, i + 1, j)], grid[(side, i + 1, j + 1)], grid[(side, i, j + 1)]]
                bm.faces.new(q if side == 1 else q[::-1]).smooth = True
    o = obj("pillow", bm, m, None, (x, y, z))
    o.rotation_euler = (tilt, 0, math.radians(rot))
    return o


def make_bed(w=2.0, l=2.1):
    """Platform bed at the origin, head at +Y, as a collection: simulated once, then instanced."""
    before = set(C.objects)
    box(-w / 2 + 0.2, w / 2 - 0.2, -l / 2 + 0.2, l / 2 - 0.1, 0, 0.1, BLACK)
    frame = box(-w / 2 - 0.14, w / 2 + 0.14, -l / 2 - 0.14, l / 2 + 0.02, 0.1, 0.3, WOODF, 0.02)
    mattress = box(-w / 2, w / 2, -l / 2, l / 2, 0.3, 0.56, LINEN, 0.07)
    box(-w / 2 - 0.5, w / 2 + 0.5, l / 2 + 0.06, l / 2 + 0.12, 0.1, 1.22, WOODF, 0.01)
    k = round((w + 0.9) / 0.2)  # channel-tufted headboard: vertical padded strips
    for i in range(k):
        x0 = -w / 2 - 0.45 + (w + 0.9) * i / k
        box(x0 + 0.004, x0 + (w + 0.9) / k - 0.004, l / 2 - 0.02, l / 2 + 0.08, 0.34, 1.18, HEAD, 0.035, None, 0.02)
    for sx in (-1, 1):
        pillow(sx * w / 4, l / 2 - 0.22, 0.78, 0.74, 0.5, 0.2, LINEN, math.radians(58), sx * -3)
        pillow(sx * (w / 4 - 0.06), l / 2 - 0.45, 0.72, 0.52, 0.52, 0.17, ACC if sx < 0 else DUVET, math.radians(52), sx * 6)
        nx = sx * (w / 2 + 0.5)
        model("side_table_01", nx, l / 2 - 0.28, 0, 90 * sx)
        rod((nx, l / 2 - 0.28, 0.55), (nx, l / 2 - 0.28, 0.6), 0.05, BLACK)
        ball(nx, l / 2 - 0.28, 0.71, 0.11, LAMP)
    model("alarm_clock_01", w / 2 + 0.36, l / 2 - 0.42, 0.55, 25)
    model("standing_picture_frame_01", -w / 2 - 0.38, l / 2 - 0.42, 0.55, -110)
    box(-w / 2 - 0.02, w / 2 + 0.02, l / 2 - 0.86, l / 2 - 0.6, 0.555, 0.59, LINEN, 0.015, None, 0.04)  # turned-down sheet
    duvet = drape(sheet(-w / 2 - 0.26, w / 2 + 0.26, -l / 2 - 0.22, l / 2 - 0.66, 0.62, DUVET), [mattress, frame], thick=0.035)
    drape(sheet(-w / 2 - 0.2, w / 2 + 0.2, -l / 2 + 0.12, -l / 2 + 0.72, 0.75, ACC, 0.03), [duvet, mattress, frame], frames=40, thick=0.012)
    coll = bpy.data.collections.new("bed")
    for o in [o for o in C.objects if o not in before]:
        C.objects.unlink(o)
        coll.objects.link(o)
    return coll


BED = make_bed()  # built before the rest of the scene exists, so stepping the simulation stays quick


def bed(x, y, z, rot):
    e = G(x, y, z, rot)
    e.instance_type = "COLLECTION"
    e.instance_collection = BED


def lamp(x, y, z, h=1.7):
    g = G(x, y, z)
    rod((0, 0, 0), (0, 0, 0.02), 0.16, BLACK, g)
    rod((0, 0, 0.02), (0, 0, h), 0.012, BLACK, g, seg=8)
    ball(0, 0, h + 0.14, 0.16, LAMP, g)


def pend(x, y, zc, drop, r):
    rod((x, y, zc - drop), (x, y, zc), 0.006, BLACK, seg=6)
    ball(x, y, zc - drop - r, r, LAMP)


def shelf(x, y, z, rot, w, h, d=0.32):
    g = G(x, y, z, rot)
    box(-w / 2, w / 2, d / 2 - 0.02, d / 2, 0, h, CAB, 0, g)
    n = max(1, int(w / 0.9))
    for i in range(n + 1):
        xx = -w / 2 + i * w / n
        box(xx - 0.015, xx + 0.015, -d / 2, d / 2, 0, h, CAB, 0, g)
    zz = 0.0
    while zz < h:
        box(-w / 2, w / 2, -d / 2, d / 2, zz, zz + 0.03, CAB, 0, g)
        xx = -w / 2 + 0.05
        while zz + 0.4 < h and xx < w / 2 - 0.15:
            bw = R.uniform(0.04, 0.09)
            if R.random() < 0.65:
                box(xx, xx + bw, -d / 2 + 0.04, d / 2 - 0.06, zz + 0.03, zz + R.uniform(0.2, 0.33), R.choice(BOOKS), 0, g)
            else:
                xx += R.uniform(0.1, 0.35)
            xx += bw + 0.004
        zz += 0.42


def lounger(x, y, z, rot):
    g = G(x, y, z, rot)  # head at local +Y
    box(-0.35, 0.35, -1.0, 0.45, 0.2, 0.26, DECKW, 0.01, g)
    box(-0.33, 0.33, -0.98, 0.43, 0.26, 0.34, LINEN, 0.03, g)
    b = box(-0.33, 0.33, 0.4, 1.05, 0.3, 0.38, LINEN, 0.03, g)
    b.rotation_euler.x = 0.5
    b.location.z += 0.14
    for sy in (-0.8, 0.3):
        box(-0.33, 0.33, sy - 0.03, sy + 0.03, 0, 0.2, BLACK, 0, g)


# ---------------------------------------------------------------- bathrooms, wardrobes and small things
PORCELAIN = pbr("porcelain", (0.9, 0.9, 0.88), 0.06)
CHROME = pbr("chrome", (0.8, 0.8, 0.82), 0.1, 1.0)
MIRROR = pbr("mirror", (0.92, 0.92, 0.92), 0.0, 1.0)
SCREEN = pbr("screen", (0.01, 0.01, 0.012), 0.06)
LAPTOP = glow("laptop_screen", (0.45, 0.6, 0.9), 1.2)
SHEER = foliage("sheer", ((0.8, 0.78, 0.73), (0.85, 0.83, 0.78)))  # same light-through-cloth shader as the leaves
BOTTLES = [pbr("amber", (0.3, 0.13, 0.03), 0.12), pbr("sage", (0.42, 0.5, 0.42), 0.3), pbr("cream", (0.88, 0.87, 0.84), 0.25),
           pbr("charcoal", (0.05, 0.05, 0.055), 0.3)]
CLOTHES = [tex(pbr("cloth%d" % i, c, 0.9, Sheen_Weight=0.4), tuple(v * 0.8 for v in c), size=220, bump=0.25)
           for i, c in enumerate(((0.85, 0.84, 0.8), (0.05, 0.08, 0.15), (0.28, 0.28, 0.3), (0.55, 0.46, 0.35), (0.19, 0.24, 0.17),
                                  (0.03, 0.03, 0.035), (0.58, 0.36, 0.34), (0.1, 0.2, 0.28), (0.72, 0.68, 0.58), (0.45, 0.12, 0.1)))]


def at(g, lx, ly, lz=0.0):
    """World position of a point given in the local coordinates of group g."""
    a = g.rotation_euler.z
    return (g.location.x + lx * math.cos(a) - ly * math.sin(a), g.location.y + lx * math.sin(a) + ly * math.cos(a), g.location.z + lz)


def bottle(x, y, z, h=0.2, r=0.035, m=None, pump=True, p=None):
    m = m or R.choice(BOTTLES)
    rod((x, y, z), (x, y, z + h * 0.7), r, m, p, seg=14)
    rod((x, y, z + h * 0.7), (x, y, z + h * 0.8), r, m, p, r2=r * 0.4, seg=14)
    rod((x, y, z + h * 0.8), (x, y, z + h), r * 0.35, BLACK, p, seg=10)
    if pump:
        rod((x, y, z + h), (x + r * 1.5, y, z + h), 0.006, BLACK, p, seg=6)


def stack(x, y, z, n, p=None, w=0.34, d=0.24, mats=None):
    """Pile of folded towels or clothes."""
    for i in range(n):
        box(x - w / 2, x + w / 2, y - d / 2, y + d / 2, z + i * 0.052, z + i * 0.052 + 0.05,
            R.choice(mats or (LINEN, LINEN, DUVET, ACC)), 0.018, p, 0.04)


def toilet(x, y, z, rot):
    """Wall-hung toilet in front of a boxed-in cistern ledge. (x, y) is on the wall, which is at local +Y."""
    g = G(x, y, z, rot)
    box(-0.5, 0.5, -0.18, 0, 0, 1.12, STONE, 0, g)
    box(-0.52, 0.52, -0.2, 0, 1.12, 1.15, MARBLE, 0.004, g)
    box(-0.19, 0.19, -0.74, -0.18, 0.1, 0.42, PORCELAIN, 0.09, g, 0.12)
    cx, cy, _ = at(g, 0, -0.46)
    sideways = round(rot / 90) % 2  # the lid's long axis follows the way the toilet faces
    prism(oval(cx, cy, *((0.27, 0.185) if sideways else (0.185, 0.27))), z + 0.415, z + 0.45, PORCELAIN)
    box(-0.12, 0.12, -0.19, -0.18, 0.86, 1.0, CHROME, 0, g)
    for dx, dz in ((0.24, 0), (0.36, 0), (0.3, 0.105)):  # spare rolls
        rod((dx, -0.09, 1.15 + dz), (dx, -0.09, 1.25 + dz), 0.055, LINEN, g, seg=16)
    bottle(-0.32, -0.09, 1.15, 0.15, 0.032, BOTTLES[0], False, g)  # reed diffuser
    for a in (-0.3, 0, 0.3):
        rod((-0.32, -0.09, 1.28), (-0.32 + a * 0.3, -0.07, 1.52), 0.002, BARK, g, seg=4)
    rod((0.42, -0.3, 0), (0.42, -0.3, 0.36), 0.045, BLACK, g, seg=14)   # brush
    rod((0.42, -0.3, 0.36), (0.42, -0.3, 0.48), 0.008, CHROME, g, seg=6)


def vanity(x, y, z, rot, w=1.2):
    """Floating vanity with vessel basins, taps, a backlit mirror and toiletries. The wall is at local +Y."""
    g = G(x, y, z, rot)
    box(-w / 2, w / 2, -0.5, 0, 0.45, 0.8, WOODF, 0.004, g)
    box(-w / 2 - 0.01, w / 2 + 0.01, -0.52, 0, 0.8, 0.84, MARBLE, 0.004, g)
    box(-w / 2 + 0.03, w / 2 - 0.03, -0.012, 0, 1.03, 1.97, LED, 0, g)        # halo behind the mirror
    box(-w / 2 + 0.06, w / 2 - 0.06, -0.03, -0.012, 1.06, 1.94, MIRROR, 0, g)
    n = 2 if w > 1.5 else 1
    for i in range(n):
        bx = (i - (n - 1) / 2) * w / 2
        rod((bx, -0.27, 0.84), (bx, -0.27, 0.97), 0.15, PORCELAIN, g, r2=0.2, seg=32, cap=False)
        rod((bx, -0.27, 0.84), (bx, -0.27, 0.846), 0.15, PORCELAIN, g, seg=32)
        rod((bx, -0.06, 0.84), (bx, -0.06, 1.1), 0.012, CHROME, g, seg=10)
        rod((bx, -0.06, 1.1), (bx, -0.2, 1.1), 0.012, CHROME, g, seg=10)
        bottle(bx + 0.28, -0.1, 0.84, 0.19, 0.032, None, True, g)
        bottle(bx + 0.36, -0.2, 0.84, 0.15, 0.028, None, True, g)
    cx = -w / 2 + 0.12
    rod((cx, -0.1, 0.84), (cx, -0.1, 0.94), 0.035, PORCELAIN, g, seg=14, cap=False)   # toothbrush cup
    for k, m in enumerate((CLOTHES[7], CLOTHES[6])):
        rod((cx, -0.1, 0.85), (cx + 0.03 * (k * 2 - 1), -0.1, 1.03), 0.004, m, g, seg=6)
    stack(-w / 2 + 0.2, -0.36, 0.84, 2, g, 0.26, 0.18)
    box(-w / 2 + 0.05, w / 2 - 0.05, -0.46, -0.04, 0.2, 0.225, WOODF, 0, g)           # open shelf under the unit
    stack(w / 4, -0.25, 0.225, 3, g)
    model("wicker_basket_01", *at(g, -w / 4, -0.25, 0.225), math.degrees(g.rotation_euler.z))


def shoes(x0, x1, z, p):
    xx = x0 + 0.08
    while xx < x1 - 0.2:
        m = R.choice((BLACK, LEATHER, LINEN, CLOTHES[3]))
        for dx in (0, 0.11):
            box(xx + dx - 0.045, xx + dx + 0.045, -0.2, 0.07, z, z + 0.075, m, 0.03, p, 0.1)
            box(xx + dx - 0.04, xx + dx + 0.04, -0.02, 0.07, z + 0.05, z + 0.11, m, 0.02, p)
        xx += 0.3


def wardrobe(x, y, z, rot, w, h=2.5, d=0.6):
    """Open wardrobe, back against local +Y: rails of hanging clothes, folded stacks, shoes and baskets."""
    g = G(x, y, z, rot)
    box(-w / 2, w / 2, d / 2 - 0.02, d / 2, 0, h, WOODF, 0, g)
    box(-w / 2, w / 2, -d / 2, d / 2, 0, 0.08, WOODF, 0, g)
    box(-w / 2, w / 2, -d / 2, d / 2, h - 0.04, h, WOODF, 0, g)
    box(-w / 2 + 0.04, w / 2 - 0.04, -d / 2 + 0.03, -d / 2 + 0.05, h - 0.065, h - 0.045, LED, 0, g)
    n = max(1, round(w / 0.95))
    for i in range(n + 1):
        xx = -w / 2 + i * w / n
        box(xx - 0.012, xx + 0.012, -d / 2, d / 2, 0, h, WOODF, 0, g)
    for i in range(n):
        x0, x1 = -w / 2 + i * w / n + 0.03, -w / 2 + (i + 1) * w / n - 0.03
        kind = ("long", "shelves", "double")[i % 3]
        if kind == "shelves":
            for k, zz in enumerate((0.42, 0.8, 1.18, 1.56, 1.94)):
                box(x0 - 0.02, x1 + 0.02, -d / 2 + 0.02, d / 2 - 0.02, zz - 0.025, zz, WOODF, 0, g)
                if k < 3:
                    for sx in (x0 + 0.2, x1 - 0.2):
                        stack(sx, 0.02, zz, R.randint(2, 5), g, 0.3, 0.4, CLOTHES)
                elif k == 3:
                    model("wicker_basket_02", *at(g, (x0 + x1) / 2 - 0.2, 0, zz), math.degrees(g.rotation_euler.z))
                    model("fishermans_hat", *at(g, (x0 + x1) / 2 + 0.22, 0, zz + 0.03), R.uniform(0, 360))
                else:
                    for sx in (x0 + 0.2, x1 - 0.2):  # storage boxes
                        box(sx - 0.17, sx + 0.17, -0.2, 0.18, zz, zz + 0.22, R.choice((LINEN, CLOTHES[8], CLOTHES[2])), 0.01, g)
        else:
            rails = ((h - 0.2, 1.15, 1.4),) if kind == "long" else ((h - 0.2, 0.72, 0.85), (h - 1.22, 0.62, 0.75))
            for top, lmin, lmax in rails:
                rod((x0, 0, top), (x1, 0, top), 0.012, CHROME, g, seg=8)
                xx = x0 + 0.06
                while xx < x1 - 0.05:
                    c = box(xx - 0.02, xx + 0.02, -0.23, 0.2, top - 0.06 - R.uniform(lmin, lmax), top - 0.06, R.choice(CLOTHES), 0.016, g, 0.02)
                    c.rotation_euler.z = R.uniform(-0.1, 0.1)
                    xx += R.uniform(0.055, 0.085)
        if kind != "double":
            shoes(x0, x1, 0.08, g)


def curtain(a, b, z0, z1, folds=7, amp=0.05):
    """Sheer curtain drawn back between two points on the plan: a pleated sheet."""
    (x0, y0), (x1, y1) = a, b
    length = math.hypot(x1 - x0, y1 - y0)
    nx, ny = (y0 - y1) / length, (x1 - x0) / length
    bm = bmesh.new()
    n = folds * 8
    lo, hi = [], []
    for i in range(n + 1):
        t = i / n
        off = amp * math.sin(t * folds * math.tau)
        px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        lo.append(bm.verts.new((px + nx * off, py + ny * off, z0 + 0.02)))
        hi.append(bm.verts.new((px + nx * off * 0.6, py + ny * off * 0.6, z1)))
    for i in range(n):
        bm.faces.new((lo[i], lo[i + 1], hi[i + 1], hi[i])).smooth = True
    return obj("curtain", bm, SHEER)


# ---------------------------------------------------------------- site
HARD = (-15.5, 12, -14, 7)       # paved terrace rectangle
POOL = (-12, 2, -12, -6.5)
PLOT = (-34, 26, -36, 22)
HX0, HX1, HY0, HY1 = HARD
PX0, PX1, PY0, PY1 = POOL


def frame(outer, hole, z, m, cell=None):
    """Flat ground with a rectangular hole. `cell` splits it into a grid: hair scattering stalls on huge faces."""
    (ox0, ox1, oy0, oy1), (ix0, ix1, iy0, iy1) = outer, hole

    def cuts(a, b, extra):
        n = max(1, round((b - a) / cell)) if cell else 1
        return sorted({a + (b - a) * i / n for i in range(n + 1)} | set(extra))

    xs, ys = cuts(ox0, ox1, (ix0, ix1)), cuts(oy0, oy1, (iy0, iy1))
    bm = bmesh.new()
    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            if not (ix0 <= (x0 + x1) / 2 <= ix1 and iy0 <= (y0 + y1) / 2 <= iy1):
                bm.faces.new([bm.verts.new((x, y, z)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))])
    return obj("ground", bm, m)


frame((-900, 900, -900, 900), HARD, -0.02, MEADOW)
lawn = frame(PLOT, HARD, 0.0, GRASS, cell=2)
ps = lawn.modifiers.new("grass", "PARTICLE_SYSTEM").particle_system.settings
ps.type = "HAIR"
ps.count = 15000 if DRAFT else 40000
ps.distribution = "RAND"
ps.hair_length = 0.07
ps.hair_step = 3
ps.length_random = 0.5
ps.factor_random = 0.005  # absolute, not relative to hair_length: 0.4 gave 1.6 m blades
ps.child_type = "INTERPOLATED"
ps.rendered_child_count = 15 if DRAFT else 25
ps.roughness_2 = 0.008
ps.roughness_endpoint = 0.004
ps.root_radius = 0.5
ps.tip_radius = 0.1
ps.radius_scale = 0.01

for x0, x1, y0, y1 in ((HX0, PX0, HY0, HY1), (PX1, HX1, HY0, HY1), (PX0, PX1, HY0, PY0), (PX0, PX1, PY1, HY1)):
    box(x0, x1, y0, y1, -1.75, 0.15, DECK)
box(PX0, PX1, PY0, PY1, -1.75, -1.5, TILE)
for x0, x1, y0, y1 in ((PX0, PX0 + 0.04, PY0, PY1), (PX1 - 0.04, PX1, PY0, PY1), (PX0, PX1, PY0, PY0 + 0.04), (PX0, PX1, PY1 - 0.04, PY1)):
    box(x0, x1, y0, y1, -1.5, 0.149, TILE)
bm = bmesh.new()
bm.faces.new([bm.verts.new((x, y, 0.07)) for x, y in ((PX0, PY0), (PX1, PY0), (PX1, PY1), (PX0, PY1))])
obj("water", bm, WATER)
for x in (-10, -6.5, -3, 0.5):  # on the near wall, so they light the walls the camera sees
    light("POINT", (x, PY0 + 0.25, -0.7), 110, 0.08, (0.6, 0.95, 1.0))

# driveway slabs and stepping stones (tops sit above the grass blades)
for i in range(14):
    box(12.8, 17.6, -34 + i * 2.7, -31.5 + i * 2.7, -0.1, 0.1, PAVER)
for i in range(8):
    box(-5.8, -4.2, -31 + i * 2.1, -30.2 + i * 2.1 + 0.7, -0.1, 0.1, PAVER)

# ---------------------------------------------------------------- house shell
L1 = (-7, 8, -4, 5)
L2 = (-10, 6, -7, 4.5)
L3 = (-3, 9, -3.5, 5.5)
FL = (0.17, 4.02, 7.52)       # finished floor heights
CL = (3.48, 6.93, 10.43)      # ceiling (soffit underside) heights
STAIR, SR = (4.5, -0.3), 1.4  # helical stair centre and radius; the two floors above are open here
WELL = rod((*STAIR, 3.3), (*STAIR, 7.7), SR + 0.12, WHITE, seg=64)
WELL.hide_render = True       # only used to cut the stairwell out of the floors


def carve(o):
    m = o.modifiers.new("stairwell", "BOOLEAN")
    m.object = WELL
    m.operation = "DIFFERENCE"
    return o


def plate(x0, x1, y0, y1, z0, z1, r=1.2, well=True):
    """White floor plate with a recessed LED line and a timber soffit underneath."""
    top = prism(rr(x0, x1, y0, y1, r), z0, z1, WHITE)
    prism(rr(x0 + 0.1, x1 - 0.1, y0 + 0.1, y1 - 0.1, r - 0.1), z0 - 0.04, z0, LED,
          rr(x0 + 0.22, x1 - 0.22, y0 + 0.22, y1 - 0.22, r - 0.22))
    under = prism(rr(x0 + 0.2, x1 - 0.2, y0 + 0.2, y1 - 0.2, r - 0.2), z0 - 0.07, z0, SOFFIT)
    if well:
        carve(top)
        carve(under)


def wall(a, b, z0, z1, m=None, t=0.24):
    """Axis-aligned wall from a to b (a < b). Without a material it is glazing with mullions."""
    (x0, y0), (x1, y1) = a, b
    along_x = y0 == y1
    ex, ey = (0, t / 2) if along_x else (t / 2, 0)
    if m:
        return box(x0 - ex, x1 + ex, y0 - ey, y1 + ey, z0, z1, m)
    gx, gy = (0, 0.012) if along_x else (0.012, 0)
    box(x0 - gx, x1 + gx, y0 - gy, y1 + gy, z0, z1, GLASS)
    mx, my = (0.025, 0.06) if along_x else (0.06, 0.025)
    n = max(1, round((x1 - x0 + y1 - y0) / 2.4))
    for i in range(n + 1):
        x, y = x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n
        box(x - mx, x + mx, y - my, y + my, z0, z1, FRAME)
    for z in (z0, z1 - 0.06):
        box(x0 - mx, x1 + mx, y0 - my, y1 + my, z, z + 0.06, FRAME)


plate(-8, 9.2, -5, 6, 3.55, 3.87)            # level 1 roof
plate(-11.2, 7, -8.2, 5.5, 3.6, 4.0)         # level 2 floor, cantilevered over the terrace
plate(-11.2, 7.2, -8.2, 5.7, 7.0, 7.4)       # level 2 roof and roof terrace
plate(-4.2, 10.2, -4.7, 6.5, 7.05, 7.5)      # level 3 floor
plate(-4.6, 10.6, -5.3, 7, 10.5, 10.82, 1.5, well=False)  # roof

box(*L1, 0.15, FL[0], FLOOR1)
carve(box(*L2, 4.0, FL[1], FLOORW))
carve(box(*L3, 7.5, FL[2], FLOORW))

# lift: a timber-clad shaft with doors facing the rooms on every level
box(3, 6, 1.5, 4.5, 0.15, 10.5, CORE)
for z in FL:
    box(2.955, 3.0, 2.3, 3.7, z, z + 2.35, FRAME)
    for y0, y1 in ((2.42, 2.996), (3.004, 3.58)):
        box(2.94, 2.955, y0, y1, z, z + 2.2, STEEL)
    box(2.94, 2.955, 2.85, 3.15, z + 2.24, z + 2.3, LAMP)
    box(2.94, 3.0, 3.85, 3.93, z + 1.0, z + 1.16, STEEL)


def polar(r, a):
    return STAIR[0] + r * math.cos(math.radians(a)), STAIR[1] + r * math.sin(math.radians(a))


def flight(z0, z1, a0=180.0):
    """One full turn of the helical stair from floor z0 up to floor z1; you step on and off at angle a0."""
    n = round((z1 - z0) / 0.175)
    for i in range(1, n):
        z, a, half = z0 + (z1 - z0) * i / n, a0 + 360 * i / n, 200 / n
        prism([polar(0.1, a - half), polar(SR - 0.03, a - half), polar(SR - 0.03, a + half), polar(0.1, a + half)], z - 0.05, z, FLOORW, flat=True)
    prism([polar(0.1, a0 - 24), polar(SR + 0.11, a0 - 24), polar(SR + 0.11, a0 + 24), polar(0.1, a0 + 24)], z1 - 0.06, z1, FLOORW, flat=True)
    # outer stringer, glass balustrade and handrail as one helical ribbon
    bm = bmesh.new()
    steps = n * 3
    rows = [[], [], [], []]
    for k in range(steps + 1):
        t = k / steps
        for row, dz in zip(rows, (-0.3, 0.06, 0.95, 1.0)):
            row.append(bm.verts.new((*polar(SR, a0 + 360 * t), z0 + (z1 - z0) * t + dz)))
    for band in range(3):
        for k in range(steps):
            if band == 0 or 0.07 < (k + 0.5) / steps < 0.93:  # no glass where you step on and off
                f = bm.faces.new((rows[band][k], rows[band][k + 1], rows[band + 1][k + 1], rows[band + 1][k]))
                f.material_index = band
                f.smooth = True
    obj("stair_side", bm, [WHITE, GLASS, FRAME])
    rail([polar(SR + 0.2, a) for a in range(int(a0) + 30, int(a0) + 331, 6)], z1)  # guard round the opening above


flight(FL[0], FL[1])
flight(FL[1], FL[2])
rod((*STAIR, FL[0]), (*STAIR, FL[2] + 1.0), 0.09, FRAME)

# level 1
wall((-7, -4), (4, -4), 0.15, 3.55)
wall((4, -4), (8, -4), 0.15, 3.55, STONE)
box(5.0, 6.5, -4.17, -4.1, 0.17, 3.1, WOODV)
rod((6.3, -4.2, 1.0), (6.3, -4.2, 2.0), 0.015, FRAME, seg=8)
wall((-7, -4), (-7, 5), 0.15, 3.55)
wall((-7, 5), (8, 5), 0.15, 3.55, STONE)
wall((8, -4), (8, 5), 0.15, 3.55, STONE)
# level 2
wall((-10, -7), (6, -7), 4.0, 7.0)
wall((-10, -7), (-10, 4.5), 4.0, 7.0)
wall((-10, 4.5), (6, 4.5), 4.0, 7.0, STONE)
wall((6, -7), (6, 4.5), 4.0, 7.0, STONE)
wall((-1, -7), (-1, 1.75), 4.0, 7.0, PLASTER, 0.15)   # partition, with a doorway into the dressing area
wall((-1, 2.85), (-1, 4.5), 4.0, 7.0, PLASTER, 0.15)
wall((-1, 1.75), (-1, 2.85), 6.3, 7.0, PLASTER, 0.15)
wall((-8.6, 1.0), (-3.4, 1.0), 4.0, 6.5, WOODV, 0.2)
for i in range(21):
    x = -0.8 + i * 0.34
    box(x - 0.025, x + 0.025, -7.5, -7.15, 4.0, 7.0, WOODV)
rail(rr(-11.1, 6.9, -8.1, 5.4, 1.1) + rr(-11.1, 6.9, -8.1, 5.4, 1.1)[:1], 4.0)
# level 3
wall((-3, -3.5), (9, -3.5), 7.5, 10.5)
wall((-3, -3.5), (-3, 5.5), 7.5, 10.5)
wall((9, -3.5), (9, 1.5), 7.5, 10.5)
wall((9, 1.5), (9, 5.5), 7.5, 10.5, STONE)
wall((-3, 5.5), (9, 5.5), 7.5, 10.5, STONE)
wall((-2.6, 0.25), (1.4, 0.25), 7.5, 8.72, WOODV, 0.2)  # half-height wall behind the bed
rail([(-3, 5.6)] + rr(-11.1, 7.1, -8.1, 5.6, 1.1)[16:] + [(7.1, -4.7)], 7.4)

# V-columns under the cantilevers
for bx, by, tops in ((-8.6, -6.0, ((-10.2, -7.4), (-7.0, -7.4))), (-1.5, -6.0, ((-3.1, -7.4), (0.1, -7.4))),
                     (-9.6, 3.4, ((-10.4, 1.8), (-10.4, 4.8)))):
    for tx, ty in tops:
        rod((bx, by, 0.15), (tx, ty, 3.53), 0.07, FRAME)

# recessed downlights, none in the ceilings the stairwell passes through
for level, ((x0, x1, y0, y1), c) in enumerate(zip((L1, L2, L3), CL)):
    for i in range(1, 6):
        for j in range(1, 4):
            x, y = x0 + (x1 - x0) * i / 6, y0 + (y1 - y0) * j / 4
            if level == 2 or math.hypot(x - STAIR[0], y - STAIR[1]) > SR + 0.4:
                rod((x, y, c - 0.015), (x, y, c + 0.01), 0.06, DOWN, seg=12)

# ---------------------------------------------------------------- level 1: living, dining, kitchen, powder room
z = FL[0]
box(-6.5, -1.2, -3.2, 1.4, z, z + 0.015, RUG)
sofa(-4.1, 0.7, z, 0, 3.6, chaise=-1)
sofa(-1.75, -1.3, z, -90, 2.3)
model("mid_century_lounge_chair", -5.6, -2.6, z, 135)
model("mid_century_lounge_chair", -3.3, -2.9, z, 200)
model("side_table_01", -4.5, -3.2, z, 10)
model("standing_picture_frame_02", -4.5, -3.2, z + 0.55, -80)
model("coffee_table_round_01", -4.0, -1.1, z)
model("ceramic_vase_02", -4.2, -1.0, z + 0.49)
model("potted_plant_04", -3.75, -1.3, z + 0.49)
model("modern_wooden_cabinet", -4.1, 1.5, z, 180)          # sideboard behind the sofa
model("ceramic_vase_01", -5.1, 1.5, z + 0.68)
model("book_encyclopedia_set_01", -4.25, 1.5, z + 0.68, 180)
model("marble_bust_01", -3.85, 1.5, z + 0.68, 160)
model("potted_plant_02", -3.25, 1.5, z + 0.68, 30, 0.8)
lamp(-6.4, 1.3, z)
model("potted_plant_01", -6.3, -3.4, z, 0, 1.4)
shelf(-4.9, 4.72, z, 0, 3.6, 2.7)
curtain((-6.85, -3.82), (-5.95, -3.82), z, CL[0])
curtain((3.0, -3.82), (3.9, -3.82), z, CL[0])
curtain((-6.82, 3.95), (-6.82, 4.85), z, CL[0])
# dining, laid for six
table(0.9, -1.7, z, 0, 2.6, 1.05, 0.75, WOODF, BLACK)
box(0.0, 1.8, -1.81, -1.59, z + 0.75, z + 0.754, LINEN)
model("brass_vase_01", 0.9, -1.7, z + 0.754)
model("jug_01", 1.5, -1.7, z + 0.754, 40)
for x in (0.1, 0.9, 1.7):
    model("dining_chair_02", x, -2.3, z, 180)
    model("dining_chair_02", x, -1.1, z, 0)
    pendant(x, -1.7, z, CL[0])
    for y, side in ((-2.0, 1), (-1.4, -1)):
        rod((x, y, z + 0.75), (x, y, z + 0.762), 0.135, PORCELAIN, seg=28)
        rod((x + 0.19, y + 0.1 * side, z + 0.75), (x + 0.19, y + 0.1 * side, z + 0.87), 0.03, GLASS, r2=0.038, seg=14, cap=False)
# kitchen
box(-2.0, 1.4, 1.9, 2.8, z, z + 0.88, CAB)
box(-2.1, 1.5, 1.8, 2.9, z + 0.88, z + 0.94, MARBLE, 0.005)
box(-1.0, -0.3, 2.1, 2.6, z + 0.94, z + 0.945, BLACK)   # hob
top = z + 0.94
model("pot_enamel_01", -0.65, 2.35, top + 0.005, 20)
model("wooden_bowl_01", 0.6, 2.3, top)
for dx, dy, dz in ((0, 0, 0.06), (0.07, 0.03, 0.055), (-0.06, 0.04, 0.055), (0.02, -0.07, 0.055), (0.01, 0.01, 0.11)):
    model("lemon", 0.6 + dx, 2.3 + dy, top + dz, R.uniform(0, 360))
model("wooden_cutting_board", -1.55, 2.3, top, 80)
model("croissant", -1.6, 2.22, top + 0.04, 30)
model("croissant", -1.5, 2.42, top + 0.04, 100)
rod((1.1, 2.4, top), (1.1, 2.4, top + 0.1), 0.03, PORCELAIN, seg=12)   # cake stand
rod((1.1, 2.4, top + 0.1), (1.1, 2.4, top + 0.112), 0.15, PORCELAIN, seg=28)
model("carrot_cake", 1.1, 2.4, top + 0.112)
for x in (-1.3, -0.3, 0.7):
    stool(x, 1.5, z)
box(-3, 2.8, 4.3, 4.88, z, z + 0.88, CAB)
box(-3, 2.8, 4.26, 4.88, z + 0.88, z + 0.92, MARBLE)
box(0.5, 1.2, 4.4, 4.78, z + 0.92, z + 0.924, STEEL)    # sink
rod((0.85, 4.8, z + 0.92), (0.85, 4.8, z + 1.25), 0.012, STEEL, seg=8)
rod((0.85, 4.8, z + 1.25), (0.85, 4.62, z + 1.25), 0.012, STEEL, seg=8)
model("tea_set_01", -0.6, 4.55, z + 0.92)
model("jug_01", 0.2, 4.62, z + 0.92, 200)
bottle(1.45, 4.7, z + 0.92, 0.24, 0.035, BOTTLES[0], False)
bottle(1.56, 4.72, z + 0.92, 0.2, 0.03, BOTTLES[1], True)
model("wine_bottles_01", 1.9, 4.62, z + 0.92)
box(-3, -1.5, 4.3, 4.88, z + 0.92, z + 2.5, CAB)
box(-1.5, 2.8, 4.5, 4.88, z + 1.5, z + 2.5, CAB)
box(-1.5, 2.8, 4.52, 4.56, z + 1.47, z + 1.5, LED)
# entry: console, bench, coats and boots
table(6.6, -3.3, z, 0, 1.6, 0.4, 0.8, WOODF, BLACK)
model("ceramic_vase_01", 6.2, -3.3, z + 0.8)
model("fishermans_hat", 7.0, -3.3, z + 0.83, 30)
model("hanging_picture_frame_01", 6.6, -3.87, z + 1.65, 180, 1.3)
model("potted_plant_01", 7.45, -2.5, z, 40, 1.3)
box(7.35, 7.85, -1.2, 0.4, z + 0.36, z + 0.46, LEATHER, 0.03, None, 0.03)
for y in (-1.05, 0.25):
    box(7.4, 7.8, y - 0.02, y + 0.02, z, z + 0.36, BLACK)
box(7.84, 7.88, -1.2, 0.4, z + 1.62, z + 1.7, WOODF)
for i, y in enumerate((-0.95, -0.4, 0.15)):
    rod((7.84, y, z + 1.66), (7.76, y, z + 1.66), 0.012, CHROME, seg=8)
    box(7.68, 7.8, y - 0.24, y + 0.24, z + 1.66 - (1.0, 0.85, 1.1)[i], z + 1.66, CLOTHES[(1, 3, 5)[i]], 0.03, None, 0.02)
model("rubber_boots", 7.6, 0.95, z, 90)
rod((7.6, -1.6, z), (7.6, -1.6, z + 0.5), 0.11, BLACK, r2=0.13, cap=False)     # umbrella stand
rod((7.6, -1.6, z + 0.02), (7.66, -1.56, z + 0.95), 0.012, CLOTHES[1], seg=8)
rod((7.6, -1.6, z + 0.02), (7.54, -1.66, z + 0.9), 0.012, CLOTHES[9], seg=8)
# powder room behind the hall
wall((6, 1.55), (6.35, 1.55), 0.15, 3.55, STONE, 0.12)
wall((7.25, 1.55), (8, 1.55), 0.15, 3.55, STONE, 0.12)
wall((6.35, 1.55), (7.25, 1.55), z + 2.2, 3.55, STONE, 0.12)
toilet(7.05, 4.88, z, 0)
vanity(7.88, 2.9, z, -90, 0.9)
model("hanging_picture_frame_02", 6.01, 3.2, z + 1.5, 90)
box(7.84, 7.87, 3.6, 3.9, z + 0.75, z + 1.25, LINEN, 0.008)   # hand towel
rod((7.88, 3.75, z + 1.27), (7.82, 3.75, z + 1.27), 0.01, CHROME, seg=8)
box(6.75, 7.3, 2.55, 3.25, z, z + 0.012, RUG)
for loc, size, power in (((-4, -0.5), (4, 3), 500), ((0.9, -1.7), (2.5, 1), 200), ((-0.3, 2.6), (3, 1.5), 350),
                         ((6.5, -2.6), (1.5, 1.5), 150), ((-4.9, 3.6), (3, 1), 200), ((7.0, 3.2), (1.2, 1.6), 140)):
    room_light(loc, CL[0], power, size)

# ---------------------------------------------------------------- level 2: bedroom suite with ensuite and wardrobe, landing lounge, study
z = FL[1]
bed(-6, -0.2, z, 0)
box(-8.4, -3.6, -3.0, 0.5, z, z + 0.015, RUG)
model("Ottoman_01", -6.5, -1.95, z)
model("Ottoman_01", -5.5, -1.95, z)
model("modern_arm_chair_01", -8.4, -5.2, z, 135)
model("side_table_01", -7.3, -6.0, z, 20)
model("potted_plant_04", -7.3, -6.0, z + 0.55, 0, 1.3)
lamp(-9.3, -6.3, z)
sofa(-4.4, -6.1, z, 180, 2.2)
model("throw_pillows_01", -4.4, -5.98, z + 0.46, 180, 0.85)
model("drawer_cabinet", -1.33, -3.6, z, -90)
box(-1.12, -1.09, -5.65, -4.85, z + 0.05, z + 1.95, MIRROR)       # full-length mirror on the partition
box(-1.1, -1.085, -5.68, -4.82, z + 0.02, z + 1.98, WOODF)
model("potted_plant_01", -1.8, -6.2, z, 0, 1.4)
curtain((-9.85, -6.82), (-8.95, -6.82), z, CL[1])
curtain((-2.05, -6.82), (-1.15, -6.82), z, CL[1])
curtain((-9.82, -0.1), (-9.82, 0.8), z, CL[1])
# ensuite bathroom
wall((-9.9, 1.2), (-7.2, 1.2), 4.0, 7.0, STONE, 0.12)
wall((-7.2, 1.2), (-7.2, 1.7), 4.0, 7.0, STONE, 0.12)
wall((-7.2, 2.6), (-7.2, 4.4), 4.0, 7.0, STONE, 0.12)
wall((-7.2, 1.7), (-7.2, 2.6), z + 2.2, 7.0, STONE, 0.12)
wall((-9.86, 1.2), (-9.86, 4.4), 4.0, 7.0, STONE, 0.1)            # privacy wall inside the glazing
box(-9.8, -7.26, 1.26, 4.38, z, z + 0.012, FLOOR1)
toilet(-9.25, 1.26, z, 180)
vanity(-8.0, 4.38, z, 0, 1.3)
box(-9.8, -8.9, 2.75, 4.38, z + 0.012, z + 0.03, STONE)           # walk-in shower
box(-8.91, -8.89, 2.75, 3.85, z, z + 2.1, GLASS)
rod((-9.8, 3.55, z + 2.25), (-9.35, 3.55, z + 2.25), 0.012, CHROME, seg=8)
rod((-9.35, 3.55, z + 2.2), (-9.35, 3.55, z + 2.22), 0.14, CHROME, seg=28)
box(-9.8, -9.66, 3.1, 3.6, z + 1.1, z + 1.125, MARBLE)
for i in range(3):
    bottle(-9.73, 3.2 + i * 0.13, z + 1.125, 0.2 - i * 0.02, 0.03, BOTTLES[i], i < 2)
rod((-7.3, 2.95, z + 1.05), (-7.3, 3.75, z + 1.05), 0.012, CHROME, seg=8)   # towel rail
box(-7.325, -7.275, 3.0, 3.35, z + 0.5, z + 1.07, LINEN, 0.012)
box(-7.325, -7.275, 3.4, 3.7, z + 0.62, z + 1.07, DUVET, 0.012)
box(-8.6, -7.5, 3.0, 3.7, z + 0.012, z + 0.024, RUG)
# walk-in wardrobe along the back wall
wardrobe(-4.2, 4.08, z, 0, 5.4)
box(-6.6, -1.8, 2.2, 3.2, z, z + 0.015, RUG)
model("Ottoman_01", -4.2, 2.7, z)
# landing lounge and study
box(-0.3, 2.9, -4.6, -1.4, z, z + 0.015, RUG)
model("modern_arm_chair_01", 0.2, -3.0, z, 90)
model("modern_arm_chair_01", 2.4, -3.0, z, -90)
model("modern_coffee_table_01", 1.3, -3.0, z)
model("chess_set", 1.3, -3.0, z + 0.39, 0, 0.8)
lamp(3.0, -4.4, z)
table(1.4, -6.3, z, 0, 1.9, 0.7, 0.75, WOODF, BLACK)
model("dining_chair_02", 1.4, -5.75, z, 0)
box(1.2, 1.52, -6.42, -6.2, z + 0.75, z + 0.765, STEEL)           # laptop
box(1.2, 1.52, -6.44, -6.43, z + 0.765, z + 0.975, LAPTOP).rotation_euler.x = 0.25
rod((2.1, -6.45, z + 0.75), (2.1, -6.45, z + 0.76), 0.07, BLACK, seg=16)   # desk lamp
rod((2.1, -6.45, z + 0.76), (2.1, -6.45, z + 1.1), 0.008, BLACK, seg=6)
ball(2.1, -6.45, z + 1.16, 0.07, LAMP)
for i in range(4):
    box(0.6, 0.84, -6.5, -6.34, z + 0.75 + i * 0.03, z + 0.776 + i * 0.03, R.choice(BOOKS))
rod((0.98, -6.22, z + 0.75), (0.98, -6.22, z + 0.84), 0.04, PORCELAIN, seg=14, cap=False)
shelf(-0.76, -0.5, z, 90, 3.0, 2.4)
model("Ukulele_01", -0.82, 1.25, z, 90).rotation_euler.x = -0.16
model("steel_frame_shelves_03", 5.5, -5.3, z, -90)
model("wall_clock", 5.87, -2.6, z + 1.9, -90)
sofa(1.0, 3.88, z, 0, 2.2)
model("throw_pillows_01", 1.0, 3.76, z + 0.46, 0, 0.85)
model("side_table_01", 2.45, 3.95, z)
rod((2.45, 3.95, z + 0.55), (2.45, 3.95, z + 0.6), 0.05, BLACK)
ball(2.45, 3.95, z + 0.71, 0.11, LAMP)
model("potted_plant_02", -0.55, 4.0, z, 0, 1.2)
curtain((-0.85, -6.82), (0.05, -6.82), z, CL[1])
curtain((4.95, -6.82), (5.85, -6.82), z, CL[1])
for loc, size, power in (((-6, -2.5), (3.5, 3), 350), ((-4.5, 2.8), (4, 1), 180), ((1.5, -3.5), (4, 3), 320), ((1.5, 2.5), (3, 2), 160),
                         ((-8.5, 2.8), (1.5, 2), 160)):
    room_light(loc, CL[1], power, size)

# ---------------------------------------------------------------- level 3: master suite, bathroom and roof terrace
z = FL[2]
bed(-0.6, -1.0, z, 0)
box(-2.5, 1.3, -3.1, 0.3, z, z + 0.015, RUG)
model("Ottoman_01", -0.6, -2.7, z)
lamp(1.9, -3.0, z)
model("potted_plant_01", -2.5, -3.0, z, 0, 1.4)
curtain((-2.85, -3.32), (-1.95, -3.32), z, CL[2])
curtain((7.95, -3.32), (8.85, -3.32), z, CL[2])
curtain((-2.82, 4.4), (-2.82, 5.3), z, CL[2])
# media unit on the back of the bed wall, facing the lounge
model("modern_wooden_cabinet", -0.6, 0.63, z, 180)
box(-1.35, 0.15, 0.56, 0.6, z + 0.8, z + 1.64, SCREEN)
box(-0.8, -0.4, 0.5, 0.7, z + 0.68, z + 0.7, BLACK)
box(-0.63, -0.57, 0.56, 0.62, z + 0.7, z + 0.82, BLACK)
model("ceramic_vase_04", 0.4, 0.63, z + 0.68)
model("standing_picture_frame_02", -1.62, 0.62, z + 0.68, 110)
box(-2.5, 2.3, 1.7, 5.2, z, z + 0.015, RUG)
sofa(-0.2, 4.75, z, 0, 3.0)
model("throw_pillows_01", -0.2, 4.63, z + 0.46, 0, 0.85)
model("coffee_table_round_01", -0.2, 3.2, z, 0, 0.8)
model("ceramic_vase_03", -0.35, 3.3, z + 0.39)
model("wooden_bowl_01", 0.0, 3.1, z + 0.39, 0, 0.8)
model("mid_century_lounge_chair", -2.0, 2.6, z, 135)
model("mid_century_lounge_chair", 1.7, 2.6, z, 225)
pendant(-0.2, 3.2, z, CL[2], 1.9)
model("drawer_cabinet", 2.35, 5.12, z)
model("potted_plant_02", -2.5, 4.8, z, 0, 1.2)
# master bathroom, enclosed beside the lift
wall((6, 1.5), (6.3, 1.5), 7.5, 10.5, STONE, 0.12)
wall((7.2, 1.5), (9, 1.5), 7.5, 10.5, STONE, 0.12)
wall((6.3, 1.5), (7.2, 1.5), z + 2.2, 10.5, STONE, 0.12)
box(6.0, 8.88, 1.56, 5.38, z, z + 0.012, FLOOR1)
prism(oval(7.4, 4.7, 0.88, 0.42), z, z + 0.58, PORCELAIN, oval(7.4, 4.7, 0.8, 0.34))
prism(oval(7.4, 4.7, 0.81, 0.35), z, z + 0.12, PORCELAIN)
rod((8.42, 4.7, z), (8.42, 4.7, z + 0.95), 0.02, CHROME, seg=8)
rod((8.42, 4.7, z + 0.95), (8.15, 4.7, z + 0.95), 0.02, CHROME, seg=8)
box(7.28, 7.52, 4.26, 5.14, z + 0.585, z + 0.6, WOODF)            # tray across the bath
bottle(7.4, 4.95, z + 0.6, 0.17, 0.03, BOTTLES[0], True)
rod((7.4, 4.45, z + 0.6), (7.4, 4.45, z + 0.68), 0.035, LINEN, seg=14)   # candle
ball(7.4, 4.45, z + 0.695, 0.012, LAMP)
vanity(8.88, 2.9, z, -90, 1.8)
toilet(6.0, 3.3, z, 90)
rod((7.5, 1.6, z + 1.05), (8.6, 1.6, z + 1.05), 0.012, CHROME, seg=8)   # towel rail
box(7.6, 8.0, 1.575, 1.625, z + 0.5, z + 1.07, LINEN, 0.012)
box(8.1, 8.5, 1.575, 1.625, z + 0.62, z + 1.07, ACC, 0.012)
box(7.0, 8.2, 2.3, 3.5, z + 0.012, z + 0.024, RUG)
model("wicker_basket_02", 8.55, 4.05, z + 0.012)
wardrobe(8.05, 1.14, z, 0, 1.6)                                    # just outside the bathroom
for loc, size, power in (((-0.6, -1.2), (3, 3), 300), ((7.2, -1.5), (2, 2.5), 200), ((7.5, 3.3), (1.5, 2), 160), ((-0.2, 3.4), (3, 2), 220)):
    room_light(loc, CL[2], power, size)

z = 7.4
box(-10.6, -4.3, -7.6, 5.1, z, z + 0.04, DECKW)
box(-4.3, 6.6, -7.6, -4.8, z, z + 0.04, DECKW)
z += 0.04
sofa(-7.2, 0.4, z, 0, 3.0, OUTF)
sofa(-9.4, -1.9, z, 90, 2.4, OUTF)
rod((-7.0, -2.0, z), (-7.0, -2.0, z + 0.35), 0.65, PAVER, seg=40)
rod((-7.0, -2.0, z + 0.35), (-7.0, -2.0, z + 0.37), 0.5, FIRE, seg=32)
for dx, dy in ((0, 0), (0.18, 0.1), (-0.15, 0.12), (0.03, -0.18)):
    rod((-7.0 + dx, -2.0 + dy, z + 0.37), (-7.0 + dx, -2.0 + dy, z + 0.37 + R.uniform(0.3, 0.55)), 0.11, FIRE, r2=0.0, seg=10)
light("POINT", (-7.0, -2.0, z + 0.7), 120, 0.2, (1.0, 0.45, 0.12))
planter(-10.4, -9.8, 1.5, 4.9, z)
planter(-9.6, -5.0, 4.4, 4.9, z)
planter(-3.5, 6.2, -7.5, -7.0, z)
inst(TREES[0], -9.6, -6.4, z + 0.5, 0.28)
rod((-9.6, -6.4, z), (-9.6, -6.4, z + 0.6), 0.45, POT, r2=0.55, seg=32)
# floating ring canopy with an LED underside
prism(oval(-7.2, -1.2, 3.1, 3.1), 10.0, 10.16, WHITE, oval(-7.2, -1.2, 2.3, 2.3))
prism(oval(-7.2, -1.2, 2.75, 2.75), 9.97, 10.0, LED, oval(-7.2, -1.2, 2.62, 2.62))
for a in (100, 220, 340):
    px, py = -7.2 + 2.7 * math.cos(math.radians(a)), -1.2 + 2.7 * math.sin(math.radians(a))
    rod((px, py, 7.4), (px, py, 10.0), 0.035, FRAME, seg=10)

# ---------------------------------------------------------------- terrace and garden
for y in (-10.6, -8.6, -6.6):
    lounger(-13.8, y, 0.15, 90)
table(-9.6, -0.2, 0.15, 90, 2.4, 1.0, 0.75, DECKW, BLACK)
for dy in (-0.8, 0, 0.8):
    chair(-10.4, -0.2 + dy, 0.15, 90, OUTF)
    chair(-8.8, -0.2 + dy, 0.15, -90, OUTF)
light("AREA", (-3, -5.5, 3.45), 220, (9, 1))
light("AREA", (6, -5.2, 3.45), 90, (3, 1))  # entrance
light("AREA", (-8.7, 0.5, 3.45), 200, (1.5, 6))
planter(8.6, 11.6, -13.6, -13.0, 0.15)
# outdoor lounge with a fire bowl on the deck beside the pool
sofa(7.6, -7.4, 0.15, 0, 3.0, OUTF)
sofa(10.0, -9.6, 0.15, -90, 2.4, OUTF)
rod((7.4, -9.8, 0.15), (7.4, -9.8, 0.5), 0.6, PAVER, seg=40)
rod((7.4, -9.8, 0.5), (7.4, -9.8, 0.52), 0.46, FIRE, seg=32)
for dx, dy in ((0, 0), (0.17, 0.1), (-0.15, 0.1), (0.03, -0.17)):
    rod((7.4 + dx, -9.8 + dy, 0.52), (7.4 + dx, -9.8 + dy, 0.52 + R.uniform(0.3, 0.5)), 0.1, FIRE, r2=0.0, seg=10)
light("POINT", (7.4, -9.8, 0.9), 100, 0.2, (1.0, 0.45, 0.12))
rod((10.6, -5.4, 0.15), (10.6, -5.4, 0.85), 0.5, POT, r2=0.62, seg=32)
inst(TREES[2], 10.6, -5.4, 0.7, 0.36)
planter(-15.2, -14.6, 0.5, 6.5, 0.15)

hedge(-34, 26, 22, 23.2, 2.3)
hedge(26, 27.2, -36, 22, 2.3, 150)
for x, y, s, me in ((-12, 10, 0.6, BLOSSOM_TREE), (-22.5, -13.5, 1.1, TREES[0]), (-3, 15, 1.25, TREES[1]),
                    (7, 16.5, 1.1, TREES[2]), (16, 12, 1.3, TREES[0]), (20, -2, 1.0, TREES[3]), (21.5, -12, 1.15, TREES[2]),
                    (-12, 17.5, 0.95, TREES[3]), (22, 6, 0.9, TREES[1])):
    inst(me, x, y, 0, s)
for x, y in ((-12.8, 9.2), (-2.2, 13.8), (7.8, 15.2), (16.8, 10.8), (20.6, -3.0)):
    light("SPOT", (x, y, 0.15), 150, 0.1, (1.0, 0.85, 0.65), up=True, spot=80)
for x in range(-40, 70, 6):  # tree line behind the back hedge
    inst(R.choice(TREES), x + R.uniform(-2, 2), 29 + R.uniform(-2, 4), 0, R.uniform(0.9, 1.4))
for _ in range(170):
    a, d = R.uniform(0, math.tau), R.uniform(50, 170)
    x, y = math.cos(a) * d, math.sin(a) * d
    if not (PLOT[0] < x < PLOT[1] + 3 and PLOT[2] < y < PLOT[3] + 10):
        inst(R.choice(TREES), x, y, 0, R.uniform(0.8, 1.5))
shrubs(-17.6, -15.8, -14.5, -5, 14)
shrubs(-15.5, -11.5, -15.6, -14.3, 8)
shrubs(1, 6, -15.5, -14.3, 9)
shrubs(-13.6, -10.4, 8.4, 11.6, 6)
for x, y in ((-14, -15.2), (-10, -15.2), (-6, -15.2), (-2, -15.2), (2, -15.2), (-3.7, -19.5), (-3.7, -24), (-3.7, -28.5)):  # bollard lights
    box(x - 0.05, x + 0.05, y - 0.05, y + 0.05, 0, 0.55, BLACK)
    box(x - 0.05, x + 0.05, y - 0.05, y + 0.05, 0.55, 0.62, LAMP)
    light("POINT", (x, y, 0.75), 12, 0.05)
for sc, loc in (((480, 180, 42), (250, 900, -8)), ((600, 220, 55), (900, 620, -10)), ((420, 160, 34), (-150, 1000, -6))):
    ball(*loc, 1, HILL, sc=sc)

# ---------------------------------------------------------------- sky
world = bpy.data.worlds.new("sky")
world.use_nodes = True
S.world = world
nt = world.node_tree
L = nt.links.new
sky = nt.nodes.new("ShaderNodeTexSky")
sky.sky_type = "MULTIPLE_SCATTERING"
sky.sun_elevation = math.radians(-3)  # just after sunset
sky.sun_rotation = math.radians(25)   # clockwise from +Y: the glow sits behind the house
bg = nt.nodes["Background"]
L(sky.outputs[0], bg.inputs[0])
# Long-exposure cheat: the sky lights the garden 2.3x harder than it looks to the camera and in reflections.
lp = nt.nodes.new("ShaderNodeLightPath")
seen = calc(nt, "MAXIMUM", lp.outputs["Is Camera Ray"], lp.outputs["Is Glossy Ray"])
boost = calc(nt, "SUBTRACT", 2.3, calc(nt, "MULTIPLY", seen, 1.3))
L(boost, bg.inputs[1])


# thin pink cloud streaks: noise on the view direction projected onto a flat ceiling
tc = nt.nodes.new("ShaderNodeTexCoord")
sep = nt.nodes.new("ShaderNodeSeparateXYZ")
L(tc.outputs["Generated"], sep.inputs[0])
up = calc(nt, "MAXIMUM", sep.outputs[2], 0.03)
comb = nt.nodes.new("ShaderNodeCombineXYZ")
L(calc(nt, "DIVIDE", sep.outputs[0], up), comb.inputs[0])
L(calc(nt, "DIVIDE", sep.outputs[1], up), comb.inputs[1])
mp = nt.nodes.new("ShaderNodeMapping")
mp.inputs["Scale"].default_value = (0.22, 0.8, 1)
mp.inputs["Rotation"].default_value = (0, 0, 0.5)
L(comb.outputs[0], mp.inputs["Vector"])
noise = nt.nodes.new("ShaderNodeTexNoise")
noise.inputs["Scale"].default_value = 1.0
noise.inputs["Detail"].default_value = 8
noise.inputs["Distortion"].default_value = 0.8
L(mp.outputs[0], noise.inputs["Vector"])
cr = ramp(nt, ((0, 0, 0), (1, 1, 1)), 0.5, 0.75)
L(noise.outputs[0], cr.inputs[0])
fac = calc(nt, "MULTIPLY", calc(nt, "MULTIPLY", cr.outputs[0], calc(nt, "MULTIPLY", sep.outputs[2], 9, True)), 0.7)
cloud = nt.nodes.new("ShaderNodeBackground")
cloud.inputs[0].default_value = (0.95, 0.5, 0.42, 1)
L(calc(nt, "MULTIPLY", boost, 0.35), cloud.inputs[1])
mix = nt.nodes.new("ShaderNodeMixShader")
L(fac, mix.inputs[0])
L(bg.outputs[0], mix.inputs[1])
L(cloud.outputs[0], mix.inputs[2])
L(mix.outputs[0], nt.nodes["World Output"].inputs[0])

# ---------------------------------------------------------------- cameras and output
def camera(name, loc, target, lens, shift_y=0.0):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    cd.shift_y = shift_y
    cd.clip_end = 3000
    o = bpy.data.objects.new(name, cd)
    o.location = loc
    d = Vector(target) - Vector(loc)
    d.z = 0  # level camera plus lens shift keeps verticals vertical
    o.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    C.objects.link(o)
    return o


VIEWS = [  # name, file, camera, exposure, samples, size
    ("exterior", "exterior.jpg", camera("exterior", (-21, -27, 4.2), (-1.5, -4.2, 0), 32, 0.05), 0.8, 320, (1920, 1080)),
    ("level-1", "level-1.jpg", camera("level-1", (-6.4, 3.6, FL[0] + 1.4), (5, -3.3, 0), 20, 0.0), -1.0, 480, (1920, 1080)),
    ("level-2", "level-2.jpg", camera("level-2", (-1.6, 0.4, FL[1] + 1.4), (-10, -4, 0), 20, 0.0), -1.0, 480, (1920, 1080)),
    ("level-3", "level-3.jpg", camera("level-3", (8.3, -2.6, FL[2] + 1.4), (-6, 0.6, 0), 20, 0.0), -1.0, 480, (1920, 1080)),
]

# ---------------------------------------------------------------- walk-through tour
EYE = 1.55
SPOTS = [  # id, label, area, x, y, floor height, exposure
    ("garden", "Garden", "Outside", -16, -21, 0.0, 0.8),
    ("pool", "Pool deck", "Outside", -5, -13.1, 0.15, 0.8),
    ("fire", "Fire lounge", "Outside", 8.5, -11.8, 0.15, 0.8),
    ("veranda", "Outdoor dining", "Outside", -8.4, -3.4, 0.15, 0.3),
    ("entrance", "Front door", "Outside", 5.8, -6.2, 0.15, 0.3),
    ("hall", "Entry hall", "Level 1", 6.9, -1.8, FL[0], -1.0),
    ("powder", "Powder room", "Level 1", 6.85, 2.6, FL[0], -1.0),
    ("stair-1", "Dining and stair", "Level 1", 2.5, -0.2, FL[0], -1.0),
    ("living", "Living room", "Level 1", -2.9, -0.7, FL[0], -1.0),
    ("kitchen", "Kitchen", "Level 1", -0.3, 3.55, FL[0], -1.0),
    ("library", "Library corner", "Level 1", -5.6, 3.1, FL[0], -1.0),
    ("landing-2", "Landing lounge", "Level 2", 2.2, -0.5, FL[1], -1.0),
    ("study", "Study", "Level 2", 1.3, -4.6, FL[1], -1.0),
    ("dressing", "Walk-in wardrobe", "Level 2", -2.6, 2.3, FL[1], -1.0),
    ("ensuite", "Ensuite bathroom", "Level 2", -8.1, 2.3, FL[1], -1.0),
    ("bedroom", "Bedroom suite", "Level 2", -3.0, -2.6, FL[1], -1.0),
    ("landing-3", "Top of the stair", "Level 3", 2.3, -0.5, FL[2], -1.0),
    ("master", "Master suite", "Level 3", 1.3, -2.3, FL[2], -1.0),
    ("lounge-3", "Sky lounge", "Level 3", -0.2, 1.5, FL[2], -1.0),
    ("bath", "Master bathroom", "Level 3", 7.3, 2.4, FL[2], -1.0),
    ("terrace", "Roof terrace", "Level 3", -5.0, -0.5, 7.44, 0.0),
    ("terrace-edge", "Terrace edge", "Level 3", -6.0, -6.9, 7.44, 0.3),
]
PATHS = [("garden", "pool"), ("pool", "fire"), ("pool", "veranda"), ("pool", "entrance"), ("fire", "entrance"),
         ("entrance", "hall"), ("hall", "powder"), ("dressing", "ensuite"), ("veranda", "living"), ("hall", "stair-1"), ("stair-1", "living"), ("stair-1", "kitchen"),
         ("living", "kitchen"), ("living", "library"), ("kitchen", "library"), ("stair-1", "landing-2"),
         ("landing-2", "study"), ("landing-2", "dressing"), ("dressing", "bedroom"), ("landing-2", "landing-3"),
         ("landing-3", "master"), ("landing-3", "lounge-3"), ("landing-3", "bath"), ("master", "lounge-3"),
         ("master", "terrace"), ("terrace", "terrace-edge")]
ids = [sp[0] for sp in SPOTS]
assert len(set(ids)) == len(ids) and all(a in ids and b in ids for a, b in PATHS), "tour path names a spot that does not exist"
os.makedirs(os.path.join(OUT, "tour"), exist_ok=True)
tour = []
for sid, label, area, x, y, floor, exposure in SPOTS:
    cd = bpy.data.cameras.new(sid)
    cd.type = "PANO"
    cd.panorama_type = "EQUIRECTANGULAR"
    cd.clip_start = 0.05
    cd.clip_end = 3000
    cam = bpy.data.objects.new(sid, cd)
    cam.location = (x, y, floor + EYE)
    cam.rotation_euler = (math.pi / 2, 0, 0)  # faces +Y: +Y lands in the image centre, +X a quarter turn to its right
    C.objects.link(cam)
    VIEWS.append(("tour-" + sid, "tour/%s.jpg" % sid, cam, exposure, 96, (4096, 2048)))
    tour.append({"id": sid, "name": label, "area": area, "pos": [x, y, round(floor + EYE, 2)],
                 "links": [b if a == sid else a for a, b in PATHS if sid in (a, b)]})
with open(os.path.join(OUT, "tour.js"), "w") as f:
    f.write("const EYE = %s;\nconst TOUR = %s;\n" % (EYE, json.dumps(tour, indent=1)))

bpy.context.view_layer.update()
polys = []
for (x0, x1, y0, y1), z0, z1 in ((L1, 0.15, 3.6), (L2, 3.6, 7.05), (L3, 7.05, 10.82)):
    pts = [world_to_camera_view(S, VIEWS[0][2], Vector((x, y, zz))) for x in (x0, x1) for y in (y0, y1) for zz in (z0, z1)]
    px = [(p.x * 1920, (1 - p.y) * 1080) for p in pts]
    polys.append([[round(px[i][0]), round(px[i][1])] for i in convex_hull_2d(px)])
assert all(0 <= x <= 1920 and 0 <= y <= 1080 for poly in polys for x, y in poly), "a level falls outside the exterior frame"
with open(os.path.join(OUT, "floors.js"), "w") as f:
    f.write("const FLOORS = " + json.dumps(polys) + ";\n")

print("BUILT in %.0fs" % (time.time() - T0))
for name, file, cam, exposure, samples, size in VIEWS:
    if ONLY and not any(name == o or name.startswith(o + "-") for o in ONLY):
        continue
    S.camera = cam
    S.view_settings.exposure = exposure
    S.cycles.samples = samples // 4 if DRAFT else samples
    S.render.resolution_x, S.render.resolution_y = size
    S.render.filepath = os.path.join(OUT, file)
    bpy.ops.render.render(write_still=True)
    print("RENDERED %s at %.0fs" % (name, time.time() - T0))
