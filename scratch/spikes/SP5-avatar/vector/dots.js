// Concept B: the same giraffe seen through a dot-matrix display (soft round dots on a grid).
// node dots.js [state,...] [times] -> out/dots-review.png
// Renders the vector giraffe small with rsvg-convert, reads the pixels, and draws one dot per pixel,
// snapped to the palette so the dots stay crisp and few-coloured.
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const { execFileSync } = require("child_process");
const marcel = require("./marcel.js");

const GW = 40, GH = 44;              // dot grid (the vector is 200 x 220)
const PITCH = 10, R = 4.1;           // spacing and dot radius in the output drawing
const out = path.join(__dirname, "out");

function decodePNG(buf) {            // just enough PNG: 8-bit RGBA, not interlaced (what rsvg-convert writes)
  let pos = 8, w = 0, h = 0;
  const idat = [];
  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos), type = buf.toString("ascii", pos + 4, pos + 8);
    const data = buf.subarray(pos + 8, pos + 8 + len);
    if (type === "IHDR") { w = data.readUInt32BE(0); h = data.readUInt32BE(4); }
    if (type === "IDAT") idat.push(data);
    pos += 12 + len;
  }
  const raw = zlib.inflateSync(Buffer.concat(idat)), bpp = 4, stride = w * bpp;
  const px = Buffer.alloc(h * stride);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (stride + 1)], line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const a = x >= bpp ? px[y * stride + x - bpp] : 0, b = y ? px[(y - 1) * stride + x] : 0;
      const c = x >= bpp && y ? px[(y - 1) * stride + x - bpp] : 0;
      let v = line[x];
      if (f === 1) v += a; else if (f === 2) v += b; else if (f === 3) v += (a + b) >> 1;
      else if (f === 4) { const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c); v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
      px[y * stride + x] = v & 255;
    }
  }
  return { w, h, px };
}

const PALETTE = Object.values(marcel.colours).filter((c) => c !== marcel.colours.ivory)
  .concat(["#cfcbc1", "#a9a59b", "#5e807f", "#4a6867"]);
const PAL = PALETTE.map((h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)));
function nearest(r, g, b) {
  let best = 0, bd = 1e9;
  PAL.forEach((p, i) => { const d = (p[0] - r) ** 2 + (p[1] - g) ** 2 + (p[2] - b) ** 2; if (d < bd) { bd = d; best = i; } });
  return PALETTE[best];
}

// one dot-matrix frame as SVG markup (no <svg> wrapper)
function dotFrame(state, t, opts) {
  const tmp = path.join(out, "_tmp.svg"), png = path.join(out, "_tmp.png");
  fs.writeFileSync(tmp, marcel.bean2(state, t, opts));
  execFileSync("rsvg-convert", ["-w", String(GW), "-h", String(GH), "-o", png, tmp]);
  const { w, px } = decodePNG(fs.readFileSync(png));
  let s = "";
  for (let y = 0; y < GH; y++) for (let x = 0; x < GW; x++) {
    const i = (y * w + x) * 4, a = px[i + 3];
    if (a < 110) continue;
    const col = nearest(px[i], px[i + 1], px[i + 2]);
    s += `<circle cx="${x * PITCH + PITCH / 2}" cy="${y * PITCH + PITCH / 2}" r="${R}" fill="${col}"/>`;
  }
  return s;
}

if (require.main === module) {
  fs.mkdirSync(out, { recursive: true });
  const states = (process.argv[2] || "idle,thinking,working,happy").split(",");
  const times = (process.argv[3] || "0,0.35,0.7,1.2,4.6").split(",").map(Number);
  const cw = GW * PITCH, ch = GH * PITCH;
  let cells = "";
  states.forEach((s, r) => times.forEach((t, c) => {
    cells += `<g transform="translate(${c * (cw + 20)} ${r * (ch + 20)})"><rect width="${cw}" height="${ch}" fill="#141413"/>` +
      `<g>${dotFrame(s, t, { bowtie: true })}</g></g>`;
  }));
  const W = times.length * (cw + 20), H = states.length * (ch + 20);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}"><rect width="${W}" height="${H}" fill="#faf9f5"/>${cells}</svg>`;
  fs.writeFileSync(path.join(out, "dots-review.svg"), svg);
  execFileSync("rsvg-convert", ["-w", "2000", "-o", path.join(out, "dots-review.png"), path.join(out, "dots-review.svg")]);
  console.log("wrote", path.join(out, "dots-review.png"));
}
module.exports = { dotFrame };
