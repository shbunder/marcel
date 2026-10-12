// node review2d.js -> out/review2d.png : the 2D proposals in four states, side by side
const fs = require("fs"), path = require("path"), { execFileSync } = require("child_process");
const a2 = require("./avatars2d.js"), m = require("../vector/marcel.js");
const rows = [["block", a2.block], ["tiles", a2.tiles], ["line", a2.line],
              ["friendly", (s, t) => m.bean2(s, t, { bowtie: true })], ["pro", (s, t) => m.pro(s, t, {})]];
const states = ["idle", "thinking", "working", "happy"], times = (process.argv[2] || "0.3,1.1,1.0,0.5").split(",").map(Number);
const S = 220; let cells = "";
rows.forEach(([name, fn], r) => states.forEach((st, c) => {
  const svg = fn(st, times[c]); const vb = svg.match(/viewBox="([^"]+)"/)[1];
  const inner = svg.replace(/^<svg[^>]*>/, "").replace(/<\/svg>$/, "");
  cells += `<rect x="${c * S}" y="${r * S}" width="${S - 6}" height="${S - 6}" fill="#faf5f8"/>` +
    `<svg x="${c * S + 10}" y="${r * S + 10}" width="${S - 26}" height="${S - 26}" viewBox="${vb}">${inner}</svg>` +
    `<text x="${c * S + 6}" y="${r * S + 14}" font-size="11" font-family="Helvetica" fill="#999">${name} · ${st}</text>`;
}));
const W = 4 * S, H = rows.length * S;
fs.writeFileSync(path.join(__dirname, "out/review2d.svg"), `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}"><rect width="${W}" height="${H}" fill="#fff"/>${cells}</svg>`);
execFileSync("rsvg-convert", ["-o", path.join(__dirname, "out/review2d.png"), path.join(__dirname, "out/review2d.svg")]);
console.log("ok");
