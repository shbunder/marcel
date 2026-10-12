"""Marcel in Blender: one character, four looks, rendered with Cycles.

Run headless:
  Blender -b -P marcel.py -- --style plush --still out/plush.png
  Blender -b -P marcel.py -- --style clay  --state working --frames 16 --out out/clay-working/
Styles: plush (fur + felt, like Dots/Jolly), clay (soft glossy toy), toon (cel shaded, outlined), voxel (cubes).
States: idle, thinking, working, happy.
"""
import math
import os
import sys

import bpy
from mathutils import Euler, Quaternion, Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
def arg(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default

STYLE = arg("--style", "plush")
STATE = arg("--state", "idle")
STILL = arg("--still")
FRAMES = int(arg("--frames", "16"))
OUT = arg("--out", "out/frames/")
RES = int(arg("--res", "512"))
SAMPLES = int(arg("--samples", "48"))

# --------------------------------------------------------------------------- palette (sRGB hex -> linear)
def lin(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92 for v in c) + (1.0,)

ROSE, DEEP, MUZZLE, BLUSH = lin("#cf6f86"), lin("#963753"), lin("#eba7b6"), lin("#e8758f")
INK, TEAL, TEAL_DK, LAPTOP, AMBER = lin("#141012"), lin("#3f6b6a"), lin("#2f5251"), lin("#2b2a28"), lin("#f2a33a")

# --------------------------------------------------------------------------- scene
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
if STYLE == "toon":
    for engine in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            scene.render.engine = engine; break
        except TypeError:
            continue
else:
    scene.render.engine = "CYCLES"
if scene.render.engine == "CYCLES":
    prefs = bpy.context.preferences.addons["cycles"].preferences
    try:
        prefs.compute_device_type = "METAL"
        prefs.get_devices()
        for d in prefs.devices:
            d.use = True
        scene.cycles.device = "GPU"
    except Exception:
        pass
    scene.cycles.samples = SAMPLES
    scene.cycles.use_denoising = arg("--denoise", "1") == "1"
    scene.cycles.max_bounces = 6
scene.render.resolution_x = scene.render.resolution_y = RES
if STYLE == "toon":
    try:
        scene.eevee.taa_render_samples = 32
    except AttributeError:
        pass
scene.render.film_transparent = True
scene.view_settings.view_transform = "AgX"
scene.view_settings.look = "AgX - Punchy"
if STYLE == "toon":
    scene.view_settings.view_transform = "Standard"; scene.view_settings.look = "None"
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"

world = bpy.data.worlds.new("w"); scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = lin("#f3e6ea")
world.node_tree.nodes["Background"].inputs[1].default_value = 0.3

# --------------------------------------------------------------------------- materials
def mat(name, colour, rough=0.6, sheen=0.0, coat=0.0, sss=0.0, emit=None):
    m = bpy.data.materials.new(name); m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = colour
    p.inputs["Roughness"].default_value = rough
    if sheen:
        p.inputs["Sheen Weight"].default_value = sheen
        p.inputs["Sheen Roughness"].default_value = 0.5
        p.inputs["Sheen Tint"].default_value = (1, 1, 1, 1)
    if coat:
        p.inputs["Coat Weight"].default_value = coat
        p.inputs["Coat Roughness"].default_value = 0.18
    if sss:
        p.inputs["Subsurface Weight"].default_value = sss
        p.inputs["Subsurface Radius"].default_value = (0.4, 0.15, 0.1)
        p.inputs["Subsurface Scale"].default_value = 0.05
    if emit:
        p.inputs["Emission Color"].default_value = emit
        p.inputs["Emission Strength"].default_value = 0.0
    return m

def toon_mat(name, colour):
    """EEVEE cel shading: diffuse -> shader to RGB -> constant 3-step ramp."""
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    dif = nt.nodes.new("ShaderNodeBsdfDiffuse"); dif.inputs[0].default_value = (1, 1, 1, 1)
    s2r = nt.nodes.new("ShaderNodeShaderToRGB")
    ramp = nt.nodes.new("ShaderNodeValToRGB"); ramp.color_ramp.interpolation = "CONSTANT"
    el = ramp.color_ramp.elements
    el[0].position, el[0].color = 0.0, tuple(c * 0.62 for c in colour[:3]) + (1,)
    el[1].position, el[1].color = 0.25, tuple(c * 1.0 for c in colour[:3]) + (1,)
    e3 = el.new(0.7); e3.color = tuple(min(1, c * 1.25) for c in colour[:3]) + (1,)
    emi = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(dif.outputs[0], s2r.inputs[0]); nt.links.new(s2r.outputs[0], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], emi.inputs[0]); nt.links.new(emi.outputs[0], out.inputs[0])
    return m

