"""Marcel as a Dot-style plush head: one fuzzy rose blob, bead eyes, no mouth, giraffe hints, a knit bow tie.

Run headless:
  Blender -b -P dot.py -- --still out/dot.png [--bg dark|clear] [--state idle --t 0.4]
  Blender -b -P dot.py -- --state working --frames 12 --out out/dot/working/
"""
import math
import os
import sys

import bmesh
import bpy
from mathutils import Euler, Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
def arg(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default

STATE = arg("--state", "idle"); STILL = arg("--still"); FRAMES = int(arg("--frames", "12"))
OUT = arg("--out", "out/dot/"); RES = int(arg("--res", "520")); SAMPLES = int(arg("--samples", "64"))
BG = arg("--bg", "clear")

def lin(h):
    h = h.lstrip("#"); c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92 for v in c) + (1.0,)
ROSE, DEEP, LIGHT, TEAL, TEAL_DK, INK = lin("#d47088"), lin("#a5465f"), lin("#efb3c2"), lin("#3f6b6a"), lin("#2c4f4e"), lin("#0d0a0b")

# ---------------------------------------------------------------- scene
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
prefs = bpy.context.preferences.addons["cycles"].preferences
try:
    prefs.compute_device_type = "METAL"; prefs.get_devices()
    for d in prefs.devices:
        d.use = True
    scene.cycles.device = "GPU"
except Exception:
    pass
scene.cycles.samples = SAMPLES; scene.cycles.use_denoising = True
scene.render.resolution_x = scene.render.resolution_y = RES
scene.render.film_transparent = BG == "clear"
scene.view_settings.view_transform = "AgX"; scene.view_settings.look = "AgX - Punchy"
scene.render.image_settings.file_format = "PNG"; scene.render.image_settings.color_mode = "RGBA"
world = bpy.data.worlds.new("w"); scene.world = world; world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = lin("#1a1416") if BG == "dark" else lin("#f1e4e8")
world.node_tree.nodes["Background"].inputs[1].default_value = 0.25

def link(o, parent=None):
    scene.collection.objects.link(o)
    if parent is not None:
        o.parent = parent
    return o

def empty(name, loc, parent=None):
    e = bpy.data.objects.new(name, None); e.location = loc; return link(e, parent)

