// Ten Marcel proposals: the 2D ones (pixel art, Clawd-style blocks, rounded tiles, one-line drawing).
// Each returns an SVG string for (state, t). state: idle | thinking | working | happy; t in seconds.
// The flat vector ones (friendly, professional) come from ../vector/marcel.js.

const K2 = {
  rose: "#d87990", roseMid: "#cc6a80", deep: "#9c3e5a", spot: "#b9506e", muzzle: "#f3c3cd", ink: "#1d1a1c",
  teal: "#3f6b6a", tealDark: "#2f5251", clay: "#d97757", blue: "#6a9bcc", sage: "#788c5d", cloud: "#b0aea5", amber: "#f2a33a",
};
const TAU2 = Math.PI * 2;
function bump2(t, period, offset, length) {
  const p = (((t + offset) % period) + period) % period;
  return p < length ? Math.sin(Math.PI * p / length) : 0;
}
const step = (t, fps) => Math.floor(t * fps) / fps;   // pixel styles move on a fixed beat, like sprites

// ------------------------------------------------------------------ 2. Clawd-style blocks
// Flat, one rose, holes for eyes and nostrils, ear stubs where Clawd has claws. Square pixels this time,
// and a bust, so the giraffe has enough pixels to be a giraffe.
const BLOCK = [
  ".....DD......DD.....",
  ".....DD......DD.....",
  "......B......B......",
  "....BBBBBBBBBBBB....",
  "...BBBBBBBBBBBBBB...",
  "BB.BBBBBBBBBBBBBB.BB",
  "...BBB..BBBB..BBB...",
  "...BBB..BBBB..BBB...",
  "...BBBBBBBBBBBBBB...",
  "....BBBBBBBBBBBB....",
  "....BB.BBBBBB.BB....",
  ".....BBBBBBBBBB.....",
  ".......BBBBBB.......",
  ".......BDDBBB.......",
  ".......BBBBBB.......",
  ".......BBBDDB.......",
  ".......BBBBBB.......",
  "....BBBBBBBBBBBB....",
  "..BBBBBBBBBBBBBBBB..",
  ".BBBBBDDBBBBBBBBBBB.",
  ".BBBBBBDBBBBBBDDBBB.",
  ".BBBBBBBBBBBBBBBBBB.",
];
function block(state = "idle", t = 0, opts = {}) {
  t = step(t, 8);
  const cols = { B: K2.rose, D: K2.deep };
  const blink = bump2(t, 3.6, 0.5, 0.25) > 0.1;
  const hop = state === "happy" && (t * 4) % 2 < 1 ? -1 : 0;      // the whole giraffe hops
  const bob = state === "happy" ? 0 : (Math.sin(TAU2 * t / 2.4) > 0.55 ? 1 : 0);   // the head dips over the neck
  const look = state === "thinking" ? 1 : (state === "working" ? -1 : (bump2(t, 7, 3, 1.4) > 0.1 ? 1 : 0));
  const earUp = bump2(t, 5, 1, 0.4) > 0.1;
  const W = 28, H = 27, ox = 3, oy = 3 + hop;
  const map = new Map();                       // "x,y" -> colour; holes are simply missing pixels
  const put = (x, y, c) => map.set(`${x},${y}`, c);
  BLOCK.forEach((row, y) => row.split("").forEach((ch, x) => {
    if (ch === ".") return;
    const dy = y <= 11 ? bob : 0;
    if (y === 5 && (x <= 1 || x >= 18) && earUp) put(ox + x, oy + y - 1 + dy, cols.B);
    else put(ox + x, oy + y + dy, cols[ch]);
  }));
  // the eye holes are part of the drawing; fill them, then cut them again where Marcel is looking
  [6, 7].forEach((y) => [6, 7, 12, 13].forEach((x) => put(ox + x, oy + y + bob, cols.B)));
  if (!blink) [6, 7].forEach((y) => [6, 7, 12, 13].forEach((x) => map.delete(`${ox + x + look},${oy + y + bob}`)));
  // props, Clawd-like: grey thought pixels, coloured code lines being typed, sparkles
  if (state === "thinking") [[23, 4], [25, 2], [26, 0]].forEach(([x, y], i) => { if ((t * 2) % 4 > i) put(x, y, K2.cloud); });
  if (state === "working") {
    const k = Math.floor(t * 4);
    [K2.blue, K2.sage, K2.clay].forEach((c, i) => {
      const len = 1 + ((k + i * 3) % 5);
      for (let j = 0; j < len; j++) put(22 + j, 9 + i * 2, c);
    });
  }
  if (state === "happy") [[1, 2, K2.blue], [25, 4, K2.sage], [1, 14, K2.clay], [25, 13, K2.amber]].forEach(([x, y, c], i) => {
    if ((Math.floor(t * 4) + i) % 3 === 0) [[0, 0], [-1, 0], [1, 0], [0, -1], [0, 1]].forEach(([dx, dy]) => put(x + dx, y + dy, c));
  });
  let s = "";
  map.forEach((c, k) => { const [x, y] = k.split(",").map(Number); s += `<rect x="${x}" y="${y}" width="1.02" height="1.02" fill="${c}"/>`; });
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="200" height="${Math.round(200 * H / W)}" shape-rendering="crispEdges">${s}</svg>`;
}

// ------------------------------------------------------------------ 3. Rounded tiles (pixel homage, modern)
// A hand-drawn 22 x 26 grid, each pixel a soft rounded tile with a small gap. Two tones of rose, a light muzzle,
// eyes with a highlight, a teal bow tie. Tiles light up and settle like a display.
const TILES = [
  "......DD......DD......",
  "......DD......DD......",
  ".......B......B.......",
  ".....BBBBBBBBBBBB.....",
  "....BBBBBBBBBBBBBB....",
  "BBB.BBBBBBBBBBBBBB.BBB",
  ".BB.BBweBBBBBBweBB.BB.",
  "....BBeeBBBBBBeeBB....",
  "....BBBBBBBBBBBBBB....",
  "....BBMMMMMMMMMMBB....",
  ".....MMDMMMMMMDMM.....",
  ".....MMMMMDDMMMMM.....",
  "......MMMMMMMMMM......",
  "........BBBBBB........",
  "........BDDBBB........",
  "........BBBBBB........",
  "........BBBBDB........",
  "......TTTTttTTTT......",
  "......BBBBBBBBBB......",
  "....BBBBBBBBBBBBBB....",
  "...BBBDDBBBBBBBBBBB...",
  "...BBDDDBBBBBBDDBBB...",
  "...BBBBBBBBBBBDDBBB...",
  "...BBBBBBBBBBBBBBBB...",
  "....BBBBBBBBBBBBBB....",
  "....DDD........DDD....",
];
function tiles(state = "idle", t = 0, opts = {}) {
  const cols = { B: K2.rose, D: K2.deep, M: K2.muzzle, e: K2.ink, w: "#ffffff", T: K2.teal, t: K2.tealDark };
  const blink = bump2(t, 3.8, 0.6, 0.18) > 0.15;
  const W = 26, H = 30, ox = 2, oy = 2;
  const bob = state === "happy" ? -Math.abs(Math.sin(TAU2 * t / 0.9)) * 1.2 : 0.18 * Math.sin(TAU2 * t / 3);
  let s = "";
  TILES.forEach((row, y) => row.split("").forEach((ch, x) => {
    if (ch === ".") return;
    let c = cols[ch];
    if (blink && (ch === "e" || ch === "w")) c = K2.rose;
    // working: three spots glow amber in a slow chase; thinking: a soft shimmer runs down the neck
    if (state === "working" && ch === "D" && y >= 13 && y <= 22) {
      const g = bump2(t, 2.4, -((x + y) % 3) * 0.8, 1.2);
      c = mixHex(K2.deep, K2.amber, 0.85 * g);
    }
    let scale = 1;
    if (state === "thinking") scale = 1 - 0.18 * bump2(t - y * 0.05, 1.6, 0, 0.5);
    if (state === "happy") scale = 1 - 0.15 * bump2(t - (x + y) * 0.03, 0.9, 0, 0.4);
    const head = y <= 12, dy = head ? bob : bob * 0.6;
    const size = 0.86 * scale, off = (1 - size) / 2;
    s += `<rect x="${(ox + x + off).toFixed(3)}" y="${(oy + y + dy + off).toFixed(3)}" width="${size.toFixed(3)}" height="${size.toFixed(3)}" rx="${(size * 0.32).toFixed(3)}" fill="${c}"/>`;
  }));
  if (state === "thinking") [0, 1, 2].forEach((i) => {
    const k = 0.5 + 0.5 * Math.sin(TAU2 * (t * 0.9 - i * 0.18));
    s += `<rect x="${19.4 + i * 1.6}" y="${1.6 - k * 0.4}" width="1" height="1" rx="0.32" fill="${K2.ink}" opacity="${(0.3 + 0.6 * k).toFixed(2)}"/>`;
  });
  if (state === "happy") s += `<g transform="translate(22.5 3.5)"><circle r="2" fill="${K2.teal}"/><path d="M-0.9 0 L -0.25 0.7 L 1 -0.7" stroke="#fff" stroke-width="0.45" fill="none" stroke-linecap="round" stroke-linejoin="round"/></g>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="200" height="${Math.round(200 * H / W)}">${s}</svg>`;
}

// ------------------------------------------------------------------ 4. One line (monoline, editorial)
function line(state = "idle", t = 0, opts = {}) {
  const c = opts.colour || K2.deep, sw = 3.2;
  const blink = Math.max(bump2(t, 4.2, 0.8, 0.15), bump2(t, 12.4, 6, 0.15));
  let tilt = 2 * Math.sin(TAU2 * t / 9);
  if (state === "thinking") tilt = -7;
  if (state === "working") tilt = 4;
  if (state === "happy") tilt = 3 * bump2(t, 1.6, 0, 0.8);
  const earF = (side) => 14 * bump2(t, 6, side > 0 ? 3.3 : 0.8, 0.3);
  const eye = (x) => state === "happy"
    ? `<path d="M${x - 4} 75 Q ${x} 70 ${x + 4} 75" stroke="${c}" stroke-width="${sw}" fill="none" stroke-linecap="round"/>`
    : `<ellipse cx="${x + (state === "thinking" ? 1.5 : 0)}" cy="${73 + (state === "working" ? 2 : state === "thinking" ? -2 : 0)}" rx="3" ry="${(4.2 * (1 - 0.9 * blink)).toFixed(2)}" fill="${c}"/>`;
  const head = "M100 40 C 117 40 130 52 130 68 C 130 80 126 86 123 92 C 125 98 124 108 116 112 " +
    "C 110 116 90 116 84 112 C 76 108 75 98 77 92 C 74 86 70 80 70 68 C 70 52 83 40 100 40 Z";
  const ear = (side) => {
    const x = side < 0 ? 72 : 128;
    return `<g transform="rotate(${side * (18 + earF(side))} ${x} 60)"><path d="M${x} 60 Q ${x + side * 12} 52 ${x + side * 24} 60 Q ${x + side * 12} 68 ${x} 60 Z" stroke="${c}" stroke-width="${sw}" fill="none" stroke-linejoin="round"/></g>`;
  };
  const horn = (x, i) => `<g transform="rotate(${(i ? 7 : -7) + 1.5 * Math.sin(TAU2 * t / 3 + i)} ${x} 44)"><path d="M${x} 44 L ${x} 32" stroke="${c}" stroke-width="${sw}" stroke-linecap="round"/><circle cx="${x}" cy="27" r="5" fill="none" stroke="${c}" stroke-width="${sw}"/></g>`;
  let extra = "";
  if (state === "thinking") [0, 1, 2].forEach((i) => {
    const k = 0.5 + 0.5 * Math.sin(TAU2 * (t * 0.9 - i * 0.18));
    extra += `<circle cx="${146 + i * 10}" cy="${36 - k * 2}" r="2.6" fill="${c}" opacity="${(0.3 + 0.7 * k).toFixed(2)}"/>`;
  });
  if (state === "working") {
    const a = (t * 0.6) % 1;
    extra += `<circle cx="100" cy="112" r="78" fill="none" stroke="${K2.cloud}" stroke-width="2" opacity="0.5"/>` +
      `<circle cx="100" cy="112" r="78" fill="none" stroke="${K2.teal}" stroke-width="3" stroke-linecap="round" ` +
      `stroke-dasharray="${(490 * 0.22).toFixed(1)} 490" transform="rotate(${(a * 360 - 90).toFixed(1)} 100 112)"/>`;
  }
  if (state === "happy") {
    const d = Math.min(1, (t % 2.4) / 0.5);
    extra += `<path d="M138 46 L 146 54 L 162 36" stroke="${K2.teal}" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="40" stroke-dashoffset="${(40 * (1 - d)).toFixed(1)}"/>`;
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="20 14 160 176" width="200" height="220">` +
    `<g transform="rotate(${tilt.toFixed(2)} 100 112)">${horn(91, 0)}${horn(109, 1)}${ear(-1)}${ear(1)}` +
    `<path d="${head}" stroke="${c}" stroke-width="${sw}" fill="none" stroke-linejoin="round"/>` +
    `<path d="M80 95 Q 100 87 120 95" stroke="${c}" stroke-width="${sw * 0.8}" fill="none" stroke-linecap="round"/>` +
    `<circle cx="94" cy="100" r="1.8" fill="${c}"/><circle cx="106" cy="100" r="1.8" fill="${c}"/>` +
    `<path d="M96 106 Q 100 109 104 106" stroke="${c}" stroke-width="2.4" fill="none" stroke-linecap="round"/>` +
    eye(89) + eye(111) + `</g>` +
    `<path d="M88 114 L 84 160 M112 114 L 116 160" stroke="${c}" stroke-width="${sw}" stroke-linecap="round"/>` +
    `<rect x="95" y="124" width="9" height="10" rx="3" fill="none" stroke="${c}" stroke-width="2.4"/>` +
    `<rect x="101" y="140" width="8" height="8" rx="3" fill="none" stroke="${c}" stroke-width="2.4"/>` +
    `<path d="M100 160 L 88 154 L 88 166 Z M100 160 L 112 154 L 112 166 Z" stroke="${K2.teal}" stroke-width="2.6" fill="none" stroke-linejoin="round"/>` +
    `<path d="M48 188 C 60 168 76 162 84 160 M152 188 C 140 168 124 162 116 160" stroke="${c}" stroke-width="${sw}" fill="none" stroke-linecap="round"/>` +
    extra + `</svg>`;
}

function mixHex(a, b, k) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16)), pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return "#" + pa.map((v, i) => Math.round(v + (pb[i] - v) * Math.max(0, Math.min(1, k))).toString(16).padStart(2, "0")).join("");
}

const avatars2d = { block, tiles, line };
if (typeof module !== "undefined") module.exports = avatars2d;
if (typeof window !== "undefined") window.avatars2d = avatars2d;
