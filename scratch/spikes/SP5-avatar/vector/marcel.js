// Marcel avatar concepts, drawn as SVG from (state, time).
// The same functions run in Node (to render review frames) and in the browser (live preview).
//   marcel.bean(state, t)  -> SVG string, 200 x 200
// state: "idle" | "thinking" | "working" | "happy";  t: seconds since the state started.

const C = {
  rose: "#d87990", roseDeep: "#b9506e", roseLight: "#eda2b3",
  muzzle: "#fbe4e6", ink: "#141413", white: "#ffffff", blush: "#f08aa2",
  clay: "#d97757", blue: "#6a9bcc", sage: "#788c5d", cloud: "#b0aea5", ivory: "#faf9f5",
  glow: "#f6aa1c",
};

const TAU = Math.PI * 2;
const ease = (x) => 0.5 - 0.5 * Math.cos(Math.PI * Math.min(1, Math.max(0, x)));
// 0..1..0 bump while an event repeating every `period` s is running (length s), else 0
function pulse(t, period, offset, length) {
  const p = (((t + offset) % period) + period) % period;   // stays positive for negative offsets
  return p < length ? Math.sin(Math.PI * p / length) : 0;
}

// ------------------------------------------------------------------ concept A: the giraffe bean
function bean(state = "idle", t = 0) {
  const s = state;
  const breathe = Math.sin(TAU * t / 3.2);
  let hop = 0, squash = 0;
  if (s === "happy") {
    const p = (t % 0.9) / 0.9;                       // hop cycle
    hop = p < 0.6 ? Math.sin(Math.PI * p / 0.6) * 22 : 0;
    squash = p >= 0.6 ? Math.sin(Math.PI * (p - 0.6) / 0.4) * 0.08 : (p < 0.1 ? 0.05 : -0.04 * Math.sin(Math.PI * p / 0.6));
  }
  const sy = 1 + 0.018 * breathe - squash;           // breathing: taller and thinner, then back
  const sx = 1 - 0.012 * breathe + squash * 0.8;

  const blink = Math.max(pulse(t, 3.7, 0.6, 0.16), pulse(t, 11.3, 5.1, 0.16));
  let lookX = 0, lookY = 0;
  if (s === "idle") { lookX = 3 * Math.sin(TAU * t / 7.0) ; lookX += 4 * pulse(t, 8, 4, 1.4); }
  if (s === "thinking") { lookX = 4; lookY = -4; }
  if (s === "working") { lookY = 3; lookX = -2; }

  const hornWobble = (i) => (s === "happy" ? 10 * Math.sin(TAU * t / 0.9 - 0.6 + i) : 4 * Math.sin(TAU * t / 2.4 + i * 1.3))
    + (s === "thinking" ? 6 : 0);
  const earFlick = (i) => 18 * pulse(t, 5.3, i ? 2.6 : 0.3, 0.3);

  // body: one soft bean, narrower at the top (head and neck), round at the bottom
  const body = "M100 34 C 128 34 136 52 136 76 C 136 98 132 108 140 128 C 150 152 140 178 100 178 " +
               "C 60 178 50 152 60 128 C 68 108 64 98 64 76 C 64 52 72 34 100 34 Z";

  const eye = (cx) => {
    const ry = 10 * (1 - 0.92 * blink);
    if (s === "happy") {   // happy eyes: little arches
      return `<path d="M${cx - 8} ${84} Q ${cx} ${73} ${cx + 8} ${84}" stroke="${C.ink}" stroke-width="4.5" fill="none" stroke-linecap="round"/>`;
    }
    const sq = s === "working" ? 0.75 : 1;
    return `<ellipse cx="${cx + lookX * 0.6}" cy="${82 + lookY * 0.6}" rx="7.5" ry="${ry * sq}" fill="${C.ink}"/>` +
      (ry > 4 ? `<circle cx="${cx + lookX * 0.6 - 2.5}" cy="${78 + lookY * 0.6}" r="2.6" fill="${C.white}"/>` : "");
  };

  const horn = (x, i) => {
    const a = hornWobble(i) * (i ? 1 : -1) + (i ? 8 : -8);
    return `<g transform="rotate(${a} ${x} 44)"><rect x="${x - 4}" y="12" width="8" height="34" rx="4" fill="${C.rose}"/>` +
      `<circle cx="${x}" cy="12" r="9" fill="${C.roseDeep}"/></g>`;
  };
  const ear = (side) => {
    const x = side < 0 ? 66 : 134, a = side * (28 + earFlick(side > 0 ? 1 : 0));
    return `<g transform="rotate(${a} ${x} 62)"><ellipse cx="${x + side * 16}" cy="62" rx="17" ry="8" fill="${C.rose}"/>` +
      `<ellipse cx="${x + side * 17}" cy="62" rx="10" ry="4" fill="${C.roseLight}"/></g>`;
  };

  // spots: soft rounded patches; in working three of them glow in turn (Marcel's "working(3)")
  const spots = [[118, 140, 13, 10, -12], [80, 154, 11, 9, 15], [124, 112, 8, 7, 8], [86, 124, 7, 6, -5]];
  const spotSvg = spots.map(([x, y, rx, ry, r], i) => {
    let fill = C.roseDeep, extra = "";
    if (s === "working" && i < 3) {
      const g = pulse(t, 1.8, -i * 0.6, 0.9);
      if (g > 0.02) {
        fill = mix(C.roseDeep, C.glow, g);
        extra = `<ellipse cx="${x}" cy="${y}" rx="${rx + 7 * g}" ry="${ry + 7 * g}" fill="${C.glow}" opacity="${0.25 * g}" transform="rotate(${r} ${x} ${y})"/>`;
      }
    }
    return extra + `<ellipse cx="${x}" cy="${y}" rx="${rx}" ry="${ry}" transform="rotate(${r} ${x} ${y})" fill="${fill}"/>`;
  }).join("");

  let props = "";
  if (s === "thinking") {
    const dots = [0, 1, 2].map((i) => {
      const on = ((t * 1.6) % 4) > i;
      return on ? `<circle cx="${150 + i * 14}" cy="${46 - i * 10}" r="${4 + i * 1.5}" fill="${C.cloud}"/>` : "";
    }).join("");
    props += dots;
  }
  if (s === "happy") {
    props += [[40, 60, C.blue], [160, 52, C.sage], [34, 120, C.clay], [168, 118, C.glow]].map(([x, y, c], i) => {
      const g = pulse(t, 0.9, i * 0.22, 0.45);
      return g > 0.05 ? star(x, y, 7 * g, c) : "";
    }).join("");
  }

  const cx = 100, base = 178;
  const tf = `translate(0 ${-hop}) translate(${cx} ${base}) scale(${sx} ${sy}) translate(${-cx} ${-base})`;
  const shadowW = 46 * (1 - hop / 60);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="200" height="200">` +
    `<ellipse cx="100" cy="186" rx="${shadowW}" ry="5" fill="${C.ink}" opacity="0.08"/>` +
    `<g transform="${tf}">` +
    horn(88, 0) + horn(112, 1) + ear(-1) + ear(1) +
    `<path d="${body}" fill="${C.rose}"/>` +
    // soft light on the left, shade on the right, kept very subtle
    `<path d="${body}" fill="url(#shade)"/>` +
    spotSvg +
    `<ellipse cx="100" cy="104" rx="25" ry="15" fill="${C.muzzle}"/>` +
    `<circle cx="93" cy="101" r="2.2" fill="${C.roseDeep}"/><circle cx="107" cy="101" r="2.2" fill="${C.roseDeep}"/>` +
    (s === "happy" ? `<path d="M92 108 Q 100 117 108 108" stroke="${C.ink}" stroke-width="3" fill="none" stroke-linecap="round"/>`
                   : `<path d="M95 109 Q 100 113 105 109" stroke="${C.ink}" stroke-width="2.6" fill="none" stroke-linecap="round"/>`) +
    `<ellipse cx="74" cy="98" rx="7" ry="4" fill="${C.blush}" opacity="0.7"/><ellipse cx="126" cy="98" rx="7" ry="4" fill="${C.blush}" opacity="0.7"/>` +
    eye(87) + eye(113) +
    `<ellipse cx="86" cy="176" rx="11" ry="6" fill="${C.roseDeep}"/><ellipse cx="114" cy="176" rx="11" ry="6" fill="${C.roseDeep}"/>` +
    `</g>` + props +
    `<defs><linearGradient id="shade" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0.16"/>` +
    `<stop offset="0.55" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#5a1830" stop-opacity="0.14"/></linearGradient></defs>` +
    `</svg>`;
}