if STYLE == "plush":
    M = dict(body=mat("fur-skin", ROSE, rough=1.0, sheen=1.0), deep=mat("felt-deep", DEEP, rough=1.0, sheen=0.8),
             muzzle=mat("muzzle", MUZZLE, rough=1.0, sheen=1.0), blush=mat("blush", BLUSH, rough=1.0, sheen=0.6),
             teal=mat("felt-teal", TEAL, rough=1.0, sheen=0.8), tealdk=mat("felt-teal-dk", TEAL_DK, rough=1.0, sheen=0.6))
elif STYLE == "clay":
    M = dict(body=mat("clay", ROSE, rough=0.38, coat=0.35, sss=0.25), deep=mat("clay-deep", DEEP, rough=0.38, coat=0.35, sss=0.2),
             muzzle=mat("clay-muzzle", MUZZLE, rough=0.4, coat=0.3, sss=0.3), blush=mat("clay-blush", BLUSH, rough=0.45, coat=0.2),
             teal=mat("clay-teal", TEAL, rough=0.35, coat=0.4), tealdk=mat("clay-teal-dk", TEAL_DK, rough=0.35, coat=0.4))
elif STYLE == "toon":
    M = dict(body=toon_mat("t-body", ROSE), deep=toon_mat("t-deep", DEEP), muzzle=toon_mat("t-muzzle", MUZZLE),
             blush=toon_mat("t-blush", BLUSH), teal=toon_mat("t-teal", TEAL), tealdk=toon_mat("t-tealdk", TEAL_DK))
else:
    M = dict(body=mat("v-body", ROSE, rough=0.55), deep=mat("v-deep", DEEP, rough=0.55), muzzle=mat("v-muzzle", MUZZLE, rough=0.55),
             blush=mat("v-blush", BLUSH, rough=0.55), teal=mat("v-teal", TEAL, rough=0.5), tealdk=mat("v-tealdk", TEAL_DK, rough=0.5))
M["eye"] = mat("eye", INK, rough=0.3, coat=0.6) if STYLE != "toon" else toon_mat("t-eye", INK)
M["spark"] = mat("spark", (1, 1, 1, 1), emit=(1, 1, 1, 1))
M["spark"].node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 3.0
M["laptop"] = mat("laptop", LAPTOP, rough=0.35) if STYLE != "toon" else toon_mat("t-laptop", LAPTOP)
M["glow"] = mat("glow", DEEP, rough=1.0, sheen=0.8 if STYLE == "plush" else 0, emit=AMBER)

# --------------------------------------------------------------------------- geometry helpers
def link(obj, parent=None):
    scene.collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    return obj

def empty(name, loc, parent=None):
    e = bpy.data.objects.new(name, None); e.location = loc
    return link(e, parent)

def sphere(name, loc, scale, material, parent=None, seg=48):
    me = bpy.data.meshes.new(name)
    import bmesh
    bm = bmesh.new(); bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=seg // 2, radius=1.0)
    bm.to_mesh(me); bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(material)
    o = bpy.data.objects.new(name, me); o.location = loc; o.scale = scale
    return link(o, parent)

