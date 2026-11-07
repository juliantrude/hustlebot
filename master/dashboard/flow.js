"use strict";
// The energy core: one pulsing orb for the user's subscription, beams flowing out of it into the active hustles.
// The orb's brightness follows the total session points (how fast the session burns), its rings the 5h (inner) and
// week (outer) usage. Each beam is as thick as the hustle's share of the points, like a Sankey diagram; sparks run
// along it in the colours of the models that are running in that hustle right now.

const Flow = (() => {
  const MODEL = { haiku: "#4cc9d8", sonnet: "#d8a548", opus: "#a78bfa", fable: "#e46fb8" };
  const CORE = { x: 150, r: 52 }; // centre x and radius of the orb: far enough in for the halo (2.6 r) to fit
  const MAX_POINTS = 40; // points at which the core burns at full brightness
  const TRUNK_MAX = 96; // beam thickness at the core when all points are shared out
  const RINGS = { session: CORE.r + 9, week: CORE.r + 17 }; // radii of the usage rings, which carry the sliders
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  let state = null;
  let cards = new Map();
  const sparks = []; // { hustle, model, t: 0..1, speed }
  let lastFrame = performance.now();

  function update(next, cardMap) {
    state = next;
    cards = cardMap;
  }

  const canvas = document.getElementById("flow");
  const col = canvas.parentElement;
  const coreEl = document.getElementById("core");
  const pointsEl = col.querySelector(".core-points");

  function size() {
    const dpr = window.devicePixelRatio || 1;
    // the beams end at the cards, which sit right of the column: draw a little into the gap
    const w = col.clientWidth, h = col.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }

  /** Beams: one per hustle with a share, stacked at the core in card order, each ending at its card's left edge. */
  function beams(h, w) {
    const top = col.getBoundingClientRect().top;
    const list = state.hustles
      .filter((c) => (c.share > 0 || (c.active && c.status === "blocked")) && cards.get(c.id))
      .map((c) => {
        const r = cards.get(c.id).getBoundingClientRect();
        return { hustle: c, y: r.top - top + r.height / 2, cardH: r.height };
      })
      .sort((a, b) => a.y - b.y);
    const perPoint = TRUNK_MAX / state.total_points;
    const cy = coreY(h);
    const trunk = list.reduce((s, b) => s + b.hustle.share * perPoint, 0);
    let y = cy - trunk / 2;
    for (const b of list) {
      b.dead = !(b.hustle.share > 0); // active but blocked: a hairline that carries nothing
      b.thick = b.dead ? 1.5 : Math.max(2, b.hustle.share * perPoint);
      b.y0 = y + b.thick / 2;
      y += b.thick;
      b.x0 = CORE.x; // from the centre: the orb covers where the beams are born
      b.x1 = w;
    }
    return list;
  }

  function coreY(h) {
    return Math.max(CORE.r + 20, Math.min(h - CORE.r - 70, h / 2));
  }

  // point on a beam's centre line, t in 0..1 (cubic bezier with horizontal tangents)
  function along(b, t) {
    const mx = (b.x0 + b.x1) / 2, u = 1 - t;
    const x = u * u * u * b.x0 + 3 * u * u * t * mx + 3 * u * t * t * mx + t * t * t * b.x1;
    const y = u * u * u * b.y0 + 3 * u * u * t * b.y0 + 3 * u * t * t * b.y + t * t * t * b.y;
    return { x, y };
  }

  function beamPath(ctx, b) {
    const mx = (b.x0 + b.x1) / 2, h0 = b.thick / 2, h1 = b.thick / 2;
    ctx.beginPath();
    ctx.moveTo(b.x0, b.y0 - h0);
    ctx.bezierCurveTo(mx, b.y0 - h0, mx, b.y - h1, b.x1, b.y - h1);
    ctx.lineTo(b.x1, b.y + h1);
    ctx.bezierCurveTo(mx, b.y + h1, mx, b.y0 + h0, b.x0, b.y0 + h0);
    ctx.closePath();
  }

  function drawCore(ctx, cy, now, glow) {
    const t = now / 1000;
    const used = state.points_in_use;
    // beats faster the more points burn right now
    const beat = reduced ? 0 : Math.sin(t * Math.PI * 2 * (0.4 + used / 25));
    const r = CORE.r * (1 + 0.035 * beat);
    // halo
    const halo = ctx.createRadialGradient(CORE.x, cy, r * 0.4, CORE.x, cy, r * 2.6);
    halo.addColorStop(0, `rgba(110,190,255,${0.45 * glow})`);
    halo.addColorStop(0.4, `rgba(80,140,255,${0.16 * glow})`);
    halo.addColorStop(1, "rgba(60,100,255,0)");
    ctx.fillStyle = halo;
    ctx.fillRect(CORE.x - r * 2.6, cy - r * 2.6, r * 5.2, r * 5.2);
    // orb
    const orb = ctx.createRadialGradient(CORE.x - r * 0.25, cy - r * 0.3, r * 0.05, CORE.x, cy, r);
    orb.addColorStop(0, `rgba(255,255,255,${0.55 + 0.45 * glow})`);
    orb.addColorStop(0.35, `rgba(140,210,255,${0.35 + 0.55 * glow})`);
    orb.addColorStop(0.8, `rgba(40,90,200,${0.35 + 0.4 * glow})`);
    orb.addColorStop(1, "rgba(20,40,110,0.2)");
    ctx.fillStyle = orb;
    ctx.beginPath();
    ctx.arc(CORE.x, cy, r, 0, Math.PI * 2);
    ctx.fill();
    // swirling filaments inside
    if (!reduced) {
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      for (let i = 0; i < 5; i++) {
        const a = t * (0.3 + i * 0.13) + i * 1.3;
        ctx.strokeStyle = `rgba(170,225,255,${0.08 + 0.1 * glow})`;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.ellipse(CORE.x, cy, r * (0.55 + 0.08 * i), r * (0.18 + 0.05 * i), a, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
    }
    // usage rings: 5h inside, week outside, filling clockwise from the top
    const ring = (radius, pct, color, width) => {
      ctx.lineCap = "round";
      ctx.lineWidth = width;
      ctx.strokeStyle = "rgba(255,255,255,0.07)";
      ctx.beginPath();
      ctx.arc(CORE.x, cy, radius, 0, Math.PI * 2);
      ctx.stroke();
      ctx.save();
      ctx.shadowColor = color;
      ctx.shadowBlur = 10;
      ctx.strokeStyle = color;
      ctx.beginPath();
      ctx.arc(CORE.x, cy, radius, -Math.PI / 2, -Math.PI / 2 + (Math.PI * 2 * Math.min(100, pct)) / 100);
      ctx.stroke();
      ctx.restore();
    };
    const hot = (p) => (p >= 90 ? "#ff6b6b" : p >= 75 ? "#f0b74d" : null);
    ring(RINGS.session, state.usage.session.percent, hot(state.usage.session.percent) || "#7fd0ff", 3);
    ring(RINGS.week, state.usage.week.percent, hot(state.usage.week.percent) || "#a78bfa", 2);
  }

  function frame(now) {
    requestAnimationFrame(frame);
    if (!state) return;
    const dt = Math.min(0.1, (now - lastFrame) / 1000);
    lastFrame = now;
    const { ctx, w, h } = size();
    ctx.clearRect(0, 0, w, h);
    const cy = coreY(h);
    coreEl.style.top = `${cy}px`;
    pointsEl.style.top = `${cy + CORE.r + 30}px`;
    const glow = Math.min(1, state.total_points / MAX_POINTS) * 0.8 + 0.2;
    const list = beams(h, w);

    // beams: glowing ribbons, brighter where points are actually burning
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const b of list) {
      if (b.dead) {
        const flick = reduced || Math.random() > 0.1;
        beamPath(ctx, b);
        ctx.fillStyle = `rgba(255,107,107,${flick ? 0.45 : 0.08})`;
        ctx.fill();
        continue;
      }
      const burning = Math.min(1, b.hustle.points_in_use / Math.max(1, b.hustle.share));
      const grad = ctx.createLinearGradient(b.x0, 0, b.x1, 0);
      const edge = Math.min(0.9, (CORE.r * 1.1) / (b.x1 - b.x0));
      grad.addColorStop(0, "rgba(120,200,255,0)");
      grad.addColorStop(edge * 0.7, "rgba(120,200,255,0)");
      grad.addColorStop(edge, `rgba(120,200,255,${0.35 * glow})`);
      grad.addColorStop(1, `rgba(90,150,255,${0.08 + 0.22 * burning})`);
      beamPath(ctx, b);
      ctx.fillStyle = grad;
      ctx.fill();
      // bright filament along the middle
      ctx.strokeStyle = `rgba(190,230,255,${0.12 + 0.35 * burning})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let i = 0; i <= 24; i++) {
        const p = along(b, i / 24);
        i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y);
      }
      ctx.stroke();
    }
    ctx.restore();

    // sparks: per running session, a stream in the model's colour; denser for more points
    if (!reduced) {
      for (const b of list) {
        for (const s of b.hustle.sessions) {
          if (!b.dead && Math.random() < dt * s.points * 1.6) sparks.push({ hustle: b.hustle.id, model: s.model, t: 0, speed: 0.35 + Math.random() * 0.25, off: (Math.random() - 0.5) * 0.8 });
        }
      }
      const byId = new Map(list.map((b) => [b.hustle.id, b]));
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      for (let i = sparks.length - 1; i >= 0; i--) {
        const s = sparks[i];
        const b = byId.get(s.hustle);
        s.t += s.speed * dt;
        if (!b || b.dead || s.t >= 1) {
          sparks.splice(i, 1);
          continue;
        }
        const p = along(b, s.t);
        const y = p.y + s.off * b.thick * 0.5;
        const g = ctx.createRadialGradient(p.x, y, 0, p.x, y, 6);
        g.addColorStop(0, "#ffffff");
        g.addColorStop(0.3, MODEL[s.model]);
        g.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = g;
        ctx.globalAlpha = Math.sin(Math.PI * s.t);
        ctx.fillRect(p.x - 6, y - 6, 12, 12);
      }
      ctx.restore();
    }

    drawCore(ctx, cy, now, glow);
  }
  requestAnimationFrame(frame);

  return { update, rings: RINGS };
})();