// ------------------------------------------------------------------ concept A2: big head, visible neck, little arms
// Flat colours only. Spots are rounded patches like the logo's. viewBox 0 0 200 220 (headroom for hops).
function patch(x, y, w, h, rot, fill) {          // a soft rounded-square giraffe patch
  return `<rect x="${x - w / 2}" y="${y - h / 2}" width="${w}" height="${h}" rx="${Math.min(w, h) * 0.38}" ` +
    `transform="rotate(${rot} ${x} ${y})" fill="${fill}"/>`;
}

function bean2(state = "idle", t = 0, opts = {}) {
  const s = state;
  const breathe = Math.sin(TAU * t / 3.2);
  let hop = 0, squash = 0, tilt = 0;
  if (s === "happy") {
    const p = (t % 0.9) / 0.9;
    hop = p < 0.6 ? Math.sin(Math.PI * p / 0.6) * 20 : 0;
    squash = p >= 0.6 ? Math.sin(Math.PI * (p - 0.6) / 0.4) * 0.07 : -0.03 * Math.sin(Math.PI * p / 0.6);
  }
  if (s === "thinking") tilt = -5 + 1.5 * Math.sin(TAU * t / 2.2);
  if (s === "idle") tilt = 2.5 * pulse(t, 8, 4, 1.6);
  const sy = 1 + 0.016 * breathe - squash, sx = 1 - 0.01 * breathe + squash * 0.8;

  const blink = s === "happy" ? 0 : Math.max(pulse(t, 3.7, 0.6, 0.16), pulse(t, 11.3, 5.1, 0.16));
  let lookX = 0, lookY = 0;
  if (s === "idle") lookX = 2.5 * Math.sin(TAU * t / 7) + 3.5 * pulse(t, 8, 4, 1.6);
  if (s === "thinking") { lookX = 3.5; lookY = -3.5; }
  if (s === "working") { lookY = 3.2; }

  // one continuous silhouette: round head, a neck, a round body
  const body = "M100 40 C 126 40 142 56 142 76 C 142 92 134 102 124 108 C 123 118 124 128 128 136 " +
    "C 146 144 152 160 148 176 C 144 192 126 198 100 198 C 74 198 56 192 52 176 C 48 160 54 144 72 136 " +
    "C 76 128 77 118 76 108 C 66 102 58 92 58 76 C 58 56 74 40 100 40 Z";

  const horn = (x, i) => {
    const w = s === "happy" ? 9 * Math.sin(TAU * t / 0.9 - 0.8 + i) : 3.5 * Math.sin(TAU * t / 2.4 + i * 1.3);
    const a = (i ? 10 : -10) + w * (i ? 1 : -1) + (s === "thinking" ? 5 : 0);
    return `<g transform="rotate(${a} ${x} 48)"><rect x="${x - 4.5}" y="26" width="9" height="24" rx="4.5" fill="${C.rose}"/>` +
      `<circle cx="${x}" cy="25" r="8.5" fill="${C.roseDeep}"/></g>`;
  };
  const ear = (side) => {
    const flick = 22 * pulse(t, 5.3, side > 0 ? 2.6 : 0.3, 0.32);
    const x = side < 0 ? 62 : 138, a = side * (22 + flick) - (s === "happy" ? side * 14 : 0);
    return `<g transform="rotate(${a} ${x} 64)"><ellipse cx="${x + side * 14}" cy="64" rx="15" ry="7.5" fill="${C.rose}"/>` +
      `<ellipse cx="${x + side * 15}" cy="64" rx="9" ry="3.6" fill="${C.roseLight}"/></g>`;
  };
  // arms: little nubs at the shoulders; pose per state
  // rotate(a) turns the arm (which hangs straight down) clockwise; for the left arm (side -1) positive a swings it outward
  const arm = (side) => {
    const sx0 = side < 0 ? 62 : 138, sy0 = 150;
    let a = -side * (12 + 3 * Math.sin(TAU * t / 3.2 + side)), len = 24;
    if (s === "working") { const tap = Math.max(0, Math.sin(TAU * t * 2.6 + (side > 0 ? Math.PI : 0))); a = side * (34 - 8 * tap); len = 26; }
    if (s === "thinking" && side > 0) { a = 160 + 4 * Math.sin(TAU * t / 1.1); len = 42; }    // hoof up to the cheek
    if (s === "happy") a = -side * (138 + 14 * Math.sin(TAU * t / 0.45));                     // both arms up and out, waving
    return `<g transform="rotate(${a.toFixed(1)} ${sx0} ${sy0})"><rect x="${sx0 - 8}" y="${sy0 - 6}" width="16" height="${len + 6}" rx="8" ` +
      `fill="${C.rose}" stroke="${C.roseDeep}" stroke-opacity="0.45" stroke-width="2"/>` +
      `<circle cx="${sx0}" cy="${sy0 + len - 4}" r="7.5" fill="${C.roseDeep}"/></g>`;
  };

  const eye = (cx) => {
    if (s === "happy") return `<path d="M${cx - 7} 78 Q ${cx} 68 ${cx + 7} 78" stroke="${C.ink}" stroke-width="4.2" fill="none" stroke-linecap="round"/>`;
    const ry = 8.5 * (1 - 0.92 * blink) * (s === "working" ? 0.8 : 1);
    return `<ellipse cx="${cx + lookX * 0.7}" cy="${75 + lookY * 0.7}" rx="6.5" ry="${ry}" fill="${C.ink}"/>` +
      (ry > 4 ? `<circle cx="${cx + lookX * 0.7 - 2.2}" cy="${71.5 + lookY * 0.7}" r="2.3" fill="${C.white}"/>` : "");
  };

  const spots = [[112, 118, 13, 15, 8], [84, 160, 20, 16, -10], [118, 172, 17, 14, 12], [104, 148, 9, 8, 0], [90, 128, 8, 9, -6]];
  const spotSvg = spots.map(([x, y, w, h, r], i) => {
    let fill = C.roseDeep, extra = "";
    if (s === "working" && i < 3) {
      const g = pulse(t, 1.8, -i * 0.6, 0.9);
      fill = mix(C.roseDeep, C.glow, g);
      if (g > 0.05) extra = `<circle cx="${x}" cy="${y}" r="${(w + h) / 3 + 6 * g}" fill="${C.glow}" opacity="${0.18 * g}"/>`;
    }
    return extra + patch(x, y, w, h, r, fill);
  }).join("");

  let front = "", props = "";
  if (s === "working") {      // a little laptop in front; we see the back of the lid
    front = `<rect x="64" y="160" width="72" height="44" rx="8" fill="#cfcbc1"/>` +
            `<rect x="64" y="160" width="72" height="44" rx="8" fill="none" stroke="#b0aea5" stroke-width="2"/>` +
            `<g transform="translate(100 182)">${heart(0, 0, 6, C.rose)}</g>` +
            `<rect x="54" y="202" width="92" height="6" rx="3" fill="#a9a59b"/>`;
  }
  if (s === "thinking") {
    props += [0, 1, 2].map((i) => (((t * 1.6) % 4) > i)
      ? `<circle cx="${150 + i * 13}" cy="${48 - i * 11}" r="${3.5 + i * 1.8}" fill="${C.cloud}"/>` : "").join("");
  }
  if (s === "happy") {
    props += [[36, 70, C.blue], [164, 62, C.sage], [30, 140, C.clay], [172, 132, C.glow]].map(([x, y, c], i) => {
      const g = pulse(t, 0.9, i * 0.22, 0.45);
      return g > 0.05 ? star(x, y, 8 * g, c) : "";
    }).join("");
  }
  if (s === "working") {
    const k = Math.floor(t / 0.9), g = (t % 0.9) / 0.9;
    const glyphs = ["&lt;/&gt;", "{ }", "✓", "+1"];
    const col = [C.blue, C.sage, C.clay, C.roseDeep][k % 4];
    props += `<text x="${150 + 6 * Math.sin(k)}" y="${70 - 30 * g}" font-family="Menlo, monospace" font-weight="700" font-size="14" ` +
             `fill="${col}" opacity="${(1 - g).toFixed(2)}">${glyphs[k % 4]}</text>`;
  }

  const base = 198;
  const tf = `translate(0 ${-hop}) rotate(${tilt} 100 ${base}) translate(100 ${base}) scale(${sx} ${sy}) translate(-100 ${-base})`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -10 200 220" width="200" height="220">` +
    `<ellipse cx="100" cy="203" rx="${44 * (1 - hop / 60)}" ry="4.5" fill="${C.ink}" opacity="0.07"/>` +
    `<g transform="${tf}">` + horn(88, 0) + horn(112, 1) + ear(-1) + ear(1) +
    `<path d="${body}" fill="${C.rose}"/>` + spotSvg + (opts.bowtie ? bowtie(100, 132, s, t) : "") +
    `<ellipse cx="100" cy="94" rx="24" ry="13" fill="${C.roseLight}"/>` +
    `<ellipse cx="93" cy="91" rx="2" ry="2.6" fill="${C.roseDeep}"/><ellipse cx="107" cy="91" rx="2" ry="2.6" fill="${C.roseDeep}"/>` +
    (s === "happy" ? `<path d="M92 98 Q 100 107 108 98" stroke="${C.ink}" stroke-width="3" fill="${C.roseDeep}" stroke-linecap="round"/>`
                   : `<path d="M95 99 Q 100 103 105 99" stroke="${C.ink}" stroke-width="2.5" fill="none" stroke-linecap="round"/>`) +
    `<ellipse cx="73" cy="88" rx="7" ry="4" fill="${C.blush}" opacity="0.75"/><ellipse cx="127" cy="88" rx="7" ry="4" fill="${C.blush}" opacity="0.75"/>` +
    eye(87) + eye(113) +
    `<ellipse cx="84" cy="198" rx="12" ry="6" fill="${C.roseDeep}"/><ellipse cx="116" cy="198" rx="12" ry="6" fill="${C.roseDeep}"/>` +
    front + arm(-1) + arm(1) +
    `</g>` + props + `</svg>`;
}

