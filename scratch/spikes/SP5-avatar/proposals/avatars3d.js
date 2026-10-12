// Ten Marcel proposals: the 3D ones, built with three.js (r128, loaded from cdnjs as the global THREE).
//   create3d(kind, canvas) -> { render(state, t) }   kind: "voxel" | "toon" | "clay" | "plush"
// One rig (body, neck, head, ears, horns, eyes, arms, spots, bow tie, laptop) dressed four ways.
(function () {
  const COL = { rose: 0xd87990, deep: 0x9c3e5a, muzzle: 0xf3c3cd, ink: 0x1d1a1c, white: 0xffffff,
                teal: 0x3f6b6a, tealDark: 0x2f5251, laptop: 0x2b2a28, laptopLid: 0x3a3936, amber: 0xf2a33a };
  const TAU = Math.PI * 2;
  const bump = (t, period, offset, length) => { const p = (((t + offset) % period) + period) % period; return p < length ? Math.sin(Math.PI * p / length) : 0; };

  // ---------------------------------------------------------------- shared motion
  function motion(state, t) {
    const m = { breathe: Math.sin(TAU * t / 3.6), blink: Math.max(bump(t, 3.9, 0.7, 0.16), bump(t, 11.7, 5.9, 0.16)),
                tilt: 0, nod: 0, yaw: 0, look: [0, 0], earL: bump(t, 5.6, 0.6, 0.32), earR: bump(t, 5.6, 3.1, 0.32),
                hop: 0, armL: 0, armR: 0, laptop: false, glow: [0, 0, 0], happyEyes: false };
    if (state === "idle") { m.tilt = 0.05 * Math.sin(TAU * t / 8); m.yaw = 0.22 * bump(t, 8, 4, 1.8); m.look = [0.6 * bump(t, 8, 4, 1.8), 0]; }
    if (state === "thinking") { m.tilt = -0.16 + 0.03 * Math.sin(TAU * t / 3); m.look = [0.5, 0.45]; m.yaw = 0.18; }
    if (state === "working") {
      m.laptop = true; m.nod = 0.16; m.look = [0, -0.5];
      m.armL = Math.max(0, Math.sin(TAU * t * 2.3)); m.armR = Math.max(0, Math.sin(TAU * t * 2.3 + Math.PI));
      m.glow = [0, 1, 2].map((i) => bump(t, 2.4, -i * 0.8, 1.2));
    }
    if (state === "happy") { const p = (t % 0.9) / 0.9; m.hop = p < 0.55 ? Math.sin(Math.PI * p / 0.55) : 0; m.happyEyes = true; m.earL = m.earR = 0.8 * m.hop; m.armR = -1; m.nod = -0.08; }
    return m;
  }

  // ---------------------------------------------------------------- materials per style
  function gradientMap() {
    const tex = new THREE.DataTexture(new Uint8Array([90, 175, 255]), 3, 1, THREE.LuminanceFormat);
    tex.minFilter = tex.magFilter = THREE.NearestFilter; tex.needsUpdate = true; return tex;
  }
  function furTexture() {
    const c = document.createElement("canvas"); c.width = c.height = 256;
    const g = c.getContext("2d"); g.fillStyle = "#000"; g.fillRect(0, 0, 256, 256);
    for (let i = 0; i < 26000; i++) { const v = 140 + Math.random() * 115; g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(Math.random() * 256, Math.random() * 256, 1, 1); }
    const tex = new THREE.CanvasTexture(c); tex.wrapS = tex.wrapT = THREE.RepeatWrapping; tex.repeat.set(9, 9); return tex;
  }
  function style(kind) {
    if (kind === "toon") {
      const gm = gradientMap();
      return { mat: (c) => new THREE.MeshToonMaterial({ color: c, gradientMap: gm }), outline: 0x3a1526, fur: false };
    }
    if (kind === "clay") {
      return { mat: (c) => new THREE.MeshPhysicalMaterial({ color: c, roughness: 0.38, metalness: 0, clearcoat: 0.55, clearcoatRoughness: 0.25 }), outline: null, fur: false };
    }
    if (kind === "plush") {
      return { mat: (c) => new THREE.MeshStandardMaterial({ color: c, roughness: 1, metalness: 0 }), outline: null, fur: true, furTex: furTexture() };
    }
    return { mat: (c) => new THREE.MeshLambertMaterial({ color: c }), outline: null, fur: false };
  }

  // ---------------------------------------------------------------- the rig (smooth styles)
  function smoothRig(kind) {
    const S = style(kind), root = new THREE.Group(), outlines = [];
    const sphere = new THREE.SphereGeometry(1, 48, 32);
    function blob(parent, colour, pos, scale, opts = {}) {
      const mesh = new THREE.Mesh(opts.geo || sphere, S.mat(colour));
      mesh.position.set(...pos); mesh.scale.set(...scale);
      if (opts.rot) mesh.rotation.set(...opts.rot);
      parent.add(mesh);
      if (S.outline && !opts.noOutline) {
        const o = new THREE.Mesh(opts.geo || sphere, new THREE.MeshBasicMaterial({ color: S.outline, side: THREE.BackSide }));
        o.scale.setScalar(1 + (opts.outline || 0.045) / Math.min(...scale)); mesh.add(o); outlines.push(o);
      }
      if (S.fur && opts.fur !== false) {
        for (let i = 1; i <= 10; i++) {
          const shell = new THREE.Mesh(opts.geo || sphere, new THREE.MeshStandardMaterial({
            color: new THREE.Color(colour).lerp(new THREE.Color(0xffffff), 0.035 * i), roughness: 1, alphaMap: S.furTex,
            transparent: true, depthWrite: false, opacity: 1 - i / 12 }));
          const k = 1 + (0.0055 * i) / Math.min(...scale); shell.scale.setScalar(k); mesh.add(shell);
        }
      }
      return mesh;
    }
    const big = kind === "toon" ? 1.15 : 1;     // the cartoon style gets a bigger head
    // body, feet, neck
    const bodyG = new THREE.Group(); root.add(bodyG);
    const BC = [0, 0.74, 0], BR = [0.86, 0.68, 0.72];
    blob(bodyG, COL.rose, BC, BR);
    blob(bodyG, COL.deep, [-0.36, 0.1, 0.42], [0.25, 0.14, 0.3], { fur: false });
    blob(bodyG, COL.deep, [0.36, 0.1, 0.42], [0.25, 0.14, 0.3], { fur: false });
    const neckGeo = new THREE.CylinderGeometry(0.25, 0.34, 1.4, 40, 1, false);
    blob(bodyG, COL.rose, [0, 1.8, 0.02], [1, 1, 1], { geo: neckGeo, outline: 0.03 });
    const onBody = (d) => { const k = 1 / Math.hypot(d[0] / BR[0], d[1] / BR[1], d[2] / BR[2]); return [BC[0] + d[0] * k, BC[1] + d[1] * k, BC[2] + d[2] * k]; };
    const onNeck = (y, a) => { const r = 0.25 + (2.5 - y) / 1.4 * 0.09; return [r * Math.sin(a), y, 0.02 + r * Math.cos(a)]; };
    // spots: flattened discs on the surface of the body and neck
    const spots = [];
    [[onNeck(2.15, 0.35), [0.1, 0.12, 0.022]], [onNeck(1.62, -0.4), [0.1, 0.09, 0.022]], [onBody([0.55, 0.45, 0.7]), [0.2, 0.15, 0.025]],
     [onBody([-0.7, 0.15, 0.7]), [0.22, 0.17, 0.025]], [onBody([0.7, -0.35, 0.6]), [0.17, 0.13, 0.025]]].forEach(([p, s], i) => {
      const d = blob(bodyG, COL.deep, p, s, { fur: false, noOutline: true });
      const c = i < 2 ? new THREE.Vector3(0, p[1], 0.02) : new THREE.Vector3(...BC);
      d.lookAt(new THREE.Vector3(...p).multiplyScalar(2).sub(c)); spots.push(d);
    });
    // bow tie
    const bow = new THREE.Group(); bow.position.set(0, 1.28, 0.4); bodyG.add(bow);
    const cone = new THREE.ConeGeometry(0.13, 0.22, 24);
    const w1 = new THREE.Mesh(cone, S.mat(COL.teal)); w1.rotation.z = Math.PI / 2; w1.position.x = -0.14; bow.add(w1);
    const w2 = new THREE.Mesh(cone, S.mat(COL.teal)); w2.rotation.z = -Math.PI / 2; w2.position.x = 0.14; bow.add(w2);
    blob(bow, COL.tealDark, [0, 0, 0.02], [0.08, 0.08, 0.07], { fur: false });
    // arms (pivot at the shoulder)
    const arms = [-1, 1].map((side) => {
      const pivot = new THREE.Group(); pivot.position.set(side * 0.8, 1.0, 0.22); bodyG.add(pivot);
      blob(pivot, COL.rose, [0, -0.25, 0], [0.13, 0.3, 0.13], { outline: 0.03 });
      blob(pivot, COL.deep, [0, -0.54, 0], [0.13, 0.1, 0.13], { fur: false });
      pivot.rotation.z = side * 0.25; return pivot;
    });
    // head (pivot at the top of the neck)
    const headP = new THREE.Group(); headP.position.set(0, 2.55, 0.05); root.add(headP);
    const head = new THREE.Group(); head.scale.setScalar(big); headP.add(head);
    blob(head, COL.rose, [0, 0.22, 0], [0.62, 0.58, 0.56]);
    blob(head, COL.muzzle, [0, -0.06, 0.38], [0.42, 0.3, 0.32]);
    blob(head, COL.deep, [-0.12, -0.02, 0.69], [0.035, 0.045, 0.02], { fur: false, noOutline: true });
    blob(head, COL.deep, [0.12, -0.02, 0.69], [0.035, 0.045, 0.02], { fur: false, noOutline: true });
    blob(head, COL.deep, [0, 0.62, 0.36], [0.1, 0.08, 0.04], { fur: false, noOutline: true });   // forehead spot
    const eyes = [-1, 1].map((side) => {
      const g = new THREE.Group(); g.position.set(side * 0.24, 0.28, 0.5); head.add(g);
      const e = new THREE.Mesh(sphere, new THREE.MeshBasicMaterial({ color: COL.ink })); e.scale.set(0.075, 0.095, 0.06); g.add(e);
      const h = new THREE.Mesh(sphere, new THREE.MeshBasicMaterial({ color: COL.white })); h.scale.setScalar(0.024); h.position.set(-0.025, 0.035, 0.05); g.add(h);
      return g;
    });
    const smile = new THREE.Mesh(new THREE.TorusGeometry(0.07, 0.014, 8, 24, Math.PI), new THREE.MeshBasicMaterial({ color: COL.ink }));
    smile.position.set(0, -0.12, 0.7); smile.rotation.z = Math.PI; head.add(smile);
    const ears = [-1, 1].map((side) => {
      const p = new THREE.Group(); p.position.set(side * 0.52, 0.45, -0.02); head.add(p);
      blob(p, COL.rose, [side * 0.22, 0, 0], [0.26, 0.1, 0.12], { outline: 0.025 });
      blob(p, COL.muzzle, [side * 0.24, 0, 0.05], [0.16, 0.05, 0.05], { fur: false, noOutline: true });
      p.rotation.z = side * -0.35; return p;
    });
    const horns = [-1, 1].map((side) => {
      const p = new THREE.Group(); p.position.set(side * 0.2, 0.7, -0.05); head.add(p);
      blob(p, COL.rose, [0, 0.18, 0], [1, 1, 1], { geo: new THREE.CylinderGeometry(0.06, 0.075, 0.36, 20), outline: 0.02, fur: false });
      blob(p, COL.deep, [0, 0.4, 0], [0.12, 0.12, 0.12], { outline: 0.02 });
      p.rotation.z = side * -0.12; return p;
    });
    // laptop
    const laptop = new THREE.Group(); laptop.position.set(0, 0.5, 0.95); root.add(laptop);
    const base = new THREE.Mesh(new THREE.BoxGeometry(1.15, 0.05, 0.7), new THREE.MeshStandardMaterial({ color: COL.laptop, roughness: 0.5 }));
    laptop.add(base);
    const lid = new THREE.Mesh(new THREE.BoxGeometry(1.15, 0.72, 0.04), new THREE.MeshStandardMaterial({ color: COL.laptopLid, roughness: 0.4 }));
    lid.position.set(0, 0.36, 0.33); lid.rotation.x = -0.25; laptop.add(lid);
    const logo = new THREE.Mesh(sphere, new THREE.MeshBasicMaterial({ color: COL.rose })); logo.scale.set(0.05, 0.05, 0.01); logo.position.set(0, 0.36, 0.36); logo.rotation.x = -0.25; laptop.add(logo);

    return {
      root, spots, glowSpots: [spots[0], spots[1], spots[2]],
      apply(m) {
        bodyG.scale.set(1 - 0.008 * m.breathe, 1 + 0.012 * m.breathe, 1 - 0.008 * m.breathe);
        root.position.y = 0.38 * m.hop;
        headP.position.y = 2.55 + 0.03 * m.breathe;
        headP.rotation.set(m.nod, m.yaw, m.tilt);
        eyes.forEach((e) => {
          e.scale.y = m.happyEyes ? 0.35 : Math.max(0.08, 1 - m.blink);
          e.position.x = Math.sign(e.position.x) * 0.24 + m.look[0] * 0.04; e.position.y = 0.28 + m.look[1] * 0.04;
        });
        ears[0].rotation.z = 0.35 + 0.55 * m.earL; ears[1].rotation.z = -0.35 - 0.55 * m.earR;
        horns[0].rotation.z = 0.12 + 0.05 * Math.sin(performance.now() / 600); horns[1].rotation.z = -0.12 - 0.05 * Math.sin(performance.now() / 600 + 1);
        laptop.visible = m.laptop;
        arms[0].rotation.set(m.laptop ? -1.15 - 0.25 * m.armL : 0, 0, m.laptop ? 0.15 : -0.3);
        arms[1].rotation.set(m.laptop ? -1.15 - 0.25 * m.armR : 0, 0, m.armR < 0 ? 2.5 + 0.25 * Math.sin(performance.now() / 130) : (m.laptop ? -0.15 : 0.3));
        this.glowSpots.forEach((sp, i) => {
          const g = m.glow[i] || 0;
          sp.material.color.setHex(COL.deep).lerp(new THREE.Color(COL.amber), 0.85 * g);
          if (sp.material.emissive) sp.material.emissive.setHex(COL.amber).multiplyScalar(0.5 * g);
        });
      },
    };
  }

  // ---------------------------------------------------------------- the voxel rig
  function voxelRig() {
    const root = new THREE.Group(), V = 0.2, box = new THREE.BoxGeometry(V, V, V);
    const mats = {}; const mat = (c) => (mats[c] = mats[c] || new THREE.MeshLambertMaterial({ color: c }));
    function vox(group, x, y, z, c) { const m = new THREE.Mesh(box, mat(c)); m.position.set(x * V, y * V, z * V); group.add(m); return m; }
    function fill(group, x0, x1, y0, y1, z0, z1, c, skip) {
      for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) for (let z = z0; z <= z1; z++) {
        if (skip && skip(x, y, z)) continue; vox(group, x, y, z, c);
      }
    }
    const bodyG = new THREE.Group(); root.add(bodyG);
    fill(bodyG, -4, 4, 2, 6, -3, 3, COL.rose, (x, y, z) => (Math.abs(x) === 4 && (y === 2 || y === 6)) || (Math.abs(z) === 3 && (y === 2 || y === 6)));
    [[-3, 1, 2], [3, 1, 2], [-3, 1, -2], [3, 1, -2], [-3, 0, 2], [3, 0, 2], [-3, 0, -2], [3, 0, -2]].forEach(([x, y, z]) => vox(bodyG, x, y, z, y === 0 ? COL.deep : COL.rose));
    fill(bodyG, -1, 1, 7, 11, -1, 1, COL.rose);
    const spotVox = [[2, 5, 4], [-3, 4, 4], [1, 9, 2], [-1, 8, 2], [3, 3, 4]].map(([x, y, z]) => vox(bodyG, x, y, z, COL.deep));
    [[-2, 7, 2], [-1, 7, 2], [1, 7, 2], [2, 7, 2]].forEach(([x, y, z]) => vox(bodyG, x, y, z, COL.teal)); vox(bodyG, 0, 7, 2, COL.tealDark);
    const arms = [-1, 1].map((side) => { const p = new THREE.Group(); p.position.set(side * 5 * V, 5.5 * V, 1 * V); bodyG.add(p);
      vox(p, 0, -1, 0, COL.rose); vox(p, 0, -2, 0, COL.rose); vox(p, 0, -3, 0, COL.deep); return p; });
    const headP = new THREE.Group(); headP.position.set(0, 12 * V, 0); root.add(headP);
    let eyes = [];
    for (let x = -3; x <= 3; x++) for (let y = 0; y <= 4; y++) for (let z = -2; z <= 2; z++) {
      if (Math.abs(x) === 3 && (y === 0 || y === 4)) continue;
      let c = COL.rose;
      if (z === 2 && y <= 1 && Math.abs(x) <= 2) c = COL.muzzle;
      if (z === 2 && y === 1 && Math.abs(x) === 1) c = COL.deep;
      const v = vox(headP, x, y, z, c);
      if (z === 2 && y === 3 && Math.abs(x) === 2) { v.material = mat(COL.ink); eyes.push(v); }
    }
    vox(headP, 0, 5, 1, COL.deep);
    [[-2, 5, 0], [2, 5, 0]].forEach(([x, y, z]) => vox(headP, x, y, z, COL.rose)); [[-2, 6, 0], [2, 6, 0]].forEach(([x, y, z]) => vox(headP, x, y, z, COL.deep));
    const ears = [-1, 1].map((side) => { const p = new THREE.Group(); p.position.set(side * 3.5 * V, 3 * V, 0); headP.add(p);
      vox(p, side * 1, 0, 0, COL.rose); vox(p, side * 2, 0, 0, COL.rose); return p; });
    const laptop = new THREE.Group(); root.add(laptop);
    fill(laptop, -3, 3, 1, 1, 5, 7, COL.laptop); fill(laptop, -3, 3, 2, 5, 8, 8, COL.laptopLid);
    return {
      root, apply(m) {
        root.position.y = 0.3 * m.hop;
        headP.position.y = 12 * V + (m.breathe > 0.6 ? -V * 0.5 : 0);
        headP.rotation.set(m.nod, m.yaw, m.tilt);
        eyes.forEach((e) => { e.material = mat(m.blink > 0.3 || m.happyEyes && Math.floor(performance.now() / 300) % 2 ? COL.rose : COL.ink); });
        ears[0].rotation.z = 0.6 * m.earL; ears[1].rotation.z = -0.6 * m.earR;
        laptop.visible = m.laptop;
        arms[0].rotation.x = m.laptop ? -1.2 - 0.3 * m.armL : 0; arms[1].rotation.x = m.laptop ? -1.2 - 0.3 * m.armR : 0;
        arms[1].rotation.z = m.armR < 0 ? 2.6 : 0;
        spotVox.forEach((v, i) => { v.material = mat(i < 3 && m.glow[i] > 0.4 ? COL.amber : COL.deep); });
      },
    };
  }

  // ---------------------------------------------------------------- scene per canvas
  function create3d(kind, canvas) {
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    const scene = new THREE.Scene();
    let camera;
    if (kind === "voxel") {
      const s = 2.3; camera = new THREE.OrthographicCamera(-s, s, s, -s, 0.1, 50);
      camera.position.set(6, 6.5, 8); camera.lookAt(0, 1.55, 0);
    } else {
      camera = new THREE.PerspectiveCamera(28, 1, 0.1, 50);
      camera.position.set(1.3, 2.4, 9.4); camera.lookAt(0, 1.75, 0);
    }
    const L = { voxel: [0.62, 0.55, 0.25], toon: [0.45, 0.62, 0.3], clay: [0.72, 0.7, 0.4], plush: [0.85, 0.55, 0.75] }[kind];
    const hemi = new THREE.HemisphereLight(0xfff4f6, 0x6a4a56, L[0]); scene.add(hemi);
    const key = new THREE.DirectionalLight(0xffffff, L[1]); key.position.set(3, 6, 5); scene.add(key);
    const rim = new THREE.DirectionalLight(0xffe2ea, L[2]); rim.position.set(-4, 3, -5); scene.add(rim);
    // soft contact shadow
    const sc = document.createElement("canvas"); sc.width = sc.height = 128; const g = sc.getContext("2d");
    const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64); grad.addColorStop(0, "rgba(40,20,30,0.28)"); grad.addColorStop(1, "rgba(40,20,30,0)");
    g.fillStyle = grad; g.fillRect(0, 0, 128, 128);
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(3, 2), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(sc), transparent: true, depthWrite: false }));
    shadow.rotation.x = -Math.PI / 2; shadow.position.y = 0.01; scene.add(shadow);
    const rig = kind === "voxel" ? voxelRig() : smoothRig(kind);
    scene.add(rig.root);
    if (kind === "voxel") { rig.root.position.set(0, 0, 0); }
    let w = 0, h = 0;
    return {
      canvas,
      render(state, t) {
        const cw = canvas.clientWidth, ch = canvas.clientHeight;
        if (cw !== w || ch !== h) { w = cw; h = ch; renderer.setSize(w, h, false); if (camera.isPerspectiveCamera) { camera.aspect = w / h; camera.updateProjectionMatrix(); } }
        const m = motion(state, t);
        rig.apply(m);
        shadow.scale.setScalar(1 - 0.25 * m.hop);
        renderer.render(scene, camera);
      },
    };
  }

  window.create3d = create3d;
})();