def pmat(name, colour, rough=0.5, sheen=0.0, coat=0.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = colour; p.inputs["Roughness"].default_value = rough
    if sheen:
        p.inputs["Sheen Weight"].default_value = sheen; p.inputs["Sheen Roughness"].default_value = 0.4
    if coat:
        p.inputs["Coat Weight"].default_value = coat; p.inputs["Coat Roughness"].default_value = 0.05
    return m

def knit(name, colour):
    """Chunky knit: felt-like base with a stitched bump pattern (wave texture in two directions)."""
    m = pmat(name, colour, rough=0.9, sheen=0.9)
    nt = m.node_tree; p = nt.nodes["Principled BSDF"]
    tc = nt.nodes.new("ShaderNodeTexCoord")
    w = nt.nodes.new("ShaderNodeTexWave"); w.wave_type = "BANDS"; w.bands_direction = "Z"; w.inputs["Scale"].default_value = 22
    w.inputs["Distortion"].default_value = 3; w.inputs["Detail"].default_value = 2
    w2 = nt.nodes.new("ShaderNodeTexWave"); w2.wave_type = "BANDS"; w2.bands_direction = "X"; w2.inputs["Scale"].default_value = 30
    mix = nt.nodes.new("ShaderNodeMath"); mix.operation = "MULTIPLY"
    bump = nt.nodes.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.55; bump.inputs["Distance"].default_value = 0.02
    L = nt.links
    L.new(tc.outputs["Object"], w.inputs["Vector"]); L.new(tc.outputs["Object"], w2.inputs["Vector"])
    L.new(w.outputs["Fac"], mix.inputs[0]); L.new(w2.outputs["Fac"], mix.inputs[1])
    L.new(mix.outputs[0], bump.inputs["Height"]); L.new(bump.outputs["Normal"], p.inputs["Normal"])
    return m

def ellipsoid_mesh(name, centre, axes, material, parent=None, seg=64):
    bm = bmesh.new(); bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=seg // 2, radius=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * axes[0], v.co.y * axes[1], v.co.z * axes[2]))
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(material)
    o = bpy.data.objects.new(name, me); o.location = centre
    return link(o, parent)

def gumdrop(name, material, parent):
    """The Dot body: a soft gumdrop, wider and heavier at the bottom, a round dome on top."""
    bm = bmesh.new(); bmesh.ops.create_uvsphere(bm, u_segments=96, v_segments=48, radius=1.0)
    for v in bm.verts:
        x, y, z = v.co
        w = 1.0 - 0.34 * ((z + 1) / 2) ** 1.4  # narrower towards the top
        flat = 0.72 if z < 0 else 1.0          # sits flat-ish on the bottom
        v.co = Vector((x * 0.58 * w, y * 0.52 * w, z * 0.66 * flat))
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(material)
    o = bpy.data.objects.new(name, me); o.location = (0, 0, 0.48)
    return link(o, parent)

def fur(surface, length, density, radius, base, patch, light):
    """Dense short plush fur. Colour per strand from a giraffe-patch pattern (Voronoi), lighter at the tips."""
    hair = link(bpy.data.objects.new("fur-" + surface.name, bpy.data.hair_curves.new("fur-" + surface.name)), surface)
    ng = bpy.data.node_groups.new("fur-" + surface.name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n, L = ng.nodes, ng.links
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    src = n.new("GeometryNodeObjectInfo"); src.transform_space = "ORIGINAL"; src.inputs["Object"].default_value = surface
    dist = n.new("GeometryNodeDistributePointsOnFaces"); dist.inputs["Density"].default_value = density
    line = n.new("GeometryNodeCurvePrimitiveLine"); line.inputs["End"].default_value = (0, 0, length)
    res = n.new("GeometryNodeResampleCurve"); res.inputs["Count"].default_value = 5
    inst = n.new("GeometryNodeInstanceOnPoints")
    rnd = n.new("FunctionNodeRandomValue"); rnd.data_type = "FLOAT"; rnd.inputs["Min"].default_value = 0.7; rnd.inputs["Max"].default_value = 1.15
    real = n.new("GeometryNodeRealizeInstances")
    sp = n.new("GeometryNodeSplineParameter")
    taper = n.new("ShaderNodeMath"); taper.operation = "MULTIPLY_ADD"; taper.inputs[1].default_value = -0.8 * radius; taper.inputs[2].default_value = radius
    noise = n.new("ShaderNodeTexNoise"); noise.inputs["Scale"].default_value = 14.0
    centre = n.new("ShaderNodeVectorMath"); centre.operation = "SUBTRACT"; centre.inputs[1].default_value = (0.5, 0.5, 0.5)
    bend = n.new("ShaderNodeVectorMath"); bend.operation = "SCALE"
    amount = n.new("ShaderNodeMath"); amount.operation = "MULTIPLY"; amount.inputs[1].default_value = length * 1.1
    setpos = n.new("GeometryNodeSetPosition"); rad = n.new("GeometryNodeSetCurveRadius")
    hm = bpy.data.materials.new("hair-" + surface.name); hm.use_nodes = True
    hn = hm.node_tree.nodes; hn.clear(); hl = hm.node_tree.links
    hb = hn.new("ShaderNodeBsdfHairPrincipled"); hb.parametrization = "COLOR"
    hb.inputs["Roughness"].default_value = 0.42; hb.inputs["Radial Roughness"].default_value = 0.7; hb.inputs["Coat"].default_value = 0.2
    # giraffe patches: Voronoi cells on the strand roots, darker inside, rose along the "lines"
    tc = hn.new("ShaderNodeTexCoord")
    vor = hn.new("ShaderNodeTexVoronoi"); vor.feature = "DISTANCE_TO_EDGE"; vor.inputs["Scale"].default_value = 3.0; vor.inputs["Randomness"].default_value = 0.85
    ramp = hn.new("ShaderNodeValToRGB"); ramp.color_ramp.elements[0].position = 0.05; ramp.color_ramp.elements[0].color = base
    ramp.color_ramp.elements[1].position = 0.22; ramp.color_ramp.elements[1].color = tuple(b + (p - b) * 0.8 for b, p in zip(base, patch))
    info = hn.new("ShaderNodeHairInfo")
    tip = hn.new("ShaderNodeMixRGB"); tip.blend_type = "MIX"; tip.inputs["Color2"].default_value = light
    tipf = hn.new("ShaderNodeMath"); tipf.operation = "POWER"; tipf.inputs[1].default_value = 3.0
    tipk = hn.new("ShaderNodeMath"); tipk.operation = "MULTIPLY"; tipk.inputs[1].default_value = 0.45
    ho = hn.new("ShaderNodeOutputMaterial")
    hl.new(tc.outputs["Object"], vor.inputs["Vector"]); hl.new(vor.outputs["Distance"], ramp.inputs["Fac"])
    hl.new(ramp.outputs["Color"], tip.inputs["Color1"])
    hl.new(info.outputs["Intercept"], tipf.inputs[0]); hl.new(tipf.outputs[0], tipk.inputs[0]); hl.new(tipk.outputs[0], tip.inputs["Fac"])
    hl.new(tip.outputs["Color"], hb.inputs["Color"]); hl.new(hb.outputs[0], ho.inputs[0])
    sm = n.new("GeometryNodeSetMaterial"); sm.inputs["Material"].default_value = hm
    L.new(src.outputs["Geometry"], dist.inputs["Mesh"]); L.new(line.outputs[0], res.inputs["Curve"])
    L.new(dist.outputs["Points"], inst.inputs["Points"]); L.new(res.outputs[0], inst.inputs["Instance"])
    L.new(dist.outputs["Rotation"], inst.inputs["Rotation"]); L.new(rnd.outputs[0], inst.inputs["Scale"])
    L.new(inst.outputs[0], real.inputs[0])
    L.new(noise.outputs["Color"], centre.inputs[0]); L.new(centre.outputs[0], bend.inputs[0])
    L.new(sp.outputs["Factor"], amount.inputs[0]); L.new(amount.outputs[0], bend.inputs["Scale"])
    L.new(real.outputs[0], setpos.inputs["Geometry"]); L.new(bend.outputs[0], setpos.inputs["Offset"])
    L.new(sp.outputs["Factor"], taper.inputs[0]); L.new(setpos.outputs[0], rad.inputs["Curve"]); L.new(taper.outputs[0], rad.inputs["Radius"])
    L.new(rad.outputs[0], sm.inputs["Geometry"]); L.new(sm.outputs[0], go.inputs[0])
    hair.data.materials.append(hm)
    hair.modifiers.new("fur", "NODES").node_group = ng
    return hair

# ---------------------------------------------------------------- Marcel the Dot (faces -Y)
skin = pmat("skin", ROSE, rough=1.0)
felt = pmat("felt", DEEP, rough=1.0, sheen=0.9)
bead = pmat("bead", INK, rough=0.06, coat=1.0)
bowknit = knit("knit-teal", TEAL); knotknit = knit("knit-teal-dk", TEAL_DK)

root = empty("marcel", (0, 0, 0))
body = gumdrop("blob", skin, root)
fur(body, length=0.048, density=80000, radius=0.0012, base=ROSE, patch=DEEP, light=LIGHT)

EYES = []
for side in (-1, 1):
    e = ellipsoid_mesh(f"eye{side}", (side * 0.12, -0.43, 0.66), (0.034, 0.024, 0.048), bead, root, seg=32)
    EYES.append(e)

EARS = []
for side in (-1, 1):
    p = empty(f"earP{side}", (side * 0.36, -0.02, 0.9), root)
    ear = ellipsoid_mesh(f"ear{side}", (side * 0.1, 0, 0), (0.12, 0.05, 0.055), skin, p, seg=32)
    fur(ear, length=0.014, density=90000, radius=0.0009, base=ROSE, patch=ROSE, light=LIGHT)
    p.rotation_euler = (0, side * 0.35, 0); EARS.append(p)

HORNS = []
for side in (-1, 1):
    p = empty(f"hornP{side}", (side * 0.1, 0.02, 1.06), root)
    stub = ellipsoid_mesh(f"horn{side}", (0, 0, 0.09), (0.04, 0.04, 0.1), skin, p, seg=32)
    fur(stub, length=0.012, density=90000, radius=0.0009, base=ROSE, patch=ROSE, light=LIGHT)
    ellipsoid_mesh(f"knob{side}", (0, 0, 0.2), (0.055, 0.055, 0.05), felt, p, seg=32)
    p.rotation_euler = (0, side * 0.18, 0); HORNS.append(p)

bow = empty("bow", (0, -0.5, 0.3), root); bow.scale = (1.3, 1.3, 1.3)
for side in (-1, 1):
    w = ellipsoid_mesh(f"bow{side}", (side * 0.085, 0, 0), (0.09, 0.04, 0.065), bowknit, bow, seg=40)
    w.rotation_euler = (0, side * 0.2, 0)
ellipsoid_mesh("knot", (0, -0.02, 0), (0.035, 0.03, 0.04), knotknit, bow, seg=24)
bow.rotation_euler = (-0.12, 0, 0)

# ---------------------------------------------------------------- light and camera (soft studio, strong rims for the fuzz)
aim = empty("aim", (0, 0, 0.55))
def area(name, loc, energy, size, colour=(1, 1, 1)):
    l = bpy.data.lights.new(name, "AREA"); l.energy = energy; l.size = size; l.color = colour
    o = link(bpy.data.objects.new(name, l)); o.location = loc
    t = o.constraints.new("TRACK_TO"); t.target = aim; t.track_axis = "TRACK_NEGATIVE_Z"; t.up_axis = "UP_Y"
area("key", (-1.6, -3.0, 3.2), 300, 2.6)
area("fill", (2.4, -2.4, 1.0), 90, 3.0, (1.0, 0.95, 0.97))
area("rimL", (-2.4, 2.2, 1.6), 600, 1.4, (1.0, 0.92, 0.95))
area("rimR", (2.4, 2.2, 1.8), 600, 1.4, (1.0, 0.92, 0.95))
cam_d = bpy.data.cameras.new("cam"); cam_d.lens = 85
cam = link(bpy.data.objects.new("cam", cam_d)); scene.camera = cam
cam.location = (0.0, -4.6, 1.05)
t = cam.constraints.new("TRACK_TO"); t.target = empty("look", (0, 0, 0.62)); t.track_axis = "TRACK_NEGATIVE_Z"; t.up_axis = "UP_Y"

# ---------------------------------------------------------------- motion
def bump(t, period, offset, length):
    p = ((t + offset) % period + period) % period
    return math.sin(math.pi * p / length) if p < length else 0.0

def pose(state, t, loop):
    breathe = math.sin(2 * math.pi * t / loop)
    sq, hop, tilt, turn, look = 0.0, 0.0, 0.0, 0.0, (0.0, 0.0)
    blink = bump(t, loop, 0.25 * loop, 0.18) if state != "happy" else 0
    earL, earR = bump(t, loop, 0.1 * loop, 0.3), bump(t, loop, 0.6 * loop, 0.3)
    if state == "idle":
        tilt = 0.05 * math.sin(2 * math.pi * t / loop); turn = 0.12 * bump(t, loop, 0.55 * loop, 1.2)
    if state == "thinking":
        tilt = -0.12 + 0.03 * math.sin(2 * math.pi * t / loop); look = (0.012, 0.012)
    if state == "working":
        sq = 0.02 * max(0, math.sin(2 * math.pi * t * 2 / loop)); look = (0.0, -0.01)
    if state == "happy":
        p = (t % (loop / 2)) / (loop / 2); hop = math.sin(math.pi * p / 0.6) if p < 0.6 else 0
        sq = -0.05 * hop + (0.06 * math.sin(math.pi * (p - 0.6) / 0.4) if p >= 0.6 else 0); earL = earR = hop
    root.scale = (1 + 0.01 * breathe + 0.6 * sq, 1 + 0.01 * breathe + 0.6 * sq, 1 - 0.012 * breathe - sq)
    root.location.z = 0.18 * hop
    root.rotation_euler = Euler((0, tilt, turn), "XYZ")
    for i, e in enumerate(EYES):
        e.scale.z = max(0.08, 1 - blink)
        e.location.x = (-1 if i == 0 else 1) * 0.12 + look[0]; e.location.z = 0.66 + look[1]
    EARS[0].rotation_euler = (0, -0.35 - 0.5 * earL, 0); EARS[1].rotation_euler = (0, 0.35 + 0.5 * earR, 0)
    for i, h in enumerate(HORNS):
        h.rotation_euler = (0, (-1 if i == 0 else 1) * (0.18 + 0.06 * math.sin(2 * math.pi * t / loop * 2 + i)), 0)

os.makedirs(os.path.dirname(STILL) if STILL else OUT, exist_ok=True)
loop = 3.2 if STATE == "idle" else 1.6
if STILL:
    pose(STATE, float(arg("--t", "0.4")), loop)
    scene.render.filepath = os.path.abspath(STILL); bpy.ops.render.render(write_still=True)
else:
    for i in range(FRAMES):
        pose(STATE, loop * i / FRAMES, loop)
        scene.render.filepath = os.path.abspath(os.path.join(OUT, f"{i:03d}.png")); bpy.ops.render.render(write_still=True)
print("DONE")