function bowtie(x, y, s, t) {               // Marcel's signature: a little deep-teal bow tie
  const wig = s === "happy" ? 8 * Math.sin(TAU * t / 0.45) : 0;
  return `<g transform="rotate(${wig} ${x} ${y})"><path d="M${x} ${y} L ${x - 15} ${y - 8} Q ${x - 18} ${y} ${x - 15} ${y + 8} Z" fill="#5e807f"/>` +
    `<path d="M${x} ${y} L ${x + 15} ${y - 8} Q ${x + 18} ${y} ${x + 15} ${y + 8} Z" fill="#5e807f"/>` +
    `<rect x="${x - 4.5}" y="${y - 5}" width="9" height="10" rx="3.5" fill="#4a6867"/></g>`;
}

function heart(x, y, r, c) {
  return `<path d="M${x} ${y + r * 0.9} C ${x - r * 1.6} ${y - r * 0.2} ${x - r * 0.9} ${y - r * 1.4} ${x} ${y - r * 0.5} ` +
         `C ${x + r * 0.9} ${y - r * 1.4} ${x + r * 1.6} ${y - r * 0.2} ${x} ${y + r * 0.9} Z" fill="${c}"/>`;
}

function star(x, y, r, c) {
  const pts = [];
  for (let i = 0; i < 8; i++) {
    const a = i * Math.PI / 4, rr = i % 2 ? r * 0.35 : r;
    pts.push(`${(x + rr * Math.sin(a)).toFixed(1)},${(y - rr * Math.cos(a)).toFixed(1)}`);
  }
  return `<polygon points="${pts.join(" ")}" fill="${c}"/>`;
}

function mix(a, b, k) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return "#" + pa.map((v, i) => Math.round(v + (pb[i] - v) * k).toString(16).padStart(2, "0")).join("");
}

const marcel = { bean, bean2, colours: C };
if (typeof module !== "undefined") module.exports = marcel;
if (typeof window !== "undefined") window.marcel = marcel;