def cylinder(name, loc, r1, r2, depth, material, parent=None):
    import bmesh
    me = bpy.data.meshes.new(name); bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=32, radius1=r1, radius2=r2, depth=depth)
    bm.to_mesh(me); bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(material)
    o = bpy.data.objects.new(name, me); o.location = loc
    return link(o, parent)

def blobs(name, parts, material, parent=None, voxel=0.009, smooth=12):
    """Exact shapes fused into one seamless form: join, voxel-remesh (union), then smooth the joins like a sewn toy.
    parts: ("ellipsoid", centre, half-axes) or ("cone", centre, (r_bottom, r_top, height), x-rotation)."""
    import bmesh
    bm = bmesh.new()
    for p in parts:
        tmp = bmesh.new()
        if p[0] == "ellipsoid":
            bmesh.ops.create_uvsphere(tmp, u_segments=48, v_segments=24, radius=1.0)
            for v in tmp.verts:
                v.co = Vector((v.co.x * p[2][0], v.co.y * p[2][1], v.co.z * p[2][2])) + Vector(p[1])
        else:
            bmesh.ops.create_cone(tmp, cap_ends=True, segments=40, radius1=p[2][0], radius2=p[2][1], depth=p[2][2])
            rot = Euler((p[3] if len(p) > 3 else 0, 0, 0)).to_matrix()
            for v in tmp.verts:
                v.co = rot @ v.co + Vector(p[1])
        me_tmp = bpy.data.meshes.new("tmp"); tmp.to_mesh(me_tmp); tmp.free()
        bm.from_mesh(me_tmp); bpy.data.meshes.remove(me_tmp)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    me.materials.append(material)
    o = link(bpy.data.objects.new(name, me), parent)
    r = o.modifiers.new("union", "REMESH"); r.mode = "VOXEL"; r.voxel_size = voxel
    sm = o.modifiers.new("soften", "SMOOTH"); sm.factor = 0.6; sm.iterations = smooth
    # bake the modifiers so fur and outlines work on the final surface
    dg = bpy.context.evaluated_depsgraph_get()
    baked = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
    o.modifiers.clear(); old = o.data; o.data = baked; bpy.data.meshes.remove(old)
    for poly in o.data.polygons:
        poly.use_smooth = True
    if not o.data.materials:
        o.data.materials.append(material)
    return o

