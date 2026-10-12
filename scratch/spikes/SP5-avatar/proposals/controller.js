// Runs every avatar on the page from one clock and one state (idle | thinking | working | happy).
(function () {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let state = "idle", t0 = performance.now();
  const dpr = Math.min(2, window.devicePixelRatio || 1);

  // --- 1: the sprite sheet
  const sheet = new Image(); sheet.src = SPRITE_PNG;
  function spriteFrame(ms) {
    const steps = SPRITE_INFO.loops[state === "working" ? "working" : "idle"];
    const total = steps.reduce((a, s) => a + s[1], 0); let t = ms % total;
    for (const s of steps) { if (t < s[1]) return s[0]; t -= s[1]; }
    return steps[0][0];
  }
  function drawSprite(canvas, t) {
    const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    const g = canvas.getContext("2d"); g.clearRect(0, 0, w, h); g.imageSmoothingEnabled = false;
    if (!sheet.complete) return;
    const fw = SPRITE_INFO.frameWidth, fh = SPRITE_INFO.frameHeight, z = Math.max(1, Math.floor(Math.min(w / fw, h / fh)));
    const x = Math.round((w - fw * z) / 2), y = Math.round((h - fh * z) / 2), i = spriteFrame(t * 1000);
    g.drawImage(sheet, i * fw, 0, fw, fh, x, y, fw * z, fh * z);
    const px = (u, v, c) => { g.fillStyle = c; g.fillRect(x + u * z, y + v * z, z, z); };
    if (state === "thinking") [[38, 8], [41, 5], [44, 2]].forEach(([u, v], k) => { if ((t * 2) % 4 > k) { px(u, v, "#b0aea5"); px(u + 1, v, "#b0aea5"); } });
    if (state === "happy") [[4, 12, "#6a9bcc"], [43, 14, "#788c5d"], [6, 30, "#f2a33a"]].forEach(([u, v, c], k) => {
      if ((Math.floor(t * 4) + k) % 3 === 0) [[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1]].forEach(([a, b]) => px(u + a, v + b, c)); });
  }

  // --- 2 to 6: SVG drawings
  const svgFns = {
    block: (s, t) => avatars2d.block(s, t),
    tiles: (s, t) => avatars2d.tiles(s, t),
    line: (s, t) => avatars2d.line(s, t),
    friendly: (s, t) => marcel.bean2(s, t, { bowtie: true }),
    pro: (s, t, el) => marcel.pro(s, t, { frame: el.dataset.frame || "full" }),
  };
  const fit = (svg) => svg.replace(/ width="\d+" height="\d+"/, ' width="100%" height="100%"');

  // --- 7 to 10: three.js scenes, mirrored into the phone mock-ups
  const scenes = {};
  if (window.THREE) document.querySelectorAll("canvas[data-3d]").forEach((c) => { scenes[c.dataset["3d"]] = window.create3d(c.dataset["3d"], c); });
  const mirrors = [...document.querySelectorAll("[data-mirror]")].map((el) => { const c = document.createElement("canvas"); el.append(c); return { el, c }; });
  function drawMirror({ el, c }) {
    const src = scenes[el.dataset.mirror]; if (!src) return;
    const s = src.canvas, w = Math.round(el.clientWidth * dpr), h = Math.round(el.clientHeight * dpr);
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const g = c.getContext("2d"); g.clearRect(0, 0, w, h);
    let sx = 0, sy = 0, sw = s.width, sh = s.height;
    if (el.dataset.crop === "head") { sw = s.width * 0.42; sh = s.height * 0.42; sx = (s.width - sw) / 2 + s.width * 0.02; sy = s.height * 0.02; }
    const k = Math.min(w / sw, h / sh), dw = sw * k, dh = sh * k;
    g.drawImage(s, sx, sy, sw, sh, (w - dw) / 2, (h - dh) / 2, dw, dh);
  }

  // --- avatars on the page
  const avs = [...document.querySelectorAll("[data-av]")].map((el) => {
    if (el.dataset.av === "sprite") { const c = document.createElement("canvas"); c.style.width = "100%"; c.style.height = "100%"; el.append(c); return { el, c }; }
    return { el };
  });
  const sections = [...document.querySelectorAll(".prop")];
  const visible = new Set(sections);
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting ? visible.add(e.target) : visible.delete(e.target)), { rootMargin: "200px" });
    sections.forEach((s) => io.observe(s));
  }
  let last = 0;
  function frame(now) {
    if (!reduce) requestAnimationFrame(frame);
    if (now - last < 32 && !reduce) return;          // about 30 frames a second is plenty
    last = now;
    const t = reduce ? 0.4 : (now - t0) / 1000;
    for (const sec of visible) {
      sec.querySelectorAll("canvas[data-3d]").forEach((c) => scenes[c.dataset["3d"]] && scenes[c.dataset["3d"]].render(state, t));
    }
    avs.forEach((a) => {
      if (!visible.has(a.el.closest(".prop"))) return;
      if (a.c) drawSprite(a.c, t); else a.el.innerHTML = fit(svgFns[a.el.dataset.av](state, t, a.el));
    });
    mirrors.forEach((m) => { if (visible.has(m.el.closest(".prop"))) drawMirror(m); });
  }
  // --- 7 to 10: Blender renders, one animated WebP per style and state
  const r3d = [...document.querySelectorAll("[data-r3d]")].map((el) => {
    const img = el.tagName === "IMG" ? el : el.appendChild(document.createElement("img"));
    img.alt = img.alt || ""; img.decoding = "async"; return img;
  });
  const r3dEls = [...document.querySelectorAll("[data-r3d]")];
  function setR3d() { r3dEls.forEach((el, i) => { r3d[i].src = `${el.dataset.r3d}-${state}.webp`; }); }
  setR3d();

  const buttons = [...document.querySelectorAll(".statebar button")];
  buttons.forEach((b) => b.addEventListener("click", () => {
    state = b.dataset.state; t0 = performance.now(); setR3d();
    buttons.forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    if (reduce) frame(performance.now());
  }));
  requestAnimationFrame(frame);
})();
