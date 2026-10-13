// Exports the fixtures the Python port is checked against: 50 (look, mood, frame) cases drawn by zoo.js,
// plus the looks zoo.js picks for some names. Run from this folder:  node tests/fixtures/export.js [path/to/zoo.js]
// It writes tests/fixtures/zoo-js.json. Commit that file; re-run only when zoo.js changes on purpose.
const fs = require("fs"), path = require("path"), crypto = require("crypto");
const src = path.resolve(process.argv[2] || path.join(__dirname, "../../../../../scratch/spikes/SP5-avatar/zoo/zoo.js"));
const Z = require(src);

const species = Object.keys(Z.SPECIES), moods = Object.keys(Z.MOODS), pers = Object.keys(Z.PERSONALITIES);
const colors = Object.keys(Z.COLORS), accs = Object.keys(Z.ACCESSORIES);
const looks = [];
for (let i = 0; i < 44; i++) {
  looks.push({
    look: {
      species: species[i % species.length],
      color: i % 10 === 9 ? "#7a5cff" : i % 4 === 0 ? "natural" : colors[(i * 3) % colors.length],
      personality: pers[(i * 5) % pers.length],
      accessory: i % 3 === 0 ? accs[i % accs.length] : null,
      sessions: 1 + (i % 3),
      mood: moods[(i * 7) % moods.length],
    },
    frame: (i * 5) % 16,
  });
}
// the places that went wrong while designing: crowns behind eye bumps, horns through a cap, the laptop
looks.push({ look: { species: "frog", color: "natural", personality: "bold", mood: "happy" }, frame: 4 });
looks.push({ look: { species: "crocodile", color: "natural", personality: "bold", mood: "neutral" }, frame: 0 });
looks.push({ look: { species: "giraffe", color: "natural", personality: "sporty", mood: "neutral" }, frame: 2 });
looks.push({ look: { species: "panda", color: "lavender", personality: "cool", mood: "angry" }, frame: 3 });
looks.push({ look: { species: "crocodile", color: "teal", personality: "professional", mood: "working", sessions: 3 }, frame: 6 });
looks.push({ look: { species: "giraffe", color: "natural", personality: "curious", mood: "neutral" }, frame: 13 });

const fixtures = looks.map(({ look, frame }) => {
  const fr = Z.frame(look, frame);
  const rgba = Buffer.from(Z.toRGBA(fr, 1).data.buffer);
  return {
    look, frame,
    grid: fr.grid.map((row) => row.map((c) => c || ".").join("")),
    palette: fr.palette,
    rgba_sha256: crypto.createHash("sha256").update(rgba).digest("hex"),
  };
});
const names = ["Marcel", "Shaun", "Emma", "  Noor ", "OMA", "Zoë", "李雷", "😀 Bob", "", "Victor", "\ud800x"].map((name) => {
  const p = Z.fromName(name);
  return { name, species: p.species, color: p.color, personality: p.personality, hash: Z.hash(name.trim().toLowerCase()) };
});
const out = path.join(__dirname, "zoo-js.json");
fs.writeFileSync(out, JSON.stringify({ source: "scratch/spikes/SP5-avatar/zoo/zoo.js", fixtures, names }, null, 1) + "\n");
console.log(`Wrote ${fixtures.length} fixtures and ${names.length} names to ${path.relative(process.cwd(), out)}`);