def fur(obj, length=0.03, density=9000, radius=0.0011, colour=ROSE):
    """Procedural fur on a real hair object (Cycles renders these as hair): strands along the surface normal,
    tapered, a little random in length, bent in soft clumps. The hair object rides along with its surface."""
    hair = bpy.data.objects.new("fur-" + obj.name, bpy.data.hair_curves.new("fur-" + obj.name))
    link(hair, obj)
    ng = bpy.data.node_groups.new("fur-" + obj.name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = ng.nodes; L = ng.links
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    src = n.new("GeometryNodeObjectInfo"); src.transform_space = "ORIGINAL"; src.inputs["Object"].default_value = obj
    dist = n.new("GeometryNodeDistributePointsOnFaces"); dist.inputs["Density"].default_value = density
    line = n.new("GeometryNodeCurvePrimitiveLine"); line.inputs["End"].default_value = (0, 0, length)
    res = n.new("GeometryNodeResampleCurve"); res.inputs["Count"].default_value = 5
    inst = n.new("GeometryNodeInstanceOnPoints")
    rnd = n.new("FunctionNodeRandomValue"); rnd.data_type = "FLOAT"; rnd.inputs["Min"].default_value = 0.55; rnd.inputs["Max"].default_value = 1.15
    real = n.new("GeometryNodeRealizeInstances")
    rad = n.new("GeometryNodeSetCurveRadius")
    sp = n.new("GeometryNodeSplineParameter")
    taper = n.new("ShaderNodeMath"); taper.operation = "MULTIPLY_ADD"; taper.inputs[1].default_value = -0.75 * radius; taper.inputs[2].default_value = radius
    noise = n.new("ShaderNodeTexNoise"); noise.inputs["Scale"].default_value = 9.0; noise.inputs["Detail"].default_value = 1.0
    centre = n.new("ShaderNodeVectorMath"); centre.operation = "SUBTRACT"; centre.inputs[1].default_value = (0.5, 0.5, 0.5)
    bend = n.new("ShaderNodeVectorMath"); bend.operation = "SCALE"
    amount = n.new("ShaderNodeMath"); amount.operation = "MULTIPLY"; amount.inputs[1].default_value = length * 0.9
    setpos = n.new("GeometryNodeSetPosition")
    hm = bpy.data.materials.new("hair-" + obj.name); hm.use_nodes = True
    hn = hm.node_tree.nodes; hn.clear()
    hb = hn.new("ShaderNodeBsdfHairPrincipled"); hb.parametrization = "COLOR"
    hb.inputs["Color"].default_value = colour; hb.inputs["Roughness"].default_value = 0.55; hb.inputs["Radial Roughness"].default_value = 0.85
    ho = hn.new("ShaderNodeOutputMaterial"); hm.node_tree.links.new(hb.outputs[0], ho.inputs[0])
    sm = n.new("GeometryNodeSetMaterial"); sm.inputs["Material"].default_value = hm
    L.new(src.outputs["Geometry"], dist.inputs["Mesh"])
    L.new(line.outputs[0], res.inputs["Curve"])
    L.new(dist.outputs["Points"], inst.inputs["Points"]); L.new(res.outputs[0], inst.inputs["Instance"])
    L.new(dist.outputs["Rotation"], inst.inputs["Rotation"]); L.new(rnd.outputs[0], inst.inputs["Scale"])
    L.new(inst.outputs[0], real.inputs[0])
    L.new(sp.outputs["Factor"], taper.inputs[0]); L.new(taper.outputs[0], rad.inputs["Radius"])
    L.new(noise.outputs["Color"], centre.inputs[0]); L.new(centre.outputs[0], bend.inputs[0])
    L.new(sp.outputs["Factor"], amount.inputs[0]); L.new(amount.outputs[0], bend.inputs["Scale"])
    L.new(real.outputs[0], setpos.inputs["Geometry"]); L.new(bend.outputs[0], setpos.inputs["Offset"])
    L.new(setpos.outputs[0], rad.inputs["Curve"])
    L.new(rad.outputs[0], sm.inputs["Geometry"])
    L.new(sm.outputs[0], go.inputs[0])
    hair.data.materials.append(hm)
    mod = hair.modifiers.new("fur", "NODES"); mod.node_group = ng

def outline(obj, thickness=0.012):
    """Inverted-hull outline for the cartoon look (EEVEE): a solidify shell with flipped normals in deep plum."""
    if STYLE != "toon":
        return
    om = bpy.data.materials.get("outline")
    if om is None:
        om = bpy.data.materials.new("outline"); om.use_nodes = True
        nt = om.node_tree; nt.nodes.clear()
        e = nt.nodes.new("ShaderNodeEmission"); e.inputs[0].default_value = lin("#3a1526")
        o = nt.nodes.new("ShaderNodeOutputMaterial"); nt.links.new(e.outputs[0], o.inputs[0])
        om.use_backface_culling = True
    obj.data.materials.append(om)
    s = obj.modifiers.new("outline", "SOLIDIFY"); s.thickness = thickness; s.offset = 1; s.use_flip_normals = True
    s.material_offset = len(obj.data.materials) - 1

# --------------------------------------------------------------------------- Marcel (front faces -Y, up is +Z, about 1.75 tall)
Q = lambda axis, ang: Quaternion(axis, ang)
root = empty("marcel", (0, 0, 0))
bodyP = empty("body-pivot", (0, 0, 0), root)
body = blobs("body", [
    ("ellipsoid", (0, 0, 0.36), (0.37, 0.33, 0.33)),               # round sitting body
    ("cone", (0, 0.02, 0.78), (0.16, 0.125, 0.5)),                # neck: shorter and thicker, still a giraffe
    ("ellipsoid", (0, 0.0, 0.6), (0.2, 0.19, 0.13)),              # soft shoulder where the neck meets the body
], M["body"], bodyP)
outline(body)
for side in (-1, 1):                                                       # feet
    f = sphere(f"foot{side}", (side * 0.14, -0.2, 0.045), (0.085, 0.11, 0.05), M["deep"], bodyP); outline(f, 0.008)
tail = cylinder("tail", (0.0, 0.33, 0.28), 0.022, 0.018, 0.2, M["body"], bodyP); tail.rotation_euler = (0.9, 0, 0)
sphere("tuft", (0, 0.41, 0.21), (0.04, 0.04, 0.055), M["deep"], bodyP)

# felt spots: flattened discs that sit on the surface, facing outward
def spot(name, at, size, material=None):
    loc = Vector(at)
    centre = Vector((0, 0.02, loc.z if loc.z > 0.62 else 0.36))
    if STYLE == "plush":
        loc = loc + (loc - centre).normalized() * 0.02          # felt patch sewn on top of the fur
    o = sphere(name, loc, (size[0], size[1], 0.012), material or M["deep"], bodyP, seg=32)
    o.rotation_mode = "QUATERNION"
    o.rotation_quaternion = (loc - centre).to_track_quat("Z", "Y")
    return o
SPOTS = [spot("spot-neck1", (0.07, -0.115, 0.86), (0.045, 0.05)), spot("spot-body1", (0.19, -0.27, 0.48), (0.085, 0.07)),
         spot("spot-body2", (-0.24, -0.25, 0.32), (0.09, 0.075))]

# bow tie (felt)
bow = empty("bow", (0, -0.16 - (0.025 if STYLE == "plush" else 0), 0.62), bodyP); bow.scale = (1.3, 1.3, 1.3)
for side in (-1, 1):
    w = sphere(f"bow{side}", (side * 0.05, 0, 0), (0.055, 0.022, 0.04), M["teal"], bow)
    w.rotation_euler = (0, side * 0.25, 0)
sphere("knot", (0, -0.012, 0), (0.022, 0.02, 0.026), M["tealdk"], bow)

# arms (pivot at the shoulder)
ARMS = []
for side in (-1, 1):
    p = empty(f"arm-pivot{side}", (side * 0.36, -0.1, 0.48), bodyP)
    a = blobs(f"arm{side}", [("ellipsoid", (0, 0, -0.12), (0.055, 0.055, 0.15))], M["body"], p, voxel=0.007, smooth=4)
    outline(a, 0.008)
    sphere(f"hoof{side}", (0, 0, -0.25), (0.06, 0.06, 0.05), M["deep"], p)
    p.rotation_euler = (0, side * -0.25, 0)
    ARMS.append(p)

# head (pivot at the top of the neck)
headP = empty("head-pivot", (0, 0.0, 1.0), root); headP.scale = (1.3, 1.3, 1.3)
head = blobs("head", [
    ("ellipsoid", (0, 0, 0.19), (0.215, 0.2, 0.2)),
    ("ellipsoid", (0, -0.15, 0.07), (0.15, 0.13, 0.115)),          # snout
    ("cone", (0, 0.02, -0.04), (0.1, 0.11, 0.18)),                 # top of the neck, tucked inside the head
], M["body"], headP)
outline(head)
muzzle = sphere("muzzle", (0, -0.205, 0.055), (0.12, 0.09, 0.082), M["muzzle"], headP)
for side in (-1, 1):
    sphere(f"nostril{side}", (side * 0.04, -0.295, 0.09), (0.012, 0.008, 0.016), M["deep"], headP, seg=16)
    sphere(f"blush{side}", (side * 0.15, -0.155, 0.12), (0.04, 0.02, 0.025), M["blush"], headP, seg=24)
EYES = []
for side in (-1, 1):
    e = sphere(f"eye{side}", (side * 0.085, -0.178 - (0.012 if STYLE == "plush" else 0), 0.24), (0.026, 0.02, 0.034), M["eye"], headP, seg=24)
    sphere(f"glint{side}", (-0.25, -0.75, 0.35), (0.28, 0.2, 0.22), M["spark"], e, seg=12)   # catch-light, rides with the eye
    EYES.append(e)
EARS = []
for side in (-1, 1):
    p = empty(f"ear-pivot{side}", (side * 0.18, 0.02, 0.3), headP)
    sphere(f"ear{side}", (side * 0.09, 0, 0), (0.1, 0.035, 0.045), M["body"], p)
    sphere(f"ear-in{side}", (side * 0.095, -0.02, 0), (0.07, 0.012, 0.028), M["muzzle"], p, seg=24)
    p.rotation_euler = (0, side * 0.35, 0)
    EARS.append(p)
HORNS = []
for side in (-1, 1):
    p = empty(f"horn-pivot{side}", (side * 0.075, 0.03, 0.36), headP)
    cylinder(f"horn{side}", (0, 0, 0.07), 0.024, 0.03, 0.14, M["body"], p)
    sphere(f"knob{side}", (0, 0, 0.16), (0.045, 0.045, 0.045), M["deep"], p)
    p.rotation_euler = (0, side * 0.15, 0)
    HORNS.append(p)

# laptop (shown while working)
laptop = empty("laptop", (0, -0.52, 0.03), root)
import bmesh
def box(name, loc, size, material, parent, bevel=0.01):
    me = bpy.data.meshes.new(name); bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(me); bm.free(); me.materials.append(material)
    o = bpy.data.objects.new(name, me); o.location = loc; o.scale = size
    b = o.modifiers.new("bevel", "BEVEL"); b.width = bevel; b.segments = 3
    return link(o, parent)
box("lap-base", (0, 0, 0), (0.5, 0.32, 0.02), M["laptop"], laptop)
lid = empty("lid-pivot", (0, -0.16, 0.01), laptop); lid.rotation_euler = (0.26, 0, 0)   # leans back a little, screen towards Marcel
box("lap-lid", (0, 0, 0.16), (0.5, 0.012, 0.32), M["laptop"], lid)
sphere("lap-logo", (0, -0.008, 0.16), (0.025, 0.003, 0.025), M["blush"], lid, seg=16)

# fur and felt for the plush look
if STYLE == "plush":
    fur(body, length=0.022, density=42000, radius=0.0016)
    fur(head, length=0.016, density=48000, radius=0.0014)
    for a in ARMS:
        fur(a.children[0], length=0.015, density=40000, radius=0.0013)
    fur(muzzle, length=0.008, density=60000, radius=0.001, colour=MUZZLE)
    for p in EARS:
        fur(p.children[0], length=0.008, density=50000, radius=0.001)

if STYLE == "voxel":
    # Sample each moving group on one even grid: every cube the same size, coloured by the part it sits in.
    # Eyes and spots become their own cube sets (a hair in front of the surface) so they can still blink and glow.
    from mathutils.bvhtree import BVHTree
    bpy.context.view_layer.update()
    CELL = 0.042
    dg = bpy.context.evaluated_depsgraph_get()
    decal_names = ("eye", "spot", "nostril", "blush", "knot", "bow", "muzzle", "ear-in", "knob", "hoof", "foot", "tuft")
    def cubes_mesh(name, centres, material, parent):
        bm = bmesh.new()
        for c in centres:
            r = bmesh.ops.create_cube(bm, size=CELL * 0.94)
            for v in r["verts"]:
                v.co += c
        me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free(); me.materials.append(material)
        o = bpy.data.objects.new(name, me)
        return link(o, parent)
    def voxelize(group):
        inv = group.matrix_world.inverted()
        members = [o for o in group.children if o.type == "MESH" and not o.name.startswith(("lap-", "glint"))]
        trees = []
        lo, hi = Vector((9, 9, 9)), Vector((-9, -9, -9))
        for o in members:
            me = o.evaluated_get(dg).to_mesh()
            bm = bmesh.new(); bm.from_mesh(me); bm.transform(inv @ o.matrix_world); bm.normal_update()
            for v in bm.verts:
                lo = Vector(map(min, lo, v.co)); hi = Vector(map(max, hi, v.co))
            trees.append((o, BVHTree.FromBMesh(bm), o.name.startswith(decal_names)))
            o.evaluated_get(dg).to_mesh_clear(); bm.free()
        per = {}
        x0, y0, z0 = (round(c / CELL) for c in lo)
        x1, y1, z1 = (round(c / CELL) for c in hi)
        for ix in range(x0, x1 + 1):
            for iy in range(y0, y1 + 1):
                for iz in range(z0, z1 + 1):
                    p = Vector((ix * CELL, iy * CELL, iz * CELL))
                    inside, best, bestd = False, None, 9.0
                    for o, tree, decal in trees:
                        hit = tree.find_nearest(p)
                        if hit[0] is None:
                            continue
                        loc, nrm, _, d = hit
                        if (p - loc).dot(nrm) < 0:
                            inside = True
                            if not decal and best is None:
                                best, bestd = o, d
                        if decal and d < CELL * 0.75 and d < bestd + 0.5:
                            best, bestd = o, -1
                    if inside and best is not None:
                        per.setdefault(best, []).append(p)
                    elif best is not None and bestd == -1 and any((p - t.find_nearest(p)[0]).length < CELL * 0.5 for _, t, dc in trees if not dc and t.find_nearest(p)[0] is not None):
                        per.setdefault(best, []).append(p)
        for o, *_ in trees:
            o.hide_render = True
        made = {}
        for o, pts in per.items():
            nudge = Vector((0, -CELL * 0.12, 0)) if o.name.startswith(("eye", "spot")) else Vector()
            made[o.name] = cubes_mesh("vox-" + o.name, [q + nudge for q in pts], o.active_material, group)
        return made
    vox = {}
    for g in [bodyP, headP] + ARMS + EARS + HORNS:
        vox.update(voxelize(g))
    # eyes: cubes show while open; spots: swap material for the glow
    EYES[:] = [vox[n] for n in ("eye-1", "eye1") if n in vox]
    SPOTS[:] = [vox[s.name] for s in SPOTS if s.name in vox]
    for o in vox.values():
        for poly in o.data.polygons:
            poly.use_smooth = False

# --------------------------------------------------------------------------- ground, light, camera
bpy.ops.mesh.primitive_plane_add(size=6, location=(0, 0, 0))
ground = bpy.context.active_object; ground.is_shadow_catcher = True
ground.hide_render = True        # no ground: the app draws a soft shadow underneath, and the alpha stays clean

def area(name, loc, energy, size, colour=(1, 1, 1)):
    l = bpy.data.lights.new(name, "AREA"); l.energy = energy; l.size = size; l.color = colour
    if STYLE == "toon":
        l.use_shadow = False            # cel shading wants clean bands, not soft-shadow noise
    o = bpy.data.objects.new(name, l); o.location = loc
    link(o)
    tr = o.constraints.new("TRACK_TO"); tr.target = root_target; tr.track_axis = "TRACK_NEGATIVE_Z"; tr.up_axis = "UP_Y"
    return o
root_target = empty("aim", (0, 0, 0.8))
area("key", (-1.4, -3.4, 4.0), 420 if STYLE != "toon" else 500, 3.5)
area("fill", (3.0, -2.0, 1.4), 120, 3.5, (1.0, 0.95, 0.97))
area("rim", (1.2, 3.0, 2.8), 380, 2.0, (1.0, 0.9, 0.93))

cam_d = bpy.data.cameras.new("cam"); cam_d.lens = 70
cam = bpy.data.objects.new("cam", cam_d); link(cam); scene.camera = cam
cam.location = (1.25, -5.0, 1.45)
if arg("--closeup"):
    cam_d.lens = 160
tr = cam.constraints.new("TRACK_TO"); tr.target = empty("look", (0, 0, 1.3 if arg("--closeup") else 0.74)); tr.track_axis = "TRACK_NEGATIVE_Z"; tr.up_axis = "UP_Y"

# --------------------------------------------------------------------------- motion per state
def bump(t, period, offset, length):
    p = ((t + offset) % period + period) % period
    return math.sin(math.pi * p / length) if p < length else 0.0

def pose(state, t, loop=3.2):
    breathe = math.sin(2 * math.pi * t / loop)
    bodyP.scale = (1 - 0.008 * breathe, 1 - 0.008 * breathe, 1 + 0.012 * breathe)
    blink = bump(t, 3.2, 1.0, 0.18)
    tilt, nod, yaw, hop = 0.0, 0.0, 0.0, 0.0
    earL, earR = bump(t, 3.2, 0.3, 0.35), bump(t, 3.2, 2.0, 0.35)
    for o in laptop.children_recursive:
        o.hide_render = state != "working"
    arm = [(0, -0.35, 0), (0, 0.35, 0)]
    glow = [0, 0, 0]
    if state == "idle":
        tilt = 0.06 * math.sin(2 * math.pi * t / 3.2); yaw = 0.18 * bump(t, 3.2, 0.2, 1.4)
    if state == "thinking":
        tilt = -0.18 + 0.03 * math.sin(2 * math.pi * t / 1.6); yaw = 0.2; nod = -0.08
    if state == "working":
        nod = 0.2
        tapL = max(0, math.sin(2 * math.pi * t * 2.5)); tapR = max(0, math.sin(2 * math.pi * t * 2.5 + math.pi))
        arm = [(-1.15 - 0.25 * tapL, -0.1, 0), (-1.15 - 0.25 * tapR, 0.1, 0)]
        glow = [bump(t, 1.6, -i * 0.53, 0.8) for i in range(3)]
    if state == "happy":
        p = (t % 0.8) / 0.8; hop = math.sin(math.pi * p / 0.55) if p < 0.55 else 0
        arm = [(0, -0.25, 0), (0, 2.5 + 0.25 * math.sin(2 * math.pi * t / 0.4), 0)]
        earL = earR = 0.8 * hop; nod = -0.1; blink = 0.75
    root.location.z = 0.22 * hop
    headP.rotation_euler = Euler((nod, tilt, yaw), "XYZ")       # nod about X, tilt about Y (towards camera), turn about Z
    for i, e in enumerate(EYES):
        if STYLE == "voxel":
            e.hide_render = blink > 0.5
        else:
            e.scale.z = 0.034 * max(0.1, 1 - blink)
    EARS[0].rotation_euler = (0, -0.35 - 0.6 * earL, 0); EARS[1].rotation_euler = (0, 0.35 + 0.6 * earR, 0)
    for i, h in enumerate(HORNS):
        h.rotation_euler = (0, (-1 if i == 0 else 1) * (0.15 + 0.05 * math.sin(2 * math.pi * t / 1.6 + i)), 0)
    for i, a in enumerate(ARMS):
        a.rotation_euler = arm[i]
    for i, s in enumerate(SPOTS[:3]):
        s.data.materials[0] = M["glow"] if glow[i] > 0.05 else M["deep"]
    M["glow"].node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 4.0 * max(glow)

# --------------------------------------------------------------------------- render
os.makedirs(os.path.dirname(STILL) if STILL else OUT, exist_ok=True)
if STILL:
    pose(STATE, float(arg("--t", "0.4")))
    scene.render.filepath = os.path.abspath(STILL)
    bpy.ops.render.render(write_still=True)
else:
    loop = 3.2 if STATE in ("idle",) else 1.6
    for i in range(FRAMES):
        pose(STATE, loop * i / FRAMES, loop)
        scene.render.filepath = os.path.abspath(os.path.join(OUT, f"{i:03d}.png"))
        bpy.ops.render.render(write_still=True)
print("DONE")
