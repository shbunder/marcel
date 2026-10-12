// Renders review sheets: node review.js <concept> -> out/<concept>-review.png
// Each row is a state; columns are moments in time. Uses rsvg-convert to rasterise.
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const marcel = require("./marcel.js");

const [concept, variant] = (process.argv[2] || "bean").split(":");
const opts = variant === "bowtie" ? { bowtie: true } : (variant === "bust" ? { frame: "bust" } : {});
const states = (process.argv[3] || "idle,thinking,working,happy").split(",");
const times = (process.argv[4] || "0,0.65,1.2,2.0,4.3").split(",").map(Number);
const size = 240, scale = 2;
const out = path.join(__dirname, "out");
fs.mkdirSync(out, { recursive: true });

let cells = "";
states.forEach((s, r) => times.forEach((t, c) => {
  const svg = marcel[concept](s, t, opts).replace(/^<svg[^>]*>/, "").replace(/<\/svg>$/, "");
  cells += `<g transform="translate(${c * size} ${r * size})"><rect width="${size}" height="${size}" fill="${marcel.colours.ivory}"/><g transform="translate(10 10)">${svg}</g>` +
    `<text x="6" y="16" font-family="Helvetica" font-size="11" fill="#888">${s} ${t}s</text></g>`;
}));
const W = times.length * size, H = states.length * size;
const sheet = `<svg xmlns="http://www.w3.org/2000/svg" width="${W * scale}" height="${H * scale}" viewBox="0 0 ${W} ${H}">${cells}</svg>`;
const svgPath = path.join(out, `${concept}${variant ? "-" + variant : ""}-review.svg`);
fs.writeFileSync(svgPath, sheet);
execFileSync("rsvg-convert", ["-o", path.join(out, `${concept}${variant ? "-" + variant : ""}-review.png`), svgPath]);
console.log("wrote", path.join(out, `${concept}${variant ? "-" + variant : ""}-review.png`));
