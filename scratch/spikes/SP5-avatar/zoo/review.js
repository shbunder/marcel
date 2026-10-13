// Renders review sheets of the Pixel Zoo to PNG, so the avatars can be looked at while they are designed.
//   node review.js species|moods|colors|names [frame]  -> out/<sheet>.png
const fs = require("fs"), path = require("path"), zlib = require("zlib");
const Z = require("./zoo.js");

function png(w, h, rgba) {
  const raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) { raw[y * (w * 4 + 1)] = 0; Buffer.from(rgba.buffer, y * w * 4, w * 4).copy(raw, y * (w * 4 + 1) + 1); }
  const chunk = (t, d) => { const l = Buffer.alloc(4); l.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(zlib.crc32(td) >>> 0); return Buffer.concat([l, td, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

function sheet(cells, cols, scale, bg = "#f4eef2") {
  const S = Z.N * scale, pad = 4 * scale / 2;
  const rows = Math.ceil(cells.length / cols);
  const W = cols * (S + pad) + pad, Hh = rows * (S + pad) + pad;
  const out = new Uint8ClampedArray(W * Hh * 4);
  const b = [parseInt(bg.slice(1, 3), 16), parseInt(bg.slice(3, 5), 16), parseInt(bg.slice(5, 7), 16)];
  for (let i = 0; i < W * Hh; i++) { out[i * 4] = b[0]; out[i * 4 + 1] = b[1]; out[i * 4 + 2] = b[2]; out[i * 4 + 3] = 255; }
  cells.forEach((fr, n) => {
    const img = Z.toRGBA(fr, scale, bg);
    const ox = pad + (n % cols) * (S + pad), oy = pad + Math.floor(n / cols) * (S + pad);
    for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
      const s = (y * S + x) * 4, d = ((oy + y) * W + ox + x) * 4;
      out[d] = img.data[s]; out[d + 1] = img.data[s + 1]; out[d + 2] = img.data[s + 2]; out[d + 3] = 255;
    }
  });
  return png(W, Hh, out);
}

const kind = process.argv[2] || "species", f = Number(process.argv[3] || 0);
const sp = Object.keys(Z.SPECIES), moods = Object.keys(Z.MOODS);
let cells = [], cols = 6, scale = 6;
if (kind === "species") { cells = sp.map((s) => Z.frame({ species: s, personality: "grumpy", mood: "neutral" }, f)); cols = 6; scale = 8; }
if (kind === "moods") { const s = process.argv[4] || "giraffe"; cells = moods.map((m) => Z.frame({ species: s, personality: "grumpy", mood: m }, f)); cols = 8; scale = 6; }
if (kind === "allmoods") { for (const s of sp) for (const m of moods) cells.push(Z.frame({ species: s, personality: "grumpy", mood: m }, f)); cols = moods.length; scale = 3; }
if (kind === "colors") { const s = process.argv[4] || "crocodile"; cells = Object.keys(Z.COLORS).map((c) => Z.frame({ species: s, color: c, personality: "grumpy", mood: "neutral" }, f)); cols = 8; scale = 5; }
if (kind === "personas") { const s = process.argv[4] || "giraffe"; cells = Object.keys(Z.PERSONALITIES).map((p) => Z.frame({ species: s, personality: p }, f)); cols = 5; scale = 6; }
if (kind === "names") { const names = ["Marcel", "Shaun", "Emma", "Lucas", "Noor", "Oma", "Lotte", "Finn", "Juna", "Mila", "Bram", "Sofie", "Kobe", "Ines", "Wout", "Elise", "Victor", "Hanna"]; cells = names.map((n) => Z.frame(Z.fromName(n), f)); cols = 6; scale = 5; }
fs.mkdirSync(path.join(__dirname, "out"), { recursive: true });
const file = path.join(__dirname, "out", `${kind}${process.argv[4] ? "-" + process.argv[4] : ""}.png`);
fs.writeFileSync(file, sheet(cells, cols, scale));
console.log(file);
if (kind === "one") {
  const list = (process.argv[4] || "giraffe").split(",");
  fs.writeFileSync(file, sheet(list.map((s) => Z.frame({ species: s, personality: "grumpy", mood: "neutral" }, f)), list.length, 14));
}
if (kind === "acc") {
  const accs = Object.keys(Z.ACCESSORIES).filter((a) => a !== "none");
  const c2 = []; for (const s of sp) for (const a of accs) c2.push(Z.frame({ species: s, personality: "grumpy", mood: "neutral", accessory: a }, f));
  fs.writeFileSync(file, sheet(c2, accs.length, 4));
}
if (kind === "work") { const c2 = []; for (const s of sp) c2.push(Z.frame({ species: s, personality: "professional", mood: "working", sessions: 2 }, f)); fs.writeFileSync(file, sheet(c2, 6, 6)); }
if (kind === "accbig") { const list = (process.argv[4] || "frog").split(","); const c2 = []; for (const s of list) for (const a of ["crown", "cap", "shades", "glasses", "bowtie"]) c2.push(Z.frame({ species: s, personality: "grumpy", mood: "neutral", accessory: a }, f)); fs.writeFileSync(file, sheet(c2, 5, 7)); }
if (kind === "crowns") { const c2 = ["frog", "crocodile"].flatMap((s) => [Z.frame({ species: s, personality: "bold", mood: "neutral" }, f), Z.frame({ species: s, personality: "bold", mood: "happy" }, f + 4)]); fs.writeFileSync(file, sheet(c2, 4, 12)); }
