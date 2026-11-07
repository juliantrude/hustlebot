"use strict";
// Cinema view (/cinema): the always-on display. No controls, pure black for OLED. The energy core on the left
// feeds one beam per hustle; each beam bends into its hustle's lane and runs on as that lane: the last hour of
// sessions pulses inside it like a waveform, and it ends in the hustle's graph, where sparks hit the increments that
// are running right now. Inactive hustles are ghost lanes without a beam.

(() => {
  const NEON = { haiku: "#00f0ff", sonnet: "#ffb300", opus: "#b46bff", fable: "#ff3dbd" };
  const STATE = { done: "#39ff88", running: "#3fa9ff", open: "#3a4654", blocked: "#ff3355" };
  const FONT = "system-ui, sans-serif";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const canvas = document.getElementById("scene");
  const ctx = canvas.getContext("2d");
  let state = null;
  const sparks = [];
  const shown = new Map(); // hustle id -> smoothed thickness, so lanes swell and fade instead of jumping
  let last = performance.now();

  async function poll() {
    try {
      state = await (await fetch("/api/state")).json();
    } catch (_) {
      // master away: keep the last picture
    }
  }
  poll();
  setInterval(poll, 1000);

  const rgba = (hex, a) => {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  };

  // --- graph layout: layers by longest path, rows by the mean of the predecessors -----------------------------
  function layoutGraph(incs) {
    const after = Object.fromEntries(incs.map((i) => [i.id, i.after]));
    const layer = {};
    const L = (n) => layer[n] ?? (layer[n] = after[n].length ? 1 + Math.max(...after[n].map(L)) : 0);
    incs.forEach((i) => L(i.id));
    const byLayer = [];
    for (const i of incs) (byLayer[layer[i.id]] ??= []).push(i.id);
    const y = {};
    byLayer.forEach((ns) => {
      const want = Object.fromEntries(ns.map((n, k) => [n, after[n].length ? after[n].reduce((s, a) => s + y[a], 0) / after[n].length : k - (ns.length - 1) / 2]));
      ns.sort((a, b) => want[a] - want[b] || a.localeCompare(b));
      const placed = [];
      ns.forEach((n, k) => placed.push(k ? Math.max(want[n], placed[k - 1] + 1) : want[n]));
      const shift = ns.reduce((s, n) => s + want[n], 0) / ns.length - placed.reduce((s, v) => s + v, 0) / ns.length;
      ns.forEach((n, k) => (y[n] = placed[k] + shift));
    });
    const ys = Object.values(y);
    return { layer, y, layers: byLayer.length, minY: Math.min(...ys), maxY: Math.max(...ys) };
  }

  // --- scene geometry ------------------------------------------------------------------------------------------
  function geometry(W, H) {
    const core = { x: W * 0.11, y: H * 0.5, r: Math.min(W, H) * 0.085 };
    const hustles = [...state.hustles].sort((a, b) => (b.active - a.active) || (b.share - a.share) || b.weight - a.weight);
    const n = hustles.length;
    const top = H * 0.12, bottom = H * 0.92;
    const gap = (bottom - top) / Math.max(1, n);
    const perPoint = (H * 0.12) / state.total_points;
    const laneX = W * 0.3, dagX = W * 0.66, endX = W * 0.95;
    // trunk at the core: beams stacked in lane order
    const lanes = hustles.map((c, i) => {
      const target = c.share > 0 ? Math.min(gap * 0.4, c.share * perPoint) : c.active && c.status === "blocked" ? 1.5 : 0;
      const prev = shown.get(c.id) ?? target;
      const thick = prev + (target - prev) * 0.06; // ease towards the new share
      shown.set(c.id, thick);
      return { hustle: c, y: top + gap * (i + 0.5), thick, gap };
    });
    const trunk = lanes.reduce((s, l) => s + l.thick, 0);
    let y0 = core.y - trunk / 2;
    for (const l of lanes) {
      l.y0 = y0 + l.thick / 2;
      y0 += l.thick;
    }
    return { core, lanes, laneX, dagX, endX };
  }

  // centre line of a beam: bezier from the core into the lane, then straight on
  function beamPoint(g, l, t) {
    const split = 0.45; // share of t spent in the bend
    if (t < split) {
      const u = t / split, v = 1 - u, mx = (g.core.x + g.laneX) / 2;
      return {
        x: v * v * v * g.core.x + 3 * v * v * u * mx + 3 * v * u * u * mx + u * u * u * g.laneX,
        y: v * v * v * l.y0 + 3 * v * v * u * l.y0 + 3 * v * u * u * l.y + u * u * u * l.y,
      };
    }
    const u = (t - split) / (1 - split);
    return { x: g.laneX + (g.dagX - g.laneX) * u, y: l.y };
  }

  function beamShape(g, l, half) {
    const mx = (g.core.x + g.laneX) / 2;
    ctx.beginPath();
    ctx.moveTo(g.core.x, l.y0 - half);
    ctx.bezierCurveTo(mx, l.y0 - half, mx, l.y - half, g.laneX, l.y - half);
    ctx.lineTo(g.dagX, l.y - half);
    ctx.lineTo(g.dagX, l.y + half);
    ctx.lineTo(g.laneX, l.y + half);
    ctx.bezierCurveTo(mx, l.y + half, mx, l.y0 + half, g.core.x, l.y0 + half);
    ctx.closePath();
  }

  // --- drawing -------------------------------------------------------------------------------------------------
  function drawLane(g, l, now) {
    const c = l.hustle, t = now / 1000;
    const live = l.thick > 0.5;
    const half = Math.max(0.75, l.thick / 2);
    if (live && c.share > 0) {
      const burn = Math.min(1, c.points_in_use / c.share);
      const grad = ctx.createLinearGradient(g.core.x, 0, g.dagX, 0);
      grad.addColorStop(0, "rgba(63,169,255,0)");
      grad.addColorStop(0.12, `rgba(63,169,255,${0.35 + 0.2 * burn})`);
      grad.addColorStop(0.5, `rgba(63,169,255,${0.12 + 0.18 * burn})`);
      grad.addColorStop(0.88, `rgba(120,80,255,${0.08 + 0.15 * burn})`);
      grad.addColorStop(1, "rgba(120,80,255,0)");
      beamShape(g, l, half);
      ctx.fillStyle = grad;
      ctx.fill();
    } else if (live) {
      // active but blocked: a flickering red thread
      beamShape(g, l, 0.75);
      ctx.fillStyle = rgba(STATE.blocked, reduced || Math.random() > 0.1 ? 0.6 : 0.1);
      ctx.fill();
    } else {
      // inactive: ghost lane, no connection to the core
      ctx.strokeStyle = "rgba(120,140,170,0.12)";
      ctx.setLineDash([2, 6]);
      ctx.beginPath();
      ctx.moveTo(g.laneX, l.y);
      ctx.lineTo(g.dagX, l.y);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // waveform: the last hour inside the lane, oldest at the bend, newest at the graph
    const hist = c.history || [];
    const span = g.dagX - g.laneX - 12;
    const colW = span / 60;
    const ptH = (l.gap * 0.42) / state.total_points;
    hist.forEach((col, i) => {
      const age = hist.length - 1 - i;
      const x = g.dagX - 12 - (age + 1) * colW;
      const a = Math.max(0.05, 0.9 * Math.pow(1 - age / 60, 1.4));
      const total = col.reduce((s, s2) => s + s2.points, 0);
      let off = -(total * ptH) / 2;
      for (const s of col) {
        ctx.fillStyle = rgba(NEON[s.model], a);
        ctx.fillRect(x, l.y + off, Math.max(1, colW - 1.5), s.points * ptH - 1);
        off += s.points * ptH;
      }
    });

    // title above the lane
    const dim = c.active ? 1 : 0.35;
    ctx.globalAlpha = dim;
    ctx.fillStyle = "rgba(160,190,220,0.7)";
    ctx.font = `600 ${Math.round(l.gap * 0.1)}px ${FONT}`;
    ctx.letterSpacing = "3px";
    const titleSize = Math.round(l.gap * 0.17), projSize = Math.round(l.gap * 0.1);
    const above = l.y - Math.max(half, 2) - 12;
    ctx.fillText(c.project.toUpperCase(), g.laneX, above - titleSize - 4);
    ctx.letterSpacing = "0px";
    ctx.fillStyle = "#eaf4ff";
    ctx.font = `600 ${titleSize}px ${FONT}`;
    ctx.fillText(c.title, g.laneX, above);
    // progress as a thin line under nothing but light
    const pw = Math.min(260, (g.dagX - g.laneX) * 0.4);
    ctx.fillStyle = "rgba(255,255,255,0.08)";
    const below = l.y + Math.max(half, 2) + 14;
    ctx.fillRect(g.laneX, below, pw, 2);
    ctx.save();
    ctx.shadowColor = STATE.done;
    ctx.shadowBlur = 8;
    ctx.fillStyle = STATE.done;
    ctx.fillRect(g.laneX, below, (pw * c.done) / c.total, 2);
    ctx.restore();
    ctx.fillStyle = "rgba(160,190,220,0.6)";
    ctx.font = `${Math.round(l.gap * 0.09)}px ${FONT}`;
    ctx.fillText(`${c.done}/${c.total}  ·  ${c.status}`, g.laneX + pw + 12, below + 4);
    ctx.globalAlpha = 1;

    drawGraph(g, l, now, dim);
  }

  function drawGraph(g, l, now, dim) {
    const c = l.hustle, t = now / 1000;
    const lay = layoutGraph(c.increments);
    const x0 = g.dagX + 18, x1 = g.endX;
    const colW = lay.layers > 1 ? (x1 - x0) / (lay.layers - 1) : 0;
    const rowH = Math.min(l.gap * 0.18, lay.maxY > lay.minY ? (l.gap * 0.7) / (lay.maxY - lay.minY) : 0);
    const mid = (lay.maxY + lay.minY) / 2;
    const pos = {};
    for (const i of c.increments) pos[i.id] = { x: x0 + lay.layer[i.id] * colW, y: l.y + (lay.y[i.id] - mid) * rowH };
    l.nodes = pos;
    const r = Math.max(3, l.gap * 0.035);
    // the lane flows on into the roots of the graph
    ctx.globalAlpha = dim;
    if (c.share > 0) {
      for (const i of c.increments.filter((i) => !i.after.length)) {
        const grad = ctx.createLinearGradient(g.dagX - 40, 0, pos[i.id].x, 0);
        grad.addColorStop(0, "rgba(120,80,255,0)");
        grad.addColorStop(1, "rgba(120,120,255,0.5)");
        ctx.strokeStyle = grad;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(g.dagX - 40, l.y);
        ctx.bezierCurveTo(g.dagX - 10, l.y, pos[i.id].x - 20, pos[i.id].y, pos[i.id].x, pos[i.id].y);
        ctx.stroke();
      }
    }
    for (const i of c.increments) for (const a of i.after) {
      ctx.strokeStyle = rgba(i.state === "open" ? "#5a6b80" : STATE[i.state], i.state === "open" ? 0.35 : 0.75);
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(pos[a].x, pos[a].y);
      ctx.lineTo(pos[i.id].x, pos[i.id].y);
      ctx.stroke();
    }
    for (const i of c.increments) {
      const p = pos[i.id];
      let color = STATE[i.state], glow = 6, rr = r;
      if (i.state === "running") {
        color = NEON[i.model];
        const beat = reduced ? 0.5 : 0.5 + 0.5 * Math.sin(t * Math.PI * 2 * { haiku: 1.1, sonnet: 0.7, opus: 0.45, fable: 0.3 }[i.model]);
        glow = 14 + 14 * beat;
        rr = r * (1.2 + 0.3 * beat);
      }
      if (i.state === "blocked" && !reduced && Math.random() < 0.1) color = "#300";
      if (i.state === "open") glow = 0;
      ctx.save();
      ctx.shadowColor = color;
      ctx.shadowBlur = glow;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(p.x, p.y, rr, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    ctx.globalAlpha = 1;
  }

  function drawCore(g, now) {
    const { x, y, r: R } = g.core, t = now / 1000;
    const used = state.points_in_use;
    const glow = 0.25 + 0.75 * Math.min(1, state.total_points / 40);
    const beat = reduced ? 0 : Math.sin(t * Math.PI * 2 * (0.35 + used / 28));
    const r = R * (1 + 0.04 * beat);
    const halo = ctx.createRadialGradient(x, y, r * 0.3, x, y, r * 3.2);
    halo.addColorStop(0, `rgba(63,169,255,${0.55 * glow})`);
    halo.addColorStop(0.35, `rgba(70,90,255,${0.18 * glow})`);
    halo.addColorStop(1, "rgba(40,0,120,0)");
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(x, y, r * 3.2, 0, Math.PI * 2);
    ctx.fill();
    const orb = ctx.createRadialGradient(x - r * 0.3, y - r * 0.35, r * 0.05, x, y, r);
    orb.addColorStop(0, `rgba(255,255,255,${0.6 + 0.4 * glow})`);
    orb.addColorStop(0.3, `rgba(120,220,255,${0.4 + 0.6 * glow})`);
    orb.addColorStop(0.75, `rgba(40,80,255,${0.4 + 0.5 * glow})`);
    orb.addColorStop(1, "rgba(30,10,120,0.3)");
    ctx.fillStyle = orb;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
    if (!reduced) {
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      for (let i = 0; i < 7; i++) {
        ctx.strokeStyle = `rgba(150,230,255,${0.06 + 0.1 * glow})`;
        ctx.lineWidth = 1.3;
        ctx.beginPath();
        ctx.ellipse(x, y, r * (0.5 + 0.07 * i), r * (0.15 + 0.05 * i), t * (0.25 + i * 0.11) + i, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
    }
    const ring = (rad, pct, color, w) => {
      ctx.lineCap = "round";
      ctx.lineWidth = w;
      ctx.strokeStyle = "rgba(255,255,255,0.06)";
      ctx.beginPath();
      ctx.arc(x, y, rad, 0, Math.PI * 2);
      ctx.stroke();
      ctx.save();
      ctx.shadowColor = color;
      ctx.shadowBlur = 16;
      ctx.strokeStyle = color;
      ctx.beginPath();
      ctx.arc(x, y, rad, -Math.PI / 2, -Math.PI / 2 + (Math.PI * 2 * Math.min(100, pct)) / 100);
      ctx.stroke();
      ctx.restore();
    };
    const hot = (p, base) => (p >= 90 ? STATE.blocked : p >= 75 ? "#ffb300" : base);
    ring(R * 1.18, state.usage.session.percent, hot(state.usage.session.percent, "#00d0ff"), 4);
    ring(R * 1.34, state.usage.week.percent, hot(state.usage.week.percent, "#b46bff"), 2.5);
    ctx.textAlign = "center";
    ctx.fillStyle = "#ffffff";
    ctx.save();
    ctx.shadowColor = "#3fa9ff";
    ctx.shadowBlur = 18;
    ctx.font = `700 ${Math.round(R * 0.55)}px ${FONT}`;
    ctx.fillText(`${Math.round(state.usage.session.percent)}%`, x, y + R * 0.12);
    ctx.restore();
    ctx.font = `600 ${Math.round(R * 0.13)}px ${FONT}`;
    ctx.letterSpacing = "3px";
    ctx.fillStyle = "rgba(200,230,255,0.8)";
    ctx.fillText("5H SESSION", x, y + R * 0.38);
    ctx.letterSpacing = "0px";
    ctx.fillStyle = "rgba(180,107,255,0.9)";
    ctx.font = `${Math.round(R * 0.14)}px ${FONT}`;
    ctx.fillText(`week ${Math.round(state.usage.week.percent)}%`, x, y + R * 0.58);
    ctx.fillStyle = "rgba(160,190,220,0.6)";
    ctx.fillText(`${state.points_in_use} / ${state.total_points} points burning`, x, y + R * 1.7);
    ctx.textAlign = "left";
  }

  function drawSparks(g, dt) {
    const byId = new Map(g.lanes.map((l) => [l.hustle.id, l]));
    if (!reduced) {
      for (const l of g.lanes) {
        if (!(l.hustle.share > 0)) continue;
        for (const s of l.hustle.sessions) {
          if (Math.random() < dt * s.points * 1.8) {
            sparks.push({ hustle: l.hustle.id, model: s.model, target: s.increment, t: 0, v: 0.22 + Math.random() * 0.12, off: (Math.random() - 0.5) * 0.8 });
          }
        }
      }
    }
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (let i = sparks.length - 1; i >= 0; i--) {
      const s = sparks[i];
      const l = byId.get(s.hustle);
      s.t += s.v * dt;
      const node = l && l.nodes && l.nodes[s.target];
      if (!l || !node || s.t >= 1.25) {
        sparks.splice(i, 1);
        continue;
      }
      let p;
      if (s.t <= 1) {
        p = beamPoint(g, l, s.t);
        p.y += s.off * l.thick * 0.5 * Math.min(1, s.t * 4);
      } else {
        const u = (s.t - 1) / 0.25, a = beamPoint(g, l, 1);
        p = { x: a.x + (node.x - a.x) * u, y: a.y + s.off * l.thick * 0.5 * (1 - u) + (node.y - a.y) * u };
      }
      const g2 = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, 7);
      g2.addColorStop(0, "#ffffff");
      g2.addColorStop(0.3, NEON[s.model]);
      g2.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g2;
      ctx.globalAlpha = Math.min(1, s.t * 6);
      ctx.fillRect(p.x - 7, p.y - 7, 14, 14);
    }
    ctx.restore();
  }

  function frame(now) {
    requestAnimationFrame(frame);
    if (!state) return;
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    const dpr = window.devicePixelRatio || 1;
    const W = innerWidth, H = innerHeight;
    if (canvas.width !== Math.round(W * dpr)) {
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);
    // OLED: drift the whole scene a few pixels over minutes so nothing burns in
    const t = now / 1000;
    ctx.translate(Math.sin(t / 290) * 6, Math.cos(t / 370) * 5);
    const g = geometry(W, H);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const l of g.lanes) if (l.hustle.share > 0) drawLaneBeamOnly(g, l);
    ctx.restore();
    for (const l of g.lanes) drawLane(g, l, now);
    drawSparks(g, dt);
    drawCore(g, now);
    ctx.font = `600 12px ${FONT}`;
    ctx.letterSpacing = "4px";
    ctx.fillStyle = "rgba(120,140,170,0.45)";
    ctx.fillText("HUSTLEBOT", 28, 34);
    ctx.letterSpacing = "0px";
  }

  // soft outer glow under every live beam
  function drawLaneBeamOnly(g, l) {
    ctx.save();
    ctx.shadowColor = "#3fa9ff";
    ctx.shadowBlur = 30;
    beamShape(g, l, Math.max(1, l.thick / 2));
    ctx.fillStyle = "rgba(63,169,255,0.06)";
    ctx.fill();
    ctx.restore();
  }

  requestAnimationFrame(frame);
})();
