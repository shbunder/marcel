/* Pixel Zoo — old-school (Game Boy Zelda) avatars, built from parts.
 *
 * An avatar is: species (which parts) x colour (one hue -> a small ramp) x mood (which face)
 * x personality (accessory, resting face, how lively it moves). Give it a name instead and the name is hashed
 * into a stable pick, like "boring avatars": the same name always gives the same animal.
 *
 * How a frame is drawn, the way a pixel artist would by hand:
 *   1. parts (ears, head, body, tail...) are small fill maps on a 32x32 grid, stacked back to front;
 *   2. each part gets a 1-px dark outline where it meets the empty canvas or a part behind it;
 *   3. one shade on the bottom/right edge, one highlight on the top-left (flat, like the GB Color games);
 *   4. the face (eyes, brows, mouth, cheeks) and effects (!, ?, Zz, tears) are stamped on last.
 * Runs in the browser (window.PixelZoo) and in node (require). No dependencies.
 */
(function (root) {
  "use strict";
  const N = 32;

  // ------------------------------------------------------------------ colour
  function hexToHsl(hex) {
    const h = hex.replace("#", "");
    const r = parseInt(h.slice(0, 2), 16) / 255, g = parseInt(h.slice(2, 4), 16) / 255, b = parseInt(h.slice(4, 6), 16) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
    if (mx === mn) return [0, 0, l];
    const d = mx - mn, s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    const hu = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [hu * 60, s, l];
  }
  function clamp(v) { return Math.max(0, Math.min(1, v)); }
  function hslToHex(h, s, l) {
    h = ((h % 360) + 360) % 360; s = clamp(s); l = clamp(l);
    const k = (n) => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
    const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    return "#" + [f(0), f(8), f(4)].map((v) => Math.round(v * 255).toString(16).padStart(2, "0")).join("");
  }
  // move a hue a few degrees towards a target (shadows lean cool and purple, highlights lean warm)
  function towards(h, target, deg) {
    const d = ((target - h + 540) % 360) - 180;
    return h + Math.sign(d) * Math.min(Math.abs(d), deg);
  }

  /** One base colour -> the whole palette of an avatar. Letters are what the parts are drawn with. */
  function palette(base, accentHex) {
    const [h, s, l] = hexToHsl(base);
    const grey = s < 0.12;
    const sh = grey ? s : s * 0.85;
    const pal = {
      b: base,                                                        // body
      d: hslToHex(towards(h, 265, 12), sh, l - 0.13),                 // shade
      l: hslToHex(towards(h, 55, 10), s, Math.min(0.95, l + 0.1)),    // highlight
      o: hslToHex(towards(h, 285, 30), grey ? 0.14 : 0.4, 0.15),      // outline and eyes
      c: "#fff4e4",                                                    // cream (muzzle, belly)
      C: "#f2d6c0",                                                    // cream shade
      i: "#ff9db0",                                                    // inside of ears, noses
      p: "#ff7896",                                                    // blush
      w: "#ffffff",
      r: "#a8324e", R: "#ff8fa6",                                      // mouth, tongue
      t: "#a5e4ff", T: "#4aa8e8",                                      // tears, sweat
      y: "#ffd83d", Y: "#e89a1c",                                      // stars, sparkles, crown
      h: "#ff4d6d", H: "#c2253f",                                      // hearts, anger
      g: "#2fb39a", G: "#1c7d6c",                                      // teal accessories (bow tie)
      v: "#9a7bff", V: "#6a4fd1",                                      // violet accessories (cap, scarf)
      q: "#4f7f7c", Q: "#355a58", x: "#c8f3ea",                        // laptop lid, its shade, screen light
      k: "#2b2433",                                                    // sunglasses
      S: "rgba(60,30,60,0.16)",                                        // ground shadow
    };
    // an accessory must stand out from the fur: green and teal animals get a rose bow tie, violet ones a teal cap
    const near = (a, b, r) => !grey && s > 0.25 && Math.abs(((a - b + 540) % 360) - 180) < r;
    if (near(h, 150, 55)) { pal.g = "#f0607e"; pal.G = "#b83a5c"; }
    if (near(h, 260, 45) || near(h, 300, 30)) { pal.v = "#2fb39a"; pal.V = "#1c7d6c"; }
    if (accentHex) {
      const [ah, as, al] = hexToHsl(accentHex);
      pal.a = accentHex; pal.A = hslToHex(towards(ah, 265, 14), as, al - 0.1);
    } else {
      pal.a = hslToHex(towards(h, 330, 14), s * 0.6, l - 0.2);      // spots, stripes, markings
      pal.A = hslToHex(towards(h, 300, 22), s * 0.55, l - 0.3);
    }
    return pal;
  }
  const SHADE = { b: "d", c: "C", a: "A", y: "Y", g: "G", v: "V", h: "H", q: "Q" };

  // ------------------------------------------------------------------ shapes
  /** A filled block w x h with rounded corners; top/bottom list how far the first/last rows are pulled in. */
  function blob(w, h, ch, top = [], bottom = null) {
    bottom = bottom || top.slice().reverse();
    const rows = [];
    for (let y = 0; y < h; y++) {
      let ins = y < top.length ? top[y] : 0;
      const fromEnd = h - 1 - y;
      if (fromEnd < bottom.length) ins = Math.max(ins, bottom[bottom.length - 1 - fromEnd]);
      rows.push(".".repeat(ins) + ch.repeat(Math.max(0, w - 2 * ins)) + ".".repeat(ins));
    }
    return rows;
  }
  const mirror = (rows) => rows.map((r) => r.split("").reverse().join(""));

  // A part: rows of letters at (x, y). ol: "full" (outline all round, drawn over what is behind),
  // "outer" (outline only against empty canvas: a patch that merges into its part), "none".
  const L = (x, y, rows, opt = {}) => ({ x, y, rows, ol: "full", shade: true, group: "body", ...opt });
  const H = (x, y, rows, opt = {}) => L(x, y, rows, { group: "head", ...opt });
  const P = (x, y, rows, opt = {}) => H(x, y, rows, { ol: "none", shade: false, ...opt });   // a painted detail

  // ------------------------------------------------------------------ species
  // Face layout: eyeL/eyeR = top-left pixel of each 2-wide eye; mouth = centre line and top row;
  // cheeks = blush; fx = where symbols float; top/neck/flower = where accessories sit.
  const ROUND_FACE = { eyeL: [11, 12], eyeR: [19, 12], mouth: [16, 16], cheekL: [8, 15], cheekR: [22, 15],
    fx: [27, 0], top: [16, 6], neck: [16, 20], flower: [8, 6] };
  const ROUND_HEAD = () => H(6, 6, blob(20, 15, "b", [4, 2, 1, 1], [1, 1, 2, 4]));
  const BODY = (limb = "b") => [
    L(9, 19, blob(14, 10, "b", [3, 1], [1])),                       // torso
    L(12, 22, blob(8, 6, "c", [2], [1, 2]), { ol: "none" }),         // belly
    L(8, 26, blob(6, 4, limb, [1], [0])),                           // feet
    L(18, 26, blob(6, 4, limb, [1], [0])),
    L(6, 21, blob(4, 5, limb, [1], [1])),                           // arms
    L(22, 21, blob(4, 5, limb, [1], [1])),
  ];
  const SHADOW = L(7, 29, blob(18, 3, "S", [3], [3]), { ol: "none", shade: false, group: "shadow" });
  const EAR_TRI = ["b.....", "bb....", "bbb...", "bbbb..", "bbbbb.", "bbbbbb", "bbbbbb"];

  const SPECIES = {
    giraffe: {
      label: "Giraffe", color: "#f08aa0",
      face: { eyeL: [12, 6], eyeR: [18, 6], mouth: [16, 11], cheekL: [10, 9], cheekR: [20, 9], fxDots: [25, 2],
        fx: [27, 0], top: [16, 5], crown: [16, 3], neck: [16, 19], flower: [10, 3] }, small: ["crown", "cap"],
      parts: () => [
        SHADOW,
        L(8, 20, blob(16, 9, "b", [3, 1], [1])),                                  // body
        P(9, 22, ["aa", "aa"]), P(21, 22, ["aa", "aa"]), P(10, 25, ["aa"]), P(20, 25, ["aa"]),
        L(10, 22, blob(4, 8, "b", [0], [0])), L(18, 22, blob(4, 8, "b", [0], [0])), // front legs
        P(10, 29, ["aaaa"]), P(18, 29, ["aaaa"]), P(11, 24, ["a"]), P(19, 25, ["a"]),
        L(13, 11, blob(6, 12, "b")),                                              // neck
        P(13, 14, ["aa....", "aa..aa", "....aa", "a.....", "a..aa.", "...aa.", "a....."]),
        H(11, 0, ["aa", "aa", "bb", "bb"], { shade: false, front: true }), H(19, 0, ["aa", "aa", "bb", "bb"], { shade: false, front: true }), // horns
        H(4, 5, blob(6, 3, "b", [1], [0])), H(22, 5, blob(6, 3, "b", [1], [0])),   // ears, sticking out
        P(5, 6, ["iiii"]), P(23, 6, ["iiii"]),
        H(9, 2, blob(14, 14, "b", [3, 1, 0], [0, 1, 2, 4])),                      // head
        H(11, 9, blob(10, 7, "c", [1, 0], [0, 1, 3]), { ol: "none", light: false }), // muzzle
        P(17, 3, ["aa", "aa"]), P(10, 6, ["a", "a"]),                            // head spots
        P(13, 9, ["d....d"]),                                                    // nostrils
      ],
    },
    crocodile: {
      label: "Crocodile", color: "#4fae5a",
      face: { eyeL: [10, 4], eyeR: [20, 4], mouth: [16, 15], cheekL: [7, 12], cheekR: [23, 12],
        fx: [28, 0], top: [16, 6], neck: [16, 20], flower: [5, 5], crownBehind: [16, 4] }, small: ["cap", "crownBehind"],
      wideMouth: 3, teeth: true, reptileEyes: true,
      parts: () => [
        SHADOW,
        L(21, 20, ["....b.", "...bbb", "..bbbb", "bbbbb.", "bbbb.."]),                    // tail
        ...BODY(),
        P(13, 22, ["CCCCCC", "......", "CCCCCC", "......"]),
        H(5, 6, blob(22, 8, "b", [2, 0], [0])),                                          // skull
        H(7, 10, blob(18, 10, "b", [0], [0, 1, 3]), { ol: "outer", light: false }),       // long snout
        H(7, 2, blob(7, 7, "b", [2, 0], [0]), { ol: "outer", shade: false }),             // eye bumps, merged into the skull
        H(18, 2, blob(7, 7, "b", [2, 0], [0]), { ol: "outer", shade: false }),
        P(14, 13, ["d..d"]),                                                            // nostrils
      ],
    },
    frog: {
      label: "Frog", color: "#8fd14f", wideMouth: 1,
      face: { eyeL: [10, 6], eyeR: [20, 6], mouth: [16, 15], cheekL: [7, 13], cheekR: [23, 13],
        fx: [28, 0], top: [16, 7], neck: [16, 20], flower: [5, 7], crownBehind: [16, 4] }, small: ["cap", "crownBehind"],
      parts: () => [
        SHADOW, ...BODY(),
        H(7, 3, blob(8, 7, "b", [2, 1], [0])), H(17, 3, blob(8, 7, "b", [2, 1], [0])),
        H(4, 7, blob(24, 13, "b", [4, 1, 0], [0, 1, 3])),
        P(8, 4, blob(6, 4, "c", [1], [0])), P(18, 4, blob(6, 4, "c", [1], [0])),
        P(7, 8, ["b".repeat(8)]), P(17, 8, ["b".repeat(8)]),
      ],
    },
    cat: {
      label: "Cat", color: "#f2a65a",
      face: { ...ROUND_FACE },
      parts: () => [
        SHADOW,
        L(23, 17, ["....bb", "....bb", "...bb.", "...bb.", "..bb..", ".bb...", "bb...."]),     // tail
        ...BODY(),
        H(6, 1, EAR_TRI), H(20, 1, mirror(EAR_TRI)),
        P(7, 3, ["i...", "ii..", "iii."]), P(21, 3, mirror(["i...", "ii..", "iii."])),
        ROUND_HEAD(),
        P(13, 6, ["a.a.a.", "a.a.a.", "..a..."]),                                        // tabby stripes
        P(15, 15, ["ii"]),                                                               // nose
        P(3, 15, ["ooo", "...", "ooo"]), P(26, 15, ["ooo", "...", "ooo"]),                 // whiskers
      ],
    },
    fox: {
      label: "Fox", color: "#f07a35",
      face: { ...ROUND_FACE },
      parts: () => [
        SHADOW,
        L(24, 12, ["..ccc..", ".ccccc.", ".bbbbbb", "bbbbbbb", "bbbbbb.", ".bbbbb.", "..bbbb.", "...bbb.", "...bb.."]),
        ...BODY(),
        H(5, 0, ["b......", "bb.....", "bbb....", "bbbb...", "bbbbb..", "bbbbbb.", "bbbbbbb", "bbbbbbb"]),
        H(20, 0, mirror(["b......", "bb.....", "bbb....", "bbbb...", "bbbbb..", "bbbbbb.", "bbbbbbb", "bbbbbbb"])),
        P(6, 2, ["o...", "oc..", "occ.", "occc"]), P(22, 2, mirror(["o...", "oc..", "occ.", "occc"])),
        ROUND_HEAD(),
        H(6, 15, ["ccccc..........ccccc", "cccccccc....cccccccc", ".cccccccccccccccccc.", "..cccccccccccccccc..", "....cccccccccccc...."], { ol: "outer" }),
        P(15, 14, ["oo"]),
      ],
    },
    bear: {
      label: "Bear", color: "#b5794c",
      face: { ...ROUND_FACE },
      parts: () => [
        SHADOW, ...BODY(),
        H(5, 3, blob(6, 6, "b", [2, 1], [1, 2])), H(21, 3, blob(6, 6, "b", [2, 1], [1, 2])),
        P(7, 5, ["cc", "cc"]), P(23, 5, ["cc", "cc"]),
        ROUND_HEAD(),
        H(12, 14, blob(8, 6, "c", [2, 0], [0, 2]), { ol: "none" }),
        P(14, 14, ["oooo", ".oo."]),
      ],
      mouthDy: 1,
    },
    panda: {
      label: "Panda", color: "#f1edf2", accent: "#3a3340", keepAccent: true,
      face: { ...ROUND_FACE },
      parts: () => [
        SHADOW, ...BODY("a"),
        H(5, 3, blob(6, 6, "a", [2, 1], [1, 2])), H(21, 3, blob(6, 6, "a", [2, 1], [1, 2])),
        ROUND_HEAD(),
        P(9, 11, [".aaa", "aaaa", "aaaa", ".aa."]), P(19, 11, ["aaa.", "aaaa", "aaaa", ".aa."]),
        P(14, 15, ["oooo", ".oo."]),
      ],
      limb: "a",
      eyeLight: true, // the eyes sit on dark patches, so they are drawn light
      mouthDy: 1,
    },
    rabbit: {
      label: "Rabbit", color: "#ece4f2",
      face: { ...ROUND_FACE },
      parts: () => [
        SHADOW, ...BODY(),
        H(8, -1, blob(5, 10, "b", [1], [0])), H(19, -1, blob(5, 10, "b", [1], [0])),
        P(10, 1, ["i", "i", "i", "i", "i", "i"]), P(21, 1, ["i", "i", "i", "i", "i", "i"]),
        ROUND_HEAD(),
        P(15, 15, ["ii"]),
      ],
    },
    dog: {
      label: "Dog", color: "#e0b07a", accent: "#8a5a3a",
      face: { ...ROUND_FACE },
      parts: () => [
        SHADOW,
        L(23, 18, ["..b", ".bb", "bb.", "b.."]),
        ...BODY(),
        ROUND_HEAD(),
        P(18, 9, blob(6, 6, "a", [1], [1])),                                   // eye patch
        H(3, 7, blob(5, 10, "a", [1, 0], [0, 1, 2])), H(24, 7, blob(5, 10, "a", [1, 0], [0, 1, 2])),
        H(12, 14, blob(8, 6, "c", [2, 0], [0, 2]), { ol: "none" }),
        P(14, 14, ["oooo", ".oo."]),
      ],
      mouthDy: 1,
    },
    pig: {
      label: "Pig", color: "#f9a9bd",
      face: { ...ROUND_FACE, eyeL: [11, 11], eyeR: [19, 11], mouth: [16, 18] },
      parts: () => [
        SHADOW, ...BODY(),
        ROUND_HEAD(),
        H(5, 3, ["bb....", "bbbb..", "bbbbb.", ".bbbbb", "..bbb."]), H(21, 3, mirror(["bb....", "bbbb..", "bbbbb.", ".bbbbb", "..bbb."])),
        H(12, 14, blob(8, 4, "l", [1], [1]), { shade: false }),
        P(14, 15, ["d..d", "d..d"]),
      ],
    },
    mouse: {
      label: "Mouse", color: "#b3aec2",
      face: { ...ROUND_FACE },
      parts: () => [
        SHADOW,
        L(23, 22, ["....b", "...b.", "..b..", "bb..."], { shade: false }),
        ...BODY(),
        H(2, 1, blob(9, 9, "b", [3, 1, 0], [0, 1, 3])), H(21, 1, blob(9, 9, "b", [3, 1, 0], [0, 1, 3])),
        P(4, 3, blob(5, 5, "i", [1], [1])), P(23, 3, blob(5, 5, "i", [1], [1])),
        ROUND_HEAD(),
        P(15, 15, ["ii"]),
        P(3, 15, ["ooo", "...", "ooo"]), P(26, 15, ["ooo", "...", "ooo"]),
      ],
    },
  };

  // ------------------------------------------------------------------ faces
  // Eye stamps are drawn for the LEFT eye, from its top-left pixel. The right eye is the mirror image, unless
  // `same` is set (a shine stays on the same side). `o` is the eye colour.
  const EYES = {
    open:    { rows: ["ow", "oo", "oo"], same: true },
    small:   { rows: ["oo", "oo"], dy: 1 },
    blink:   { rows: ["oo"], dy: 2 },
    happy:   { rows: [".oo.", "o..o"], dx: -1, dy: 1 },
    calm:    { rows: ["o..o", ".oo."], dx: -1, dy: 1 },
    sleepy:  { rows: ["oooo", ".oo."], dx: -1, dy: 1 },
    half:    { rows: ["oooo", ".oo."], dx: -1, dy: 1 },
    wide:    { rows: [".oo.", "owwo", "owoo", ".oo."], dx: -1, dy: -1, same: true },
    dot:     { rows: ["o"], dy: 1 },
    star:    { rows: ["..y..", ".yyy.", "yyyyy", ".y.y."], dx: -2, dy: -1 },
    heart:   { rows: ["hh.hh", "hhhhh", ".hhh.", "..h.."], dx: -2, dy: -1 },
    cross:   { rows: ["o.o", ".o.", "o.o"], dx: -1 },
    spiral:  { rows: ["ooo.", "o..o", "o.oo", "o..."], dx: -1, dy: -1 },
    closed:  { rows: ["oooo"], dx: -1, dy: 1 },
    angry:   { rows: ["oo", "oo"], dy: 1 },
    teary:   { rows: ["ow", "oo", "tt"], same: true },
    look:    { rows: ["wo", "oo", "oo"], same: true },
  };
  // Crocodiles keep their yellow, slit-pupil eyes under a heavy lid whenever the eyes are open.
  const REPTILE = {
    open:  { rows: ["aaaa", ".ow.", ".oo.", ".oo."], dx: -1, same: true },
    look:  { rows: ["aaaa", ".wo.", ".oo.", ".oo."], dx: -1, same: true },
    teary: { rows: ["aaaa", ".ow.", ".oo.", ".tt."], dx: -1, same: true },
    small: { rows: ["aaaa", "....", ".oo.", ".oo."], dx: -1 },
    blink: { rows: ["aaaa", "....", "....", ".oo."], dx: -1 },
    half:  { rows: ["aaaa", "aaaa", ".oo.", ".oo."], dx: -1 },
    angry: { rows: ["aaaa", "..aa", ".oo.", ".oo."], dx: -1 },
  };

  // Brows sit above the eye. Drawn for the left eye; the right one is mirrored.
  const BROWS = {
    angry:   { rows: ["oo..", "..oo"], dx: -1, dy: -2 },
    sad:     { rows: ["..oo", "oo.."], dx: -1, dy: -2 },
    raised:  { rows: [".oo."], dx: -1, dy: -3 },
    flat:    { rows: ["oooo"], dx: -1, dy: -1 },
    worried: { rows: ["...o", ".oo."], dx: -1, dy: -3 },
  };
  // Mouths are centred on the face's middle line; all even widths.
  const MOUTHS = {
    smile:   [".o..o.", "..oo.."],
    flat:    ["..oo.."],
    grin:    [".oooo.", ".orro.", "..oo.."],
    laugh:   ["oooooo", "orrrro", ".oRRo.", "..oo.."],
    laugh2:  [".oooo.", "orrrro", ".oRRo.", "..oo.."],
    open:    ["..oo..", ".orro.", "..oo.."],
    O:       ["..oo..", ".orro.", ".orro.", "..oo.."],
    frown:   ["..oo..", ".o..o."],
    wavy:    [".o.o..", "o.o.o."],
    tongue:  [".o..o.", "..oo..", "..RR.."],
    smirk:   ["....o.", ".ooo.."],
    teeth:   ["oooooo", "owwwwo", "oooooo"],
    small_o: ["..oo..", "..oo.."],
    kiss:    ["..o...", "...o..", "..o..."],
  };

  /** A wider mouth (frogs, crocodiles): repeat the two middle columns. */
  function widen(rows, k) {
    return rows.map((r) => { const m = r.length / 2; return r.slice(0, m) + r.slice(m - 1, m + 1).repeat(k) + r.slice(m); });
  }

  // Effects above the head; f is the frame number so they can move.
  const FX = {
    alert:    (f) => ({ rows: ["yy", "yy", "yy", "..", "yy"], dy: f % 4 < 2 ? 0 : -1 }),
    question: (f) => ({ rows: [".yy.", "y..y", "..y.", "....", "..y."], dy: f % 4 < 2 ? 0 : -1 }),
    zzz:      (f) => ({ rows: f % 8 < 4 ? ["...yyy", ".....y", "....y.", "...yyy", "yy....", ".y....", "yy...."] : ["......", "yyy...", "..y...", ".y....", "yyy...", "......", "......"] }),
    heart:    (f) => ({ rows: f % 4 < 2 ? ["hh.hh", "hhhhh", ".hhh.", "..h.."] : [".....", ".h.h.", ".hhh.", "..h.."] }),
    dots:     (f) => { const n = Math.floor(f / 2) % 4; return { rows: [".wwwwwww.", "ww" + [0, 1, 2].map((i) => (i < n ? "o" : "w")).join("w") + "ww", ".wwwwwww.", "ww......."] }; },
    anger:    (f) => ({ rows: f % 4 < 2 ? [".h.h.", "hh.hh", ".....", "hh.hh", ".h.h."] : [".....", ".h.h.", "hh.hh", ".h.h.", "....."] }),
    sparkle:  (f) => ({ rows: f % 4 < 2 ? ["..y..", ".yyy.", "..y.."] : ["y...y", ".....", "y...y"] }),
    note:     (f) => ({ rows: [".yyy", ".y.y", ".y.y", "yy.y", "yy.."], dy: f % 4 < 2 ? 0 : -1 }),
    sweat:    (f) => ({ rows: [".t.", "ttt", "tTt", ".t."], at: "side", dy: f % 4 < 2 ? 0 : 1 }),
    tears:    (f) => ({ tears: true }),
    dizzy:    (f) => ({ rows: f % 4 < 2 ? [".y...........", "yyy..........", ".y.........y.", "...........yyy".slice(0, 13), "...........y."] : ["...........y.", "..........yyy", ".y.........y.", "yyy..........", ".y..........."], at: "crown" }),
    bulb:     (f) => ({ rows: [".yyy.", "yyyyy", "yyyyy", ".yyy.", ".ooo."].map((r, i) => (f % 4 < 2 || i > 3 ? r : r.replace(/y/g, "Y"))) }),
  };

  // The moods. A mouth list animates; `fx` floats a symbol over the head.
  const MOODS = {
    neutral:     { label: "Neutral",     eyes: "open", mouth: "smile" },
    happy:       { label: "Happy",       eyes: "happy", mouth: "grin", blush: true },
    laughing:    { label: "Laughing",    eyes: "happy", mouth: ["laugh", "laugh2"], blush: true },
    love:        { label: "In love",     eyes: "heart", mouth: "smile", blush: true, fx: "heart" },
    excited:     { label: "Excited",     eyes: "star", mouth: "grin", blush: true, fx: "sparkle" },
    wink:        { label: "Wink",        eyes: "open", eyesR: "closed", mouth: "tongue" },
    proud:       { label: "Proud",       eyes: "calm", mouth: "smirk", brows: "raised" },
    calm:        { label: "Calm",        eyes: "calm", mouth: "smile", blush: true },
    thinking:    { label: "Thinking",    eyes: "look", mouth: "flat", brows: "worried", fx: "dots" },
    idea:        { label: "Idea",        eyes: "wide", mouth: "open", fx: "bulb" },
    focused:     { label: "Focused",     eyes: "half", mouth: "flat", brows: "flat" },
    working:     { label: "Working",     eyes: "small", mouth: "smile", prop: "laptop" },
    surprised:   { label: "Surprised",   eyes: "wide", mouth: "O", fx: "alert" },
    confused:    { label: "Confused",    eyes: "open", eyesR: "small", mouth: "wavy", brows: "worried", fx: "question" },
    worried:     { label: "Worried",     eyes: "open", mouth: "wavy", brows: "sad", fx: "sweat" },
    sad:         { label: "Sad",         eyes: "teary", mouth: "frown", brows: "sad" },
    crying:      { label: "Crying",      eyes: "closed", mouth: ["open", "O"], brows: "sad", fx: "tears" },
    angry:       { label: "Angry",       eyes: "angry", mouth: "teeth", brows: "angry", fx: "anger" },
    grumpy:      { label: "Grumpy",      eyes: "half", mouth: "frown", brows: "flat" },
    sleepy:      { label: "Sleepy",      eyes: "sleepy", mouth: "small_o", fx: "zzz" },
    dizzy:       { label: "Dizzy",       eyes: "spiral", mouth: "wavy", fx: "dizzy" },
    embarrassed: { label: "Embarrassed", eyes: "happy", mouth: "wavy", blush: true, fx: "sweat" },
    singing:     { label: "Singing",     eyes: "calm", mouth: ["open", "small_o"], blush: true, fx: "note" },
    shocked:     { label: "Shocked",     eyes: "dot", mouth: "O", fx: "alert" },
    kiss:        { label: "Kiss",        eyes: "calm", mouth: "kiss", blush: true, fx: "heart" },
  };

  // Marcel's agent states map onto moods (B-28).
  const STATES = { idle: "neutral", thinking: "thinking", working: "working", needs_you: "surprised", done: "happy" };

  // ------------------------------------------------------------------ accessories and personalities
  const ACCESSORIES = {
    none:    { label: "None" },
    bowtie:  { label: "Bow tie", at: "neck", rows: ["gg..gg", "ggGGgg", "gg..gg"], dx: -3, dy: 0 },
    glasses: { label: "Glasses", glasses: true },
    shades:  { label: "Sunglasses", shades: true },
    crown:   { label: "Crown", at: "top", rows: ["y..y..y", "yy.y.yy", "yyyyyyy", "yhyyyhy", "yyyyyyy"],
               small: ["y.y.y", "yyhyy", "yyyyy"],
               behind: ["y.yy.y", "yyyyyy", "yyhhyy", "yyyyyy", "yyyyyy"], behindSmall: ["y.yy.y", "yyyyyy", "yyhhyy", "yyyyyy"] },
    flower:  { label: "Flower", at: "flower", rows: [".hh.", "hyyh", "hyyh", ".hh."], dx: -2, dy: -2 },
    scarf:   { label: "Scarf", at: "neck", rows: ["vvvvvvvvvv", "VVVVVVVVVV", "......vv..", "......vv.."], dx: -5, dy: 0 },
    cap:     { label: "Cap", at: "top", rows: ["...vvvv...", "..vvvvvv..", ".vvvvvvvv.", "vvvvvvvvvv", "VVVVVVVVVVvv"].map((r) => r.padEnd(12, ".")), wide: 10,
               small: ["..vvvv..", ".vvvvvv.", "vvvvvvvv", "VVVVVVVVvv"], smallWide: 8 },
  };
  const PERSONALITIES = {
    professional: { label: "Professional", accessory: "bowtie",  rest: "neutral",     bob: 1, blink: 1 },
    cheerful:     { label: "Cheerful",     accessory: "flower",  rest: "happy",       bob: 2, blink: 1 },
    curious:      { label: "Curious",      accessory: "glasses", rest: "neutral",     bob: 1, blink: 2 },
    cozy:         { label: "Cozy",         accessory: "scarf",   rest: "calm",        bob: 1, blink: 1 },
    bold:         { label: "Bold",         accessory: "crown",   rest: "proud",       bob: 1, blink: 1 },
    cool:         { label: "Cool",         accessory: "shades",  rest: "proud",       bob: 1, blink: 0 },
    sporty:       { label: "Sporty",       accessory: "cap",     rest: "happy",       bob: 2, blink: 1 },
    shy:          { label: "Shy",          accessory: "none",    rest: "embarrassed", bob: 1, blink: 2, blush: true },
    grumpy:       { label: "Grumpy",       accessory: "none",    rest: "grumpy",      bob: 0, blink: 1 },
  };

  const COLORS = {
    rose: "#f08aa0", coral: "#ff8a6e", tangerine: "#f5953f", honey: "#f2c14e", lime: "#a8d65a", green: "#5fb85a",
    teal: "#3fb8a8", sky: "#6cc3f0", blue: "#5b8def", lavender: "#ab93e8", plum: "#b56aa8", cocoa: "#a87650",
    sand: "#e0c49a", slate: "#8f97a8", snow: "#eeeaf2", ink: "#5a5a78",
  };

  // ------------------------------------------------------------------ name -> avatar (stable)
  function hash(str) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
    // final mix so that similar names spread out
    h ^= h >>> 16; h = Math.imul(h, 0x85ebca6b) >>> 0; h ^= h >>> 13; h = Math.imul(h, 0xc2b2ae35) >>> 0; h ^= h >>> 16;
    return h >>> 0;
  }
  const pick = (list, n) => list[n % list.length];
  /** Same name, same avatar. Anything in `fixed` wins over the pick. */
  function fromName(name, fixed = {}) {
    const h = hash(String(name || "").trim().toLowerCase());
    return {
      name,
      species: fixed.species || pick(Object.keys(SPECIES), h),
      color: fixed.color || ((h >>> 7) % 3 === 0 ? "natural" : pick(Object.keys(COLORS), h >>> 11)),
      personality: fixed.personality || pick(Object.keys(PERSONALITIES), h >>> 19),
      mood: fixed.mood || null,
    };
  }

  // ------------------------------------------------------------------ drawing
  const grid = () => Array.from({ length: N }, () => new Array(N).fill(null));
  const inCanvas = (x, y) => x >= 0 && y >= 0 && x < N && y < N;

  function drawPart(G, part, dy) {
    const fill = new Map();
    part.rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) if (row[x] !== ".") fill.set((part.x + x) + "," + (part.y + y + dy), row[x]);
    });
    const inside = (x, y) => fill.has(x + "," + y);
    const pts = [...fill.keys()].map((k) => k.split(",").map(Number));
    if (part.ol !== "none") {
      for (const [x, y] of pts) {
        for (const [ax, ay] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const nx = x + ax, ny = y + ay;
          if (!inCanvas(nx, ny) || inside(nx, ny)) continue;
          if (part.ol === "outer" && G[ny][nx] !== null && G[ny][nx] !== "S") continue;
          G[ny][nx] = "o";
        }
      }
    }
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const big = Math.max(...xs) - Math.min(...xs) >= 4 && Math.max(...ys) - Math.min(...ys) >= 3;
    for (const [x, y] of pts) {
      if (!inCanvas(x, y)) continue;
      const ch = fill.get(x + "," + y);
      let c = ch;
      if (part.shade && big && SHADE[ch]) {
        if (!inside(x, y + 1) || !inside(x + 1, y)) c = SHADE[ch];          // one shade, bottom and right
        else if (ch === "b" && part.light !== false && (!inside(x, y - 1) || !inside(x - 1, y)) && inside(x + 1, y + 1)) c = "l"; // rim light, top-left
      }
      G[y][x] = c;
    }
  }

  function stamp(G, rows, x0, y0, map) {
    rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        const ch = row[x];
        if (ch === "." || !inCanvas(x0 + x, y0 + y)) continue;
        G[y0 + y][x0 + x] = map ? map(ch) : ch;
      }
    });
  }

  // a free-floating stamp (symbols over the head) gets its own outline so it reads on any background
  function floatStamp(G, rows, x0, y0) {
    const on = new Set();
    rows.forEach((row, y) => { for (let x = 0; x < row.length; x++) if (row[x] !== ".") on.add((x0 + x) + "," + (y0 + y)); });
    for (const key of on) {
      const [x, y] = key.split(",").map(Number);
      for (const [ax, ay] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, -1], [1, -1], [-1, 1]]) {
        const nx = x + ax, ny = y + ay;
        if (inCanvas(nx, ny) && !on.has(nx + "," + ny) && (G[ny][nx] === null || G[ny][nx] === "S")) G[ny][nx] = "o";
      }
    }
    stamp(G, rows, x0, y0);
  }

  function placeEye(G, def, ex, ey, right, map) {
    if (!def) return;
    const w = Math.max(...def.rows.map((r) => r.length));
    let rows = def.rows.map((r) => r.padEnd(w, "."));
    const dx = def.dx || 0;
    let x = ex + dx;
    if (right && !def.same) { rows = mirror(rows); x = ex + 2 - w - dx; }
    stamp(G, rows, x, ey + (def.dy || 0), map);
  }

  /** The laptop on the lap, lid towards us with a heart on it; paws type over the top edge.
   *  `sessions` (1-3) lights that many small lamps on the lid, one per running Claude Code session. */
  function drawLaptop(G, sp, f, dy, sessions) {
    const limb = sp.limb || "b";
    drawPart(G, L(8, 27, blob(16, 2, "Q")), dy);                                // keyboard base
    drawPart(G, L(9, 21, blob(14, 7, "q", [0], [0])), dy);                      // lid
    stamp(G, [".hh.hh.".slice(1, 6), "hhhhh".slice(0, 4), ".hh."].map((r, i) => ["h..h", "hhhh", ".hh."][i]), 14, 23 + dy);
    for (let i = 0; i < 3; i++) stamp(G, [i < sessions && (f + i) % 4 !== 0 ? "x" : "Q"], 19 - i * 2, 26 + dy);
    const typing = Math.floor(f / 2) % 2;                                       // paws take turns
    drawPart(G, L(10, 19 + (typing ? 0 : 1), blob(3, 2, limb)), dy);
    drawPart(G, L(19, 19 + (typing ? 1 : 0), blob(3, 2, limb)), dy);
  }

  /** Build frame f of an avatar: {grid (palette letters), palette}. */
  function frame(opts, f = 0) {
    const sp = SPECIES[opts.species] || SPECIES.giraffe;
    const per = PERSONALITIES[opts.personality] || PERSONALITIES.professional;
    const mood = MOODS[opts.mood || per.rest] || MOODS.neutral;
    const natural = !opts.color || opts.color === "natural";
    const pal = palette(natural ? sp.color : (COLORS[opts.color] || opts.color), natural || sp.keepAccent ? sp.accent : null);
    const face = sp.face;
    const G = grid();

    // breathing: the head sinks a pixel for half the loop; lively personalities hop
    const c8 = f % 8;
    const hop = per.bob === 2 && (c8 === 2 || c8 === 3) ? -1 : 0;
    const hy = (per.bob === 0 ? 0 : c8 >= 4 ? 1 : 0) + hop;
    const dyOf = (g) => (g === "head" ? hy : g === "shadow" ? 0 : hop);

    const acc = ACCESSORIES[opts.accessory || per.accessory] || ACCESSORIES.none;
    // frogs and crocodiles have their eyes on top of the head: the crown sits behind the eye bumps
    const crownBehind = acc === ACCESSORIES.crown && face.crownBehind;
    if (crownBehind) {
      const [tx, ty] = face.crownBehind;
      const rows = (sp.small || []).includes("crownBehind") ? acc.behindSmall : acc.behind;
      drawPart(G, H(tx - rows[0].length / 2, ty - rows.length + 1, rows), hy);
    }
    for (const p of sp.parts()) drawPart(G, p, dyOf(p.group));

    const _acc = ACCESSORIES[opts.accessory || per.accessory] || ACCESSORIES.none;
    if (acc.at === "neck") {
      const [nx, ny] = face.neck;
      drawPart(G, L(nx + acc.dx, ny + acc.dy, acc.rows), opts.species === "giraffe" ? hop : hy);
    }

    if (mood.prop === "laptop") drawLaptop(G, sp, f, hop, Math.max(1, Math.min(3, opts.sessions || 1)));

    // ---- face
    const eyeMap = sp.eyeLight ? (ch) => (ch === "o" ? "w" : ch === "w" ? "o" : ch) : null;
    const blinks = per.blink && ["open", "teary", "look", "small"].includes(mood.eyes);
    const blinking = blinks && (f % 16 === 13 || (per.blink === 2 && f % 16 === 6));
    const eyesL = blinking ? "blink" : mood.eyes;
    const eyesR = blinking ? "blink" : (mood.eyesR || mood.eyes);
    const [lx, ly] = face.eyeL, [rx, ry] = face.eyeR;
    if (mood.blush || per.blush) { stamp(G, ["pp"], face.cheekL[0], face.cheekL[1] + hy); stamp(G, ["pp"], face.cheekR[0], face.cheekR[1] + hy); }
    const shift = mood.eyes === "look" ? 1 : 0;
    const eyeDef = (k) => (sp.reptileEyes && REPTILE[k]) || EYES[k];
    placeEye(G, eyeDef(eyesL), lx + shift, ly + hy, false, eyeMap);
    placeEye(G, eyeDef(eyesR), rx + shift, ry + hy, true, eyeMap);
    if (mood.brows) {
      const b = BROWS[mood.brows], w = Math.max(...b.rows.map((r) => r.length));
      stamp(G, b.rows, lx + b.dx, ly + b.dy + hy, eyeMap);
      stamp(G, mirror(b.rows.map((r) => r.padEnd(w, "."))), rx + 2 - w - b.dx, ry + b.dy + hy, eyeMap);
    }
    const mouthKey = Array.isArray(mood.mouth) ? mood.mouth[Math.floor(f / 2) % mood.mouth.length] : mood.mouth;
    const mouth = sp.wideMouth ? widen(MOUTHS[mouthKey], sp.wideMouth) : MOUTHS[mouthKey];
    const mw = mouth[0].length, mx0 = face.mouth[0] - mw / 2, my0 = face.mouth[1] + hy + (sp.mouthDy || 0);
    stamp(G, mouth, mx0, my0);
    if (sp.teeth) {
      // a crocodile grin: a tooth under every other pixel of the top lip, wherever there is skin below it
      for (let x = 0; x < mw; x += 2) {
        const y = mouth.findIndex((r) => r[x] === "o");
        const px = mx0 + x, py = my0 + y + 1;
        if (y >= 0 && py < N && "bdlr".includes(G[py][px] || "-")) G[py][px] = "w";
      }
    }

    // ---- accessories on the head
    if (acc.glasses) {
      for (const ex of [lx, rx]) stamp(G, ["oooo", "o..o", "o..o", "oooo"], ex - 1, ly - 1 + hy);
      stamp(G, ["o".repeat(rx - lx - 3)], lx + 3, ly + hy);
    } else if (acc.shades) {
      for (const ex of [lx, rx]) stamp(G, ["kkkk", "kkkk", ".kk."], ex - 1, ly + hy);
      stamp(G, ["k".repeat(rx - lx - 3)], lx + 3, ly + hy);
      stamp(G, ["w"], lx, ly + hy); stamp(G, ["w"], rx, ly + hy);
    } else if (acc.at === "top" && !crownBehind) {
      // centred on the head, its bottom row resting on the head's top edge
      const small = (sp.small || []).includes(opts.accessory || per.accessory) && acc.small;
      const rows = small ? acc.small : acc.rows;
      const w = (small ? acc.smallWide : acc.wide) || rows[0].length;
      // frogs and crocodiles wear their crown on one eye bump; everyone else in the middle
      const [tx, ty] = (acc === ACCESSORIES.crown && face.crown) || face.top;
      drawPart(G, H(tx - Math.floor(w / 2), ty - rows.length + 1, rows), hy);
      for (const p of sp.parts()) if (p.front) drawPart(G, p, dyOf(p.group)); // giraffe horns poke through
    } else if (acc.at === "flower") {
      const [tx, ty] = face.flower;
      drawPart(G, H(tx + acc.dx, ty + acc.dy, acc.rows), hy);
    }

    // ---- effects
    if (mood.fx) {
      const fx = FX[mood.fx](f);
      if (fx.tears) {
        const drop = f % 4;
        for (const ex of [lx, rx + 1]) stamp(G, ["t", "t", "T"].slice(0, 1 + Math.min(drop, 2)), ex, ly + 2 + hy + drop);
      } else if (fx.at === "side") {
        floatStamp(G, fx.rows, face.cheekR[0] + 2, face.eyeR[1] - 5 + hy + (fx.dy || 0));
      } else if (fx.at === "crown") {
        floatStamp(G, fx.rows, face.top[0] - 6, face.top[1] - 2 + hy);
      } else {
        const w = Math.max(...fx.rows.map((r) => r.length));
        floatStamp(G, fx.rows, Math.min(N - 1 - w, face.fx[0] - Math.floor(w / 2)), face.fx[1] + 1 + (fx.dy || 0));
      }
    }
    return { grid: G, palette: pal };
  }

  /** The avatar's loop: 16 frames at 140 ms. */
  function loop(opts) {
    return Array.from({ length: 16 }, (_, f) => ({ ...frame(opts, f), ms: 140 }));
  }

  /** Paint a frame into RGBA bytes, `scale` screen pixels per avatar pixel. */
  function toRGBA(fr, scale = 1, bg = null) {
    const W = N * scale, out = new Uint8ClampedArray(W * W * 4), cache = {};
    const rgba = (c) => {
      if (cache[c]) return cache[c];
      let v;
      if (c.startsWith("rgba")) { const p = c.match(/[\d.]+/g).map(Number); v = [p[0], p[1], p[2], Math.round(p[3] * 255)]; }
      else v = [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16), 255];
      return (cache[c] = v);
    };
    const bgc = bg ? rgba(bg) : [0, 0, 0, 0];
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
      const k = fr.grid[y][x];
      let c = k ? rgba(fr.palette[k] || "#ff00ff") : bgc;
      if (bg && c[3] < 255) c = [0, 1, 2].map((i) => Math.round(bgc[i] * (1 - c[3] / 255) + c[i] * (c[3] / 255))).concat(255);
      for (let sy = 0; sy < scale; sy++) for (let sx = 0; sx < scale; sx++) {
        const i = ((y * scale + sy) * W + x * scale + sx) * 4;
        out[i] = c[0]; out[i + 1] = c[1]; out[i + 2] = c[2]; out[i + 3] = c[3];
      }
    }
    return { width: W, height: W, data: out };
  }

  const PixelZoo = { N, SPECIES, MOODS, PERSONALITIES, ACCESSORIES, COLORS, STATES, palette, frame, loop, toRGBA, fromName, hash };
  if (typeof module !== "undefined" && module.exports) module.exports = PixelZoo;
  else root.PixelZoo = PixelZoo;
})(typeof window !== "undefined" ? window : globalThis);
