/**
 * JARVIS — Multi-mode particle visualization.
 *
 * Floating particles with line connections between nearby ones.
 * Lines fade in/out based on state. Transition tumble on state change.
 * Speaking pulls particles closer for denser connections.
 *
 * Supports three personas:
 *  - JARVIS mode (default): cool blue/cyan palette
 *  - Ultron mode: orange/red palette + clock-face tick bezel + sweep arc
 *  - Sci-Fi mode: holographic white/cyan core with magenta accents +
 *    outer dashed precision ring + 12 orbiting glyph markers (Pragmata-style HUD)
 */

import * as THREE from "three";

export type OrbState = "idle" | "listening" | "thinking" | "speaking" | "uncertain";
export type PersonaMode = "jarvis" | "ultron" | "scifi";

export interface Orb {
  setState(s: OrbState): void;
  setAnalyser(a: AnalyserNode | null): void;
  setPersonaMode(mode: PersonaMode): void;
  /** Legacy toggle — true = Ultron, false = JARVIS. Kept for backwards compat. */
  setUltronMode(enabled: boolean): void;
  destroy(): void;
}

export function createOrb(canvas: HTMLCanvasElement): Orb {
  let destroyed = false;
  const N = 2000;

  // ── JARVIS colors (blue/cyan) ──
  const idleColor            = new THREE.Color(0x4ca8e8);
  const listeningColor       = new THREE.Color(0x58c3ff);
  const thinkingColor        = new THREE.Color(0x39ff88);
  const speakingColor        = new THREE.Color(0x5ab8f0);
  const uncertainColor       = new THREE.Color(0xff7a3d);
  const thinkingElectronColor = new THREE.Color(0xbfffe0);
  const uncertainElectronColor = new THREE.Color(0xffc28f);
  const defaultElectronColor = new THREE.Color(0xffffff);
  const jarvisRing2Base      = new THREE.Color(0x00d4e8);
  const jarvisRing3Base      = new THREE.Color(0x38b6cc);
  const jarvisRing3NodeBase  = new THREE.Color(0x7ee8f8);

  // ── Ultron colors (orange/red) ──
  const ultronIdleColor      = new THREE.Color(0xff5500);
  const ultronListenColor    = new THREE.Color(0xff8c00);
  const ultronThinkColor     = new THREE.Color(0xff2200);
  const ultronSpeakColor     = new THREE.Color(0xff7700);
  const ultronUncertainColor = new THREE.Color(0xffcc00);
  const ultronElectronColor  = new THREE.Color(0xffaa44);
  const ultronRingBase       = new THREE.Color(0xff6600);
  const ultronRing2Base      = new THREE.Color(0xff4400);
  const ultronRing3Base      = new THREE.Color(0xff3300);
  const ultronNodeBase       = new THREE.Color(0xffcc00);

  // ── Sci-Fi colors (Tony Stark blue/white dominant + subtle red ambient) ──
  // Particle / core state colors — blue-white dominant, red only when alert.
  const scifiIdleColor       = new THREE.Color(0x9ed4ff);
  const scifiListenColor     = new THREE.Color(0x4cc4ff);
  const scifiThinkColor      = new THREE.Color(0xff5577);
  const scifiSpeakColor      = new THREE.Color(0xe8f4ff);
  const scifiUncertainColor  = new THREE.Color(0xff8855);
  const scifiElectronColor   = new THREE.Color(0xffffff);
  // HUD layer base tints (used when state color is blended in per-frame).
  const scifiRingWhite       = new THREE.Color(0xe8f4ff); // outer boundary — near white
  const scifiRingBlue        = new THREE.Color(0x6cc6ff); // primary holo blue
  const scifiRingDeep        = new THREE.Color(0x3aa8f0); // deeper blue for data arcs
  const scifiRingRed         = new THREE.Color(0xff4d5a); // red accent for targeting brackets
  const scifiTickBlue        = new THREE.Color(0xa0dcff); // fine tick marks

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setClearColor(0x000000, 1);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 1, 1000);
  camera.position.z = 80;

  // ── Particles ──
  const geo = new THREE.BufferGeometry();
  const pos = new Float32Array(N * 3);
  const vel = new Float32Array(N * 3);
  const phase = new Float32Array(N);

  for (let i = 0; i < N; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const r = Math.pow(Math.random(), 0.5) * 25;
    pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    pos[i * 3 + 2] = r * Math.cos(phi);
    phase[i] = Math.random() * 1000;
  }

  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));

  const mat = new THREE.PointsMaterial({
    color: 0x4ca8e8, size: 0.4, transparent: true, opacity: 0.6,
    sizeAttenuation: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });

  const points = new THREE.Points(geo, mat);
  scene.add(points);

  // ── Connection lines ──
  const MAX_LINES = 8000;
  const linePos = new Float32Array(MAX_LINES * 6);
  const lineGeo = new THREE.BufferGeometry();
  lineGeo.setAttribute("position", new THREE.BufferAttribute(linePos, 3));
  lineGeo.setDrawRange(0, 0);

  const lineMat = new THREE.LineBasicMaterial({
    color: 0x4ca8e8, transparent: true, opacity: 0.0,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });

  const lines = new THREE.LineSegments(lineGeo, lineMat);
  scene.add(lines);

  // ── Electrons — bright dots that travel along connections ──
  const MAX_ELECTRONS = 200;
  const electronGeo = new THREE.BufferGeometry();
  const electronPos = new Float32Array(MAX_ELECTRONS * 3);
  electronGeo.setAttribute("position", new THREE.BufferAttribute(electronPos, 3));
  electronGeo.setDrawRange(0, 0);

  const electronMat = new THREE.PointsMaterial({
    color: 0xffffff, size: 0.8, transparent: true, opacity: 1.0,
    sizeAttenuation: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });

  const electrons = new THREE.Points(electronGeo, electronMat);
  scene.add(electrons);

  // Each electron: start point, end point, progress (0-1), speed
  interface Electron { sx: number; sy: number; sz: number; ex: number; ey: number; ez: number; t: number; speed: number; }
  const activeElectrons: Electron[] = [];
  let electronSpawnRate = 0;
  let targetElectronRate = 0;
  let lastElectronSpawn = 0;

  // Store active connections for electron spawning
  let activeConnections: { x1: number; y1: number; z1: number; x2: number; y2: number; z2: number }[] = [];

  // ── HUD Rings (JARVIS-style) ──
  function makeArc(radius: number, startDeg: number, endDeg: number, segs = 64): THREE.BufferGeometry {
    const pts: THREE.Vector3[] = [];
    const s = (startDeg * Math.PI) / 180;
    const e = (endDeg   * Math.PI) / 180;
    for (let i = 0; i <= segs; i++) {
      const a = s + (i / segs) * (e - s);
      pts.push(new THREE.Vector3(Math.cos(a) * radius, Math.sin(a) * radius, 0));
    }
    return new THREE.BufferGeometry().setFromPoints(pts);
  }

  function makeSegmentGroup(
    radius: number,
    segments: [number, number][],
    mat: THREE.LineBasicMaterial,
  ): THREE.Group {
    const g = new THREE.Group();
    for (const [s, e] of segments) {
      g.add(new THREE.Line(makeArc(radius, s, e, Math.ceil(Math.abs(e - s) / 1.5)), mat));
    }
    return g;
  }

  function makeNodes(radius: number, angleDegrees: number[], mat: THREE.PointsMaterial): THREE.Points {
    const buf = new Float32Array(angleDegrees.length * 3);
    angleDegrees.forEach((deg, i) => {
      const a = (deg * Math.PI) / 180;
      buf[i * 3]     = Math.cos(a) * radius;
      buf[i * 3 + 1] = Math.sin(a) * radius;
      buf[i * 3 + 2] = 0;
    });
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(buf, 3));
    return new THREE.Points(geo, mat);
  }

  // ── Ring 1 ──
  const R1 = 36;
  const arc1Mat = new THREE.LineBasicMaterial({
    color: 0x90d8ff, transparent: true, opacity: 0.82,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const arc1 = new THREE.Line(makeArc(R1, -120, 121, 160), arc1Mat);
  scene.add(arc1);

  // ── Ring 2 ──
  const R2 = 29;
  const ring2Mat = new THREE.LineBasicMaterial({
    color: 0x00d4e8, transparent: true, opacity: 0.62,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const ring2Segs: [number, number][] = [
    [0,   108],
    [133, 183],
    [210, 235],
    [268, 282],
  ];
  const ring2Group = makeSegmentGroup(R2, ring2Segs, ring2Mat);
  scene.add(ring2Group);

  const ring2NodeMat = new THREE.PointsMaterial({
    color: 0x00e5ff, size: 2.4, transparent: true, opacity: 0.9,
    sizeAttenuation: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const ring2Nodes = makeNodes(R2, [120.5, 196.5, 251.5, 321], ring2NodeMat);
  scene.add(ring2Nodes);

  // ── Ring 3 ──
  const R3 = 22;
  const ring3Mat = new THREE.LineBasicMaterial({
    color: 0x38b6cc, transparent: true, opacity: 0.52,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const ring3Segs: [number, number][] = [
    [20,  115],
    [155, 205],
    [240, 258],
  ];
  const ring3Group = makeSegmentGroup(R3, ring3Segs, ring3Mat);
  scene.add(ring3Group);

  const ring3NodeMat = new THREE.PointsMaterial({
    color: 0x7ee8f8, size: 1.8, transparent: true, opacity: 0.85,
    sizeAttenuation: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const ring3Nodes = makeNodes(R3, [135, 222.5, 319], ring3NodeMat);
  scene.add(ring3Nodes);

  // ── Ultron Tick Marks — clock-face radial ticks between Ring 3 (R3=22) and Ring 2 (R2=29) ──
  // 36 thick rectangular ticks (like watch bezel marks) built as a single merged mesh.
  // Every 3rd tick is a "major" mark (longer, wider = hour style).
  // Hidden by default; fades in when Ultron mode is active.
  const ultronTickMat = new THREE.MeshBasicMaterial({
    color: 0xff6000, transparent: true, opacity: 0.0,
    blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
  });
  const ultronTickMesh: THREE.Mesh = (() => {
    const TICK_N   = 36;
    const INNER_R  = 23.5;
    const MAJOR_OR = 28.5;  // outer radius for major (hour) ticks
    const MINOR_OR = 26.8;  // outer radius for minor ticks
    const MAJOR_W  = 1.4;   // half-width for major tick
    const MINOR_W  = 0.65;  // half-width for minor tick
    const positions: number[] = [];
    const indices: number[] = [];
    let vi = 0;
    for (let i = 0; i < TICK_N; i++) {
      const major  = i % 3 === 0;
      const outerR = major ? MAJOR_OR : MINOR_OR;
      const hw     = (major ? MAJOR_W : MINOR_W) / 2; // half-width (perpendicular)
      const midR   = (INNER_R + outerR) / 2;
      const hl     = (outerR - INNER_R) / 2; // half-length (radial)
      const angle  = (i / TICK_N) * Math.PI * 2;
      const ca = Math.cos(angle), sa = Math.sin(angle);
      // radial unit vector: (ca, sa); perpendicular: (-sa, ca)
      // 4 corners of the rectangle
      positions.push(
        ca * midR + ca * hl - sa * hw,  sa * midR + sa * hl + ca * hw,  0, // outer-right
        ca * midR + ca * hl + sa * hw,  sa * midR + sa * hl - ca * hw,  0, // outer-left
        ca * midR - ca * hl + sa * hw,  sa * midR - sa * hl - ca * hw,  0, // inner-left
        ca * midR - ca * hl - sa * hw,  sa * midR - sa * hl + ca * hw,  0, // inner-right
      );
      indices.push(vi, vi+1, vi+2,  vi, vi+2, vi+3);
      vi += 4;
    }
    const tGeo = new THREE.BufferGeometry();
    tGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(positions), 3));
    tGeo.setIndex(indices);
    return new THREE.Mesh(tGeo, ultronTickMat);
  })();
  scene.add(ultronTickMesh);

  // ── Ultron Sweep — bright short arc that laps the tick ring (loading "head") ──
  // Rotates 2.5× faster than the tick group; additive blending makes overlapping
  // ticks appear brighter, creating a natural comet-head loading effect.
  const ultronSweepMat = new THREE.LineBasicMaterial({
    color: 0xffcc44, transparent: true, opacity: 0.0,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const ultronSweep = new THREE.Line(makeArc(26, 0, 55, 35), ultronSweepMat);
  scene.add(ultronSweep);

  // ══════════════════════════════════════════════════════════════════════════
  // ── Sci-Fi HUD Layers — holographic multi-ring targeting system ──
  // Tony Stark style: concentric rings, data arcs, targeting brackets,
  // fine radial ticks, and a persistent crosshair. Blue/white dominant with
  // red accent on brackets. All layers fade in only when personaMode==="scifi"
  // and their colors blend with the current state color each frame, so the
  // HUD breathes with the orb just like the JARVIS rings do.
  // ══════════════════════════════════════════════════════════════════════════

  // L1 — Outer dashed boundary (R=44), very thin, white.
  const scifiL1Mat = new THREE.LineDashedMaterial({
    color: 0xe8f4ff, transparent: true, opacity: 0.0,
    dashSize: 0.45, gapSize: 1.2,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const scifiL1 = new THREE.Line(makeArc(44, 0, 360, 420), scifiL1Mat);
  scifiL1.computeLineDistances();
  scene.add(scifiL1);

  // L2 — Precision ring (R=40) with four 8° cardinal gaps, blue.
  const scifiL2Mat = new THREE.LineBasicMaterial({
    color: 0x6cc6ff, transparent: true, opacity: 0.0,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const scifiL2Segs: [number, number][] = [
    [  4,  86],
    [ 94, 176],
    [184, 266],
    [274, 356],
  ];
  const scifiL2 = makeSegmentGroup(40, scifiL2Segs, scifiL2Mat);
  scene.add(scifiL2);

  // L3 — Targeting corner brackets (R=37): four L-shaped marks that lock
  // onto the orb. Red accent gives the "digital targeting" feel.
  const scifiL3Mat = new THREE.LineBasicMaterial({
    color: 0xff4d5a, transparent: true, opacity: 0.0,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const scifiL3Segs: [number, number][] = [
    [ 32,  58],
    [122, 148],
    [212, 238],
    [302, 328],
  ];
  const scifiL3 = makeSegmentGroup(37, scifiL3Segs, scifiL3Mat);
  // Small radial stubs extending outward from each bracket midpoint.
  const scifiL3Stubs = (() => {
    const pts: THREE.Vector3[] = [];
    for (const midDeg of [45, 135, 225, 315]) {
      const a = (midDeg * Math.PI) / 180;
      const cx = Math.cos(a), sx = Math.sin(a);
      pts.push(new THREE.Vector3(cx * 37, sx * 37, 0));
      pts.push(new THREE.Vector3(cx * 38.8, sx * 38.8, 0));
    }
    return new THREE.LineSegments(
      new THREE.BufferGeometry().setFromPoints(pts),
      scifiL3Mat,
    );
  })();
  scene.add(scifiL3);
  scene.add(scifiL3Stubs);

  // L4 — Data segment arcs (R=34), irregular lengths, deeper blue.
  // Gives the impression of streaming data / partial readouts.
  const scifiL4Mat = new THREE.LineBasicMaterial({
    color: 0x3aa8f0, transparent: true, opacity: 0.0,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const scifiL4Segs: [number, number][] = [
    [ 10,  62],
    [ 74,  98],
    [112, 175],
    [196, 220],
    [240, 305],
    [320, 350],
  ];
  const scifiL4 = makeSegmentGroup(34, scifiL4Segs, scifiL4Mat);
  scene.add(scifiL4);

  // L5 — Fine radial tick marks (R=31.5 → R=32.8) every 5°, 72 ticks.
  // Every 6th tick (30°) is longer — a "compass" feel.
  const scifiL5Mat = new THREE.LineBasicMaterial({
    color: 0xa0dcff, transparent: true, opacity: 0.0,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const scifiL5 = (() => {
    const pts: THREE.Vector3[] = [];
    const N = 72;
    for (let i = 0; i < N; i++) {
      const a = (i / N) * Math.PI * 2;
      const major = i % 6 === 0;
      const r1 = 31.5;
      const r2 = major ? 33.4 : 32.4;
      const ca = Math.cos(a), sa = Math.sin(a);
      pts.push(new THREE.Vector3(ca * r1, sa * r1, 0));
      pts.push(new THREE.Vector3(ca * r2, sa * r2, 0));
    }
    return new THREE.LineSegments(
      new THREE.BufferGeometry().setFromPoints(pts),
      scifiL5Mat,
    );
  })();
  scene.add(scifiL5);

  // L6 — Persistent crosshair / reticle. Four radial ticks at cardinals
  // extending past the outer boundary + tiny center squares on L1.
  const scifiL6Mat = new THREE.LineBasicMaterial({
    color: 0xe8f4ff, transparent: true, opacity: 0.0,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const scifiL6 = (() => {
    const pts: THREE.Vector3[] = [];
    // Outer radial ticks (extend from R=44 to R=46.5) at 0°, 90°, 180°, 270°
    for (const a of [0, Math.PI / 2, Math.PI, -Math.PI / 2]) {
      const ca = Math.cos(a), sa = Math.sin(a);
      pts.push(new THREE.Vector3(ca * 44, sa * 44, 0));
      pts.push(new THREE.Vector3(ca * 46.5, sa * 46.5, 0));
    }
    // Short inward stubs at diagonals (hints of secondary axes)
    for (const deg of [45, 135, 225, 315]) {
      const a = (deg * Math.PI) / 180;
      const ca = Math.cos(a), sa = Math.sin(a);
      pts.push(new THREE.Vector3(ca * 44, sa * 44, 0));
      pts.push(new THREE.Vector3(ca * 45.2, sa * 45.2, 0));
    }
    return new THREE.LineSegments(
      new THREE.BufferGeometry().setFromPoints(pts),
      scifiL6Mat,
    );
  })();
  scene.add(scifiL6);

  // L7 — Node dots pinned to L2 gap endpoints (4 bright dots in blue).
  const scifiL7Mat = new THREE.PointsMaterial({
    color: 0xbfe6ff, size: 2.2, transparent: true, opacity: 0.0,
    sizeAttenuation: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const scifiL7 = makeNodes(40, [0, 90, 180, 270], scifiL7Mat);
  scene.add(scifiL7);

  // All ring/HUD objects for clean dispose
  const _hudObjects: THREE.Object3D[] = [
    arc1, ring2Group, ring3Group, ring2Nodes, ring3Nodes,
    ultronTickMesh, ultronSweep,
    scifiL1, scifiL2, scifiL3, scifiL3Stubs, scifiL4, scifiL5, scifiL6, scifiL7,
  ];

  // Rotation angles
  let arc1Rot = 0;
  let ring2Rot = 0;
  let ring3Rot = 0;
  let ultronTickRot  = 0;
  let ultronSweepRot = 0;
  // Sci-Fi layer rotations — each layer drifts at its own rate for parallax.
  let scifiL1Rot = 0;
  let scifiL2Rot = 0;
  let scifiL3Rot = 0;
  let scifiL4Rot = 0;
  let scifiL5Rot = 0;

  // ── State ──
  let state: OrbState = "idle";
  let targetRadius = 25, currentRadius = 25;
  let targetSpeed = 0.3, currentSpeed = 0.3;
  let targetBright = 0.6, currentBright = 0.6;
  let targetSize = 0.4, currentSize = 0.4;
  let lineAmount = 0, targetLineAmount = 0;
  let lineDistance = 8;

  // Persona mode — set from outside via setPersonaMode()
  let personaMode: PersonaMode = "jarvis";

  // Transition tumble
  let spinX = 0, spinY = 0, spinZ = 0;
  let transitionEnergy = 0;
  let lastState: OrbState = "idle";

  // Depth Z
  let cloudZ = 0, cloudZVel = 0;

  // ── Audio ──
  let analyser: AnalyserNode | null = null;
  let freqData = new Uint8Array(64);
  let bass = 0, mid = 0;

  const clock = new THREE.Clock();

  function animate() {
    if (destroyed) return;
    requestAnimationFrame(animate);
    const t = clock.getElapsedTime();

    switch (state) {
      case "idle":
        targetRadius = 28; targetSpeed = 0.2; targetBright = 0.5; targetSize = 0.35;
        targetLineAmount = 0.15; targetElectronRate = 0; break;
      case "listening":
        targetRadius = 22; targetSpeed = 0.3; targetBright = 0.65; targetSize = 0.4;
        targetLineAmount = 0.4; targetElectronRate = 0; break;
      case "thinking":
        targetRadius = 16; targetSpeed = 0.5; targetBright = 0.7; targetSize = 0.3;
        targetLineAmount = 1.0; targetElectronRate = 0.015; break;
      case "speaking":
        targetRadius = 18; targetSpeed = 0.2; targetBright = 0.7; targetSize = 0.4;
        targetLineAmount = 0.8; targetElectronRate = 0; break;
      case "uncertain":
        targetRadius = 20; targetSpeed = 0.34; targetBright = 0.72; targetSize = 0.38;
        targetLineAmount = 0.68; targetElectronRate = 0.004; break;
    }

    currentRadius += (targetRadius - currentRadius) * 0.02;
    currentSpeed += (targetSpeed - currentSpeed) * 0.02;
    currentBright += (targetBright - currentBright) * 0.02;
    currentSize += (targetSize - currentSize) * 0.02;
    lineAmount += (targetLineAmount - lineAmount) * 0.02;
    electronSpawnRate += (targetElectronRate - electronSpawnRate) * 0.02;

    // Transition energy
    if (state !== lastState) { transitionEnergy = 1.0; lastState = state; }
    transitionEnergy *= 0.985;
    if (transitionEnergy > 0.05) {
      spinX += transitionEnergy * 0.012 * Math.sin(t * 1.7);
      spinY += transitionEnergy * 0.015;
      spinZ += transitionEnergy * 0.008 * Math.cos(t * 1.3);
    }

    // Audio
    bass = 0; mid = 0;
    if (analyser) {
      analyser.getByteFrequencyData(freqData);
      let bSum = 0, mSum = 0;
      for (let i = 0; i < 8; i++) bSum += freqData[i];
      for (let i = 8; i < 24; i++) mSum += freqData[i];
      bass = bSum / (8 * 255); mid = mSum / (16 * 255);
    }

    // Depth Z breathing
    let zTarget = Math.sin(t * 0.12) * 8;
    if (state === "thinking") zTarget = Math.sin(t * 0.3) * 15 + Math.sin(t * 0.9) * 6;
    else if (state === "uncertain") zTarget = Math.sin(t * 0.22) * 10 + Math.sin(t * 0.55) * 3;
    else if (state === "speaking") zTarget = Math.sin(t * 0.15) * 6 - bass * 10;
    cloudZVel += (zTarget - cloudZ) * 0.008;
    cloudZVel *= 0.94;
    cloudZ += cloudZVel;

    points.rotation.x = spinX; points.rotation.y = spinY; points.rotation.z = spinZ;
    points.position.z = cloudZ;
    lines.rotation.x = spinX; lines.rotation.y = spinY; lines.rotation.z = spinZ;
    lines.position.z = cloudZ;

    // ── Update particles ──
    const p = geo.getAttribute("position") as THREE.BufferAttribute;
    const a = p.array as Float32Array;

    for (let i = 0; i < N; i++) {
      const i3 = i * 3;
      let x = a[i3], y = a[i3 + 1], z = a[i3 + 2];
      const px = phase[i];

      vel[i3] += Math.sin(t * 0.05 + px) * 0.001 * currentSpeed;
      vel[i3 + 1] += Math.cos(t * 0.06 + px * 1.3) * 0.001 * currentSpeed;
      vel[i3 + 2] += Math.sin(t * 0.055 + px * 0.7) * 0.001 * currentSpeed;
      vel[i3] += Math.sin(t * 0.02 + px * 2.1 + y * 0.1) * 0.0008 * currentSpeed;
      vel[i3 + 1] += Math.cos(t * 0.025 + px * 1.7 + z * 0.1) * 0.0008 * currentSpeed;
      vel[i3 + 2] += Math.sin(t * 0.022 + px * 0.9 + x * 0.1) * 0.0008 * currentSpeed;

      const dist = Math.sqrt(x * x + y * y + z * z) || 0.01;
      const pull = Math.max(0, dist - currentRadius) * 0.002 + 0.0003;
      vel[i3] -= (x / dist) * pull;
      vel[i3 + 1] -= (y / dist) * pull;
      vel[i3 + 2] -= (z / dist) * pull;

      if (bass > 0.05) {
        vel[i3] += (x / dist) * bass * 0.02;
        vel[i3 + 1] += (y / dist) * bass * 0.02;
        vel[i3 + 2] += (z / dist) * bass * 0.02;
      }
      if (state === "speaking" && mid > 0.1) {
        const pulse = Math.sin(t * 8 + px);
        vel[i3] += (x / dist) * mid * 0.012 * pulse;
        vel[i3 + 1] += (y / dist) * mid * 0.012 * pulse;
      }

      vel[i3] *= 0.992; vel[i3 + 1] *= 0.992; vel[i3 + 2] *= 0.992;
      a[i3] += vel[i3]; a[i3 + 1] += vel[i3 + 1]; a[i3 + 2] += vel[i3 + 2];
    }
    p.needsUpdate = true;

    // ── Update lines ──
    if (lineAmount > 0.01) {
      const lp = lineGeo.getAttribute("position") as THREE.BufferAttribute;
      const la = lp.array as Float32Array;
      let lineCount = 0;
      const maxDist = lineDistance * (1 + bass * 0.5);
      const maxDistSq = maxDist * maxDist;
      const step = Math.max(1, Math.floor(N / 600));

      for (let i = 0; i < N && lineCount < MAX_LINES; i += step) {
        const i3 = i * 3;
        const x1 = a[i3], y1 = a[i3 + 1], z1 = a[i3 + 2];
        for (let j = i + step; j < N && lineCount < MAX_LINES; j += step) {
          const j3 = j * 3;
          const dx = a[j3] - x1, dy = a[j3 + 1] - y1, dz = a[j3 + 2] - z1;
          if (dx * dx + dy * dy + dz * dz < maxDistSq) {
            const idx = lineCount * 6;
            la[idx] = x1; la[idx+1] = y1; la[idx+2] = z1;
            la[idx+3] = a[j3]; la[idx+4] = a[j3+1]; la[idx+5] = a[j3+2];
            lineCount++;
          }
        }
      }
      lineGeo.setDrawRange(0, lineCount * 2);
      lp.needsUpdate = true;
      lineMat.opacity = lineAmount * 0.12;

      activeConnections = [];
      for (let c = 0; c < Math.min(lineCount, 500); c++) {
        const ci = c * 6;
        activeConnections.push({
          x1: la[ci], y1: la[ci+1], z1: la[ci+2],
          x2: la[ci+3], y2: la[ci+4], z2: la[ci+5],
        });
      }
    } else {
      lineGeo.setDrawRange(0, 0);
      activeConnections = [];
    }

    // ── Electrons (thinking only) ──
    if (activeConnections.length > 0 && electronSpawnRate > 0.005) {
      if (activeElectrons.length < 3 && (t - lastElectronSpawn) > 1.0) {
        const conn = activeConnections[Math.floor(Math.random() * activeConnections.length)];
        activeElectrons.push({
          sx: conn.x1, sy: conn.y1, sz: conn.z1,
          ex: conn.x2, ey: conn.y2, ez: conn.z2,
          t: 0,
          speed: 0.003 + Math.random() * 0.003,
        });
        lastElectronSpawn = t;
      }
    }

    const ep = electronGeo.getAttribute("position") as THREE.BufferAttribute;
    const ea = ep.array as Float32Array;
    let aliveCount = 0;

    for (let e = activeElectrons.length - 1; e >= 0; e--) {
      const el = activeElectrons[e];
      el.t += el.speed;
      if (el.t >= 1) { activeElectrons.splice(e, 1); continue; }
      const ei = aliveCount * 3;
      ea[ei]     = el.sx + (el.ex - el.sx) * el.t;
      ea[ei + 1] = el.sy + (el.ey - el.sy) * el.t;
      ea[ei + 2] = el.sz + (el.ez - el.sz) * el.t;
      aliveCount++;
    }

    electronGeo.setDrawRange(0, aliveCount);
    ep.needsUpdate = true;

    electrons.rotation.x = spinX; electrons.rotation.y = spinY; electrons.rotation.z = spinZ;
    electrons.position.z = cloudZ;

    mat.opacity = currentBright + bass * 0.08;
    mat.size = currentSize + bass * 0.05;

    // ── Color — JARVIS vs Ultron vs Sci-Fi palette ──
    // Pick the "current state" particle color
    const cParticle = personaMode === "ultron"
      ? (state === "thinking"  ? ultronThinkColor
        : state === "speaking"  ? ultronSpeakColor
        : state === "listening" ? ultronListenColor
        : state === "uncertain" ? ultronUncertainColor
        : ultronIdleColor)
      : personaMode === "scifi"
      ? (state === "thinking"  ? scifiThinkColor
        : state === "speaking"  ? scifiSpeakColor
        : state === "listening" ? scifiListenColor
        : state === "uncertain" ? scifiUncertainColor
        : scifiIdleColor)
      : (state === "thinking"  ? thinkingColor
        : state === "speaking"  ? speakingColor
        : state === "listening" ? listeningColor
        : state === "uncertain" ? uncertainColor
        : idleColor);

    const cElectron = personaMode === "ultron" ? ultronElectronColor
      : personaMode === "scifi" ? scifiElectronColor
      : (state === "thinking"  ? thinkingElectronColor
        : state === "uncertain" ? uncertainElectronColor
        : defaultElectronColor);

    mat.color.lerp(cParticle, 0.018);
    lineMat.color.lerp(cParticle, 0.018);
    electronMat.color.lerp(cElectron, 0.03);

    camera.position.x = Math.sin(t * 0.02) * 5;
    camera.position.y = Math.cos(t * 0.03) * 3;
    camera.lookAt(0, 0, cloudZ * 0.2);

    // ── HUD Ring animation ──
    const rMult = state === "thinking" ? 1.9
      : state === "speaking"  ? 1.4
      : state === "listening" ? 1.15
      : state === "uncertain" ? 1.5
      : 0.75;

    arc1Rot        +=  0.0017 * rMult;
    ring2Rot       += -0.0013 * rMult;
    ring3Rot       +=  0.0021 * rMult;
    // Tick ring: CW at ~Ring1 speed. Sweep arc: 2.5× faster = loading comet.
    ultronTickRot  +=  0.0016 * rMult;
    ultronSweepRot +=  0.0040 * rMult;
    // Sci-Fi: each layer drifts at its own speed for a parallax HUD feel.
    scifiL1Rot +=  0.0005 * rMult;   // outer boundary — very slow
    scifiL2Rot += -0.0009 * rMult;   // precision ring — counter-rotate
    scifiL3Rot +=  0.0022 * rMult;   // targeting brackets — fast lock-on
    scifiL4Rot += -0.0014 * rMult;   // data arcs — medium counter
    scifiL5Rot +=  0.0004 * rMult;   // fine ticks — barely moves

    const pFreq = state === "thinking" ? 1.5 : state === "speaking" ? 2.0 : 0.85;
    const pMag  = state === "uncertain" ? 0.055 : 0.038;
    const pVal  = Math.sin(t * pFreq);
    const orbNorm = currentRadius / 25;
    const s1 = orbNorm * (1 + pVal * pMag);
    const s2 = orbNorm * (1 - pVal * pMag * 0.8);
    const s3 = orbNorm * (1 + pVal * pMag * 0.5);
    const sT = orbNorm * (1 + pVal * pMag * 0.12); // ticks pulse subtly

    arc1.rotation.z = arc1Rot;
    arc1.scale.set(s1, s1, 1);

    ring2Group.rotation.z = ring2Rot;
    ring2Group.scale.set(s2, s2, 1);
    ring2Nodes.rotation.z = ring2Rot;
    ring2Nodes.scale.set(s2, s2, 1);

    ring3Group.rotation.z = ring3Rot;
    ring3Group.scale.set(s3, s3, 1);
    ring3Nodes.rotation.z = ring3Rot;
    ring3Nodes.scale.set(s3, s3, 1);

    // Ultron ticks + sweep: fade in/out with mode, both pulse with orb scale
    const targetTickOpacity  = personaMode === "ultron" ? 0.58 : 0.0;
    const targetSweepOpacity = personaMode === "ultron" ? 0.92 : 0.0;
    ultronTickMat.opacity  += (targetTickOpacity  - ultronTickMat.opacity)  * 0.04;
    ultronSweepMat.opacity += (targetSweepOpacity - ultronSweepMat.opacity) * 0.04;
    ultronTickMesh.rotation.z = ultronTickRot;
    ultronTickMesh.scale.set(sT, sT, 1);
    ultronSweep.rotation.z = ultronSweepRot;
    ultronSweep.scale.set(sT, sT, 1);

    // ── Sci-Fi HUD layer pulsing & scaling ──
    // Each layer gets its own pulse magnitude so they "breathe" at slightly
    // different rates, creating a parallax holographic feel. All tied to
    // orbNorm so they shrink/grow together with the orb.
    const scifiOn = personaMode === "scifi";
    const sL1 = orbNorm * (1 + pVal * pMag * 0.15); // outer boundary — gentle
    const sL2 = orbNorm * (1 + pVal * pMag * 0.35); // precision ring
    const sL3 = orbNorm * (1 - pVal * pMag * 0.55); // targeting brackets — inverse
    const sL4 = orbNorm * (1 + pVal * pMag * 0.45); // data arcs
    const sL5 = orbNorm * (1 + pVal * pMag * 0.20); // ticks — subtle

    const fade = (target: number, current: number) => current + (target - current) * 0.04;
    const tL1 = scifiOn ? 0.55 : 0.0;
    const tL2 = scifiOn ? 0.78 : 0.0;
    const tL3 = scifiOn ? 0.82 : 0.0;
    const tL4 = scifiOn ? 0.62 : 0.0;
    const tL5 = scifiOn ? 0.50 : 0.0;
    const tL6 = scifiOn ? 0.72 : 0.0;
    const tL7 = scifiOn ? 0.92 : 0.0;
    scifiL1Mat.opacity  = fade(tL1, scifiL1Mat.opacity);
    scifiL2Mat.opacity  = fade(tL2, scifiL2Mat.opacity);
    scifiL3Mat.opacity  = fade(tL3, scifiL3Mat.opacity);
    scifiL4Mat.opacity  = fade(tL4, scifiL4Mat.opacity);
    scifiL5Mat.opacity  = fade(tL5, scifiL5Mat.opacity);
    scifiL6Mat.opacity  = fade(tL6, scifiL6Mat.opacity);
    scifiL7Mat.opacity  = fade(tL7, scifiL7Mat.opacity);

    scifiL1.rotation.z = scifiL1Rot;      scifiL1.scale.set(sL1, sL1, 1);
    scifiL2.rotation.z = scifiL2Rot;      scifiL2.scale.set(sL2, sL2, 1);
    scifiL3.rotation.z = scifiL3Rot;      scifiL3.scale.set(sL3, sL3, 1);
    scifiL3Stubs.rotation.z = scifiL3Rot; scifiL3Stubs.scale.set(sL3, sL3, 1);
    scifiL4.rotation.z = scifiL4Rot;      scifiL4.scale.set(sL4, sL4, 1);
    scifiL5.rotation.z = scifiL5Rot;      scifiL5.scale.set(sL5, sL5, 1);
    // L6 crosshair & L7 nodes — ride on L2 so they align with precision ring.
    scifiL6.scale.set(sL1, sL1, 1);
    scifiL7.rotation.z = scifiL2Rot;      scifiL7.scale.set(sL2, sL2, 1);

    // ── Ring colors — JARVIS vs Ultron vs Sci-Fi ──
    // Sci-Fi: derive a state-reactive color the same way JARVIS does, so the
    // HUD shifts subtly from cool blue (idle/listening) toward the "alert"
    // red accent (thinking) and amber (uncertain). This is what makes the
    // rings "adapt" to state instead of staying static.
    const scifiStateC = state === "thinking"  ? scifiThinkColor
      : state === "speaking"  ? scifiSpeakColor
      : state === "listening" ? scifiListenColor
      : state === "uncertain" ? scifiUncertainColor
      : scifiIdleColor;

    const cRingState = personaMode === "ultron" ? ultronRingBase
      : personaMode === "scifi" ? scifiStateC
      : (state === "thinking"  ? thinkingColor
        : state === "uncertain" ? uncertainColor
        : state === "speaking"  ? speakingColor
        : state === "listening" ? listeningColor
        : idleColor);

    const ring2BaseC = personaMode === "ultron" ? ultronRing2Base
      : personaMode === "scifi" ? scifiRingBlue
      : jarvisRing2Base;
    const ring3BaseC = personaMode === "ultron" ? ultronRing3Base
      : personaMode === "scifi" ? scifiRingDeep
      : jarvisRing3Base;
    const ring3NodeBaseC = personaMode === "ultron" ? ultronNodeBase
      : personaMode === "scifi" ? scifiTickBlue
      : jarvisRing3NodeBase;
    const ring2NodeTargetC = personaMode === "ultron" ? ultronNodeBase
      : personaMode === "scifi" ? scifiRingWhite
      : cRingState;

    arc1Mat.color.lerp(cRingState, 0.025);
    ring2Mat.color.lerp(ring2BaseC.clone().lerp(cRingState, 0.25), 0.02);
    ring3Mat.color.lerp(ring3BaseC.clone().lerp(cRingState, 0.25), 0.02);
    ring2NodeMat.color.lerp(ring2NodeTargetC, 0.04);
    ring3NodeMat.color.lerp(ring3NodeBaseC.clone().lerp(cRingState, 0.3), 0.03);
    ultronTickMat.color.lerp(cRingState.clone().lerp(ultronRingBase, 0.4), 0.025);
    ultronSweepMat.color.lerp(ultronNodeBase, 0.03); // bright yellow-orange highlight

    // ── Sci-Fi HUD layer tints ──
    // Each layer has a base hue, then we blend in the state color at a
    // different mix ratio. Targeting brackets (L3) stay mostly red — they
    // are the "alert" element. Everything else breathes blue → state.
    scifiL1Mat.color.lerp(scifiRingWhite.clone().lerp(scifiStateC, 0.18), 0.03);
    scifiL2Mat.color.lerp(scifiRingBlue.clone().lerp(scifiStateC, 0.30), 0.03);
    scifiL3Mat.color.lerp(scifiRingRed.clone().lerp(scifiStateC, 0.20), 0.03);
    scifiL4Mat.color.lerp(scifiRingDeep.clone().lerp(scifiStateC, 0.35), 0.03);
    scifiL5Mat.color.lerp(scifiTickBlue.clone().lerp(scifiStateC, 0.22), 0.03);
    scifiL6Mat.color.lerp(scifiRingWhite.clone().lerp(scifiStateC, 0.25), 0.03);
    scifiL7Mat.color.lerp(scifiStateC.clone().lerp(scifiRingWhite, 0.35), 0.04);

    // ── Ring opacity — dimmer at idle, brighter when active ──
    const ringVis = state === "idle" ? 0.42 : 0.78;
    arc1Mat.opacity      += (ringVis * 1.00 - arc1Mat.opacity)      * 0.04;
    ring2Mat.opacity     += (ringVis * 0.65 - ring2Mat.opacity)     * 0.04;
    ring3Mat.opacity     += (ringVis * 0.52 - ring3Mat.opacity)     * 0.04;
    ring2NodeMat.opacity += (ringVis * 0.90 - ring2NodeMat.opacity) * 0.04;
    ring3NodeMat.opacity += (ringVis * 0.82 - ring3NodeMat.opacity) * 0.04;
    // Note: ultronTickMat / ultronSweepMat opacity handled above (independent of ringVis)

    renderer.render(scene, camera);
  }

  function onResize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  }

  window.addEventListener("resize", onResize);
  animate();

  return {
    setState(s: OrbState) { state = s; },
    setAnalyser(a: AnalyserNode | null) {
      analyser = a;
      if (a) freqData = new Uint8Array(a.frequencyBinCount);
    },
    setPersonaMode(mode: PersonaMode) {
      personaMode = mode;
      // Snap particle color toward the new palette immediately so the
      // transition doesn't start from a fully wrong colour.
      const snap = mode === "ultron" ? ultronIdleColor
        : mode === "scifi" ? scifiIdleColor
        : idleColor;
      mat.color.set(snap);
      lineMat.color.set(snap);
    },
    setUltronMode(enabled: boolean) {
      this.setPersonaMode(enabled ? "ultron" : "jarvis");
    },
    destroy() {
      destroyed = true;
      window.removeEventListener("resize", onResize);
      arc1.geometry.dispose(); arc1Mat.dispose();
      ring2Group.children.forEach(c => (c as THREE.Line).geometry.dispose());
      ring2Mat.dispose();
      ring3Group.children.forEach(c => (c as THREE.Line).geometry.dispose());
      ring3Mat.dispose();
      ring2Nodes.geometry.dispose(); ring2NodeMat.dispose();
      ring3Nodes.geometry.dispose(); ring3NodeMat.dispose();
      ultronTickMesh.geometry.dispose(); ultronTickMat.dispose();
      ultronSweep.geometry.dispose(); ultronSweepMat.dispose();
      // Sci-Fi HUD layers
      scifiL1.geometry.dispose(); scifiL1Mat.dispose();
      scifiL2.children.forEach(c => (c as THREE.Line).geometry.dispose());
      scifiL2Mat.dispose();
      scifiL3.children.forEach(c => (c as THREE.Line).geometry.dispose());
      scifiL3Stubs.geometry.dispose();
      scifiL3Mat.dispose();
      scifiL4.children.forEach(c => (c as THREE.Line).geometry.dispose());
      scifiL4Mat.dispose();
      scifiL5.geometry.dispose(); scifiL5Mat.dispose();
      scifiL6.geometry.dispose(); scifiL6Mat.dispose();
      scifiL7.geometry.dispose(); scifiL7Mat.dispose();
      geo.dispose(); mat.dispose();
      lineGeo.dispose(); lineMat.dispose();
      electronGeo.dispose(); electronMat.dispose();
      renderer.dispose();
    },
  };
}
