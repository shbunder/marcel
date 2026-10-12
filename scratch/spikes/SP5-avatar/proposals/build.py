"""Assembles the proposals page into one self-contained HTML file: out/marcel-ten-ways.html"""
import base64, json, os
H = os.path.dirname(os.path.abspath(__file__))
R = lambda p: open(os.path.join(H, p)).read()
png = base64.b64encode(open(os.path.join(H, "../SP5Avatar/giraffe-sheet.png"), "rb").read()).decode()
info = json.load(open(os.path.join(H, "../SP5Avatar/giraffe-sheet.json")))
page = R("page.template.html")
for marker, code in [("/*SPRITE_DATA*/", f"const SPRITE_PNG = 'data:image/png;base64,{png}';\nconst SPRITE_INFO = {json.dumps(info)};"),
                     ("/*MARCEL_JS*/", R("../vector/marcel.js")), ("/*AVATARS2D_JS*/", R("avatars2d.js")),
                     ("/*CONTROLLER_JS*/", R("controller.js"))]:
    assert marker in page, marker
    page = page.replace(marker, code)
open(os.path.join(H, "out/marcel-ten-ways.html"), "w").write(page)
# a local copy with a doctype and charset, for previewing outside the artifact viewer
open(os.path.join(H, "out/local.html"), "w").write('<!doctype html><meta charset="utf-8">' + page)
# the rendered 3D loops sit next to the page
import glob, shutil
for f in glob.glob(os.path.join(H, "../blender/out/anim/*.webp")):
    shutil.copy(f, os.path.join(H, "out", os.path.basename(f)))
print(len(page) // 1024, "KB")
