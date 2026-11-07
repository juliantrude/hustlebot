"use strict";
// The load strip of a hustle card: one LED segment per session point, coloured by model. The column on the right is
// now; to its left the last minutes scroll away and fade. The dashed line is the hustle's share, segments above it
// are borrowed points. Sessions ignite segment by segment when they start and flash green when they finish.

const Load = (() => {
  const MODEL = { haiku: "#4cc9d8", sonnet: "#d8a548", opus: "#a78bfa", fable: "#e46fb8" };
  const PULSE_S = { haiku: 0.9, sonnet: 1.5, opus: 2.3, fable: 3.2 }; // heavier models breathe slower
  const EMPTY = "#1c2230";
  const DONE = "#5ff0a0";
  const BLOCKED = "#ff6b6b";
  const LIVE_W = 22; // width of the "now" column
  const GAP = 8; // between trail and now
  const IGNITE_MS = 70; // per segment
  const FLASH_MS = 900;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  let total = 20;
  let tickSeconds = 2;
  let tickAt = performance.now(); // when the last tick arrived
  let lastTick = -1;
  const strips = new Map(); // hustle id -> { canvas, hustle, seen: Map(key -> first seen ms), flashes: [] }

  const key = (s) => `${s.increment}@${s.started}`;

  function global(state) {
    total = state.total_points;
    tickSeconds = state.tick_seconds;
    if (state.tick !== lastTick) {
      lastTick = state.tick;
      tickAt = performance.now();
    }
  }

  function update(id, canvas, hustle) {
    let strip = strips.get(id);
    if (!strip) {
      strip = { canvas, hustle, seen: new Map(), flashes: [], first: true };
      strips.set(id, strip);
    }
    const now = performance.now();
    const keys = new Set(hustle.sessions.map(key));
    // finished: sessions that were running and are gone
    for (const [k, info] of strip.seen) {
      if (!keys.has(k)) {
        strip.flashes.push({ at: now, from: info.from, points: info.points });
        strip.seen.delete(k);
      }
    }
    // started: sessions we have not seen (on the first data they are just there, no ignition)
    let from = 0;
    for (const s of [...hustle.sessions].sort((a, b) => a.started - b.started)) {
      const k = key(s);
      if (!strip.seen.has(k)) strip.seen.set(k, { at: strip.first ? -1e9 : now });
      Object.assign(strip.seen.get(k), { from, points: s.points });
      from += s.points;
    }
    strip.first = false;
    strip.hustle = hustle;
    strip.canvas = canvas;
  }

  function sizeFor(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }

  function draw(strip, now) {
    const { ctx, w, h } = sizeFor(strip.canvas);
    const hustle = strip.hustle;
    ctx.clearRect(0, 0, w, h);
    const seg = (h - 2) / total; // height of one point
    const yOf = (p) => h - 1 - p * seg; // bottom of point p
    const trailW = w - LIVE_W - GAP;
    const cols = 60;
    const colW = trailW / cols;
    const scroll = reduced ? 0 : Math.min(1, (now - tickAt) / (tickSeconds * 1000)) * colW;
    const t = now / 1000;
    const idle = !hustle.active || hustle.status === "done";

    // trail: one column per minute, oldest left, fading with age
    const hist = hustle.history;
    hist.forEach((column, i) => {
      const age = hist.length - 1 - i; // 0 = newest
      const x = trailW - (age + 1) * colW - scroll + colW;
      if (x + colW < 0) return;
      const alpha = Math.max(0.06, 0.75 * Math.pow(1 - age / cols, 1.6));
      let p = 0;
      for (const s of column) {
        ctx.fillStyle = MODEL[s.model];
        ctx.globalAlpha = alpha;
        ctx.fillRect(x, yOf(p + s.points) + 0.5, Math.max(1, colW - 1), s.points * seg - 1);
        p += s.points;
      }
    });
    ctx.globalAlpha = 1;
    // fade the trail into the card on the left
    const fade = ctx.createLinearGradient(0, 0, 40, 0);
    fade.addColorStop(0, "rgba(23,26,33,1)");
    fade.addColorStop(1, "rgba(23,26,33,0)");
    ctx.fillStyle = fade;
    ctx.fillRect(0, 0, 40, h);

    // now column: every point a segment, empty ones dim
    const x0 = w - LIVE_W;
    for (let p = 0; p < total; p++) {
      ctx.fillStyle = EMPTY;
      ctx.fillRect(x0, yOf(p + 1) + 0.5, LIVE_W, seg - 1);
    }
    for (const s of [...hustle.sessions].sort((a, b) => a.started - b.started)) {
      const info = strip.seen.get(key(s));
      if (!info) continue;
      const since = now - info.at;
      const pulse = reduced || idle ? 1 : 0.78 + 0.22 * Math.sin((t * 2 * Math.PI) / PULSE_S[s.model]);
      for (let i = 0; i < s.points; i++) {
        const lit = reduced ? 1 : Math.min(1, Math.max(0, (since - i * IGNITE_MS) / IGNITE_MS));
        if (lit <= 0) continue;
        const igniting = lit < 1 || since < (i + 3) * IGNITE_MS;
        ctx.save();
        ctx.shadowColor = MODEL[s.model];
        ctx.shadowBlur = igniting ? 16 : 7 * pulse;
        ctx.globalAlpha = lit * pulse;
        ctx.fillStyle = igniting ? "#ffffff" : MODEL[s.model];
        ctx.fillRect(x0, yOf(info.from + i + 1) + 0.5, LIVE_W, seg - 1);
        ctx.restore();
      }
    }
    // finished sessions flash green and fade
    strip.flashes = strip.flashes.filter((f) => now - f.at < FLASH_MS);
    for (const f of strip.flashes) {
      const k = 1 - (now - f.at) / FLASH_MS;
      ctx.save();
      ctx.shadowColor = DONE;
      ctx.shadowBlur = 18 * k;
      ctx.globalAlpha = k;
      ctx.fillStyle = DONE;
      ctx.fillRect(x0 - 2, yOf(f.from + f.points), LIVE_W + 4, f.points * seg);
      ctx.restore();
    }

    // share: dashed line; points above it are borrowed from hustles that cannot use them
    if (hustle.share > 0) {
      const y = Math.round(yOf(hustle.share)) + 0.5;
      ctx.save();
      ctx.strokeStyle = "rgba(200,208,220,0.55)";
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
      ctx.restore();
      const used = hustle.points_in_use;
      if (used > hustle.share) {
        ctx.save();
        ctx.strokeStyle = "#f0b74d";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(x0 - 2.5, yOf(used) - 0.5, LIVE_W + 5, (used - hustle.share) * seg);
        ctx.restore();
      }
    }

    // a hard-blocked hustle flickers red at the base of now
    if (hustle.status === "blocked") {
      const on = reduced || Math.random() > 0.12;
      ctx.save();
      ctx.globalAlpha = on ? 0.55 + 0.35 * Math.abs(Math.sin(t * 5)) : 0.1;
      ctx.shadowColor = BLOCKED;
      ctx.shadowBlur = 10;
      ctx.fillStyle = BLOCKED;
      ctx.fillRect(x0, h - 3, LIVE_W, 2);
      ctx.restore();
    }
    // inactive hustles breathe faintly
    strip.canvas.style.opacity = idle && !reduced ? String(0.55 + 0.15 * Math.sin(t * 1.2)) : "";
  }

  function frame(now) {
    for (const strip of strips.values()) if (strip.canvas.isConnected) draw(strip, now);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return { global, update };
})();
