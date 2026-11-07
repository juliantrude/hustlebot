"use strict";
// The curved threshold sliders on the core's rings (ADR 0008): the knob on the inner ring is the 5h stop, above which
// nothing new starts; the knob on the outer ring is the usage the week should reach at its reset. The red arc past
// the 5h stop is where nothing starts; the white tick on the outer ring is where the weekly target line stands now.
// Drag a knob around its ring, or focus it and use the arrow keys.

const Thresholds = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const SIZE = 200, C = SIZE / 2; // the overlay is centred on the core
  const RING = Flow.rings;
  const SETTING = { session: "session_threshold", week: "week_target" };
  const LABEL = { session: "5h stop", week: "week target at reset" };
  const STEP = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 5, PageDown: -5 };

  let commit = () => {};
  let state = null;
  const value = { session: 0, week: 0 };
  let dragging = null; // ring whose knob is held; the polled state must not move it meanwhile
  let pending = null; // keyboard changes wait a moment, so holding an arrow key sends one request

  const el = (tag, attrs, parent) => {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (parent) parent.append(node);
    return node;
  };
  const point = (r, pct) => {
    const a = -Math.PI / 2 + (Math.PI * 2 * pct) / 100;
    return [C + r * Math.cos(a), C + r * Math.sin(a)];
  };
  const arc = (r, from, to) => {
    const [x0, y0] = point(r, from), [x1, y1] = point(r, Math.min(to, 99.99));
    return `M${x0} ${y0} A${r} ${r} 0 ${to - from > 50 ? 1 : 0} 1 ${x1} ${y1}`;
  };

  const svg = el("svg", { class: "thresholds", viewBox: `0 0 ${SIZE} ${SIZE}` });
  const stopArc = el("path", { class: "stop-zone" }, svg);
  const lineTick = el("line", { class: "line-now" }, svg);
  const knobs = {}, titles = {};
  for (const ring of ["session", "week"]) {
    const knob = el("g", { class: `knob ${ring}`, role: "slider", tabindex: "0", "aria-label": LABEL[ring],
                           "aria-valuemin": "0", "aria-valuemax": "100" }, svg);
    el("circle", { r: 12, class: "hit" }, knob);
    el("circle", { r: 6, class: "dot" }, knob);
    titles[ring] = el("title", {}, knob);
    knob.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      knob.setPointerCapture(e.pointerId);
      knob.focus();
      dragging = ring;
      knob.classList.add("held");
    });
    knob.addEventListener("pointermove", (e) => {
      if (dragging !== ring) return;
      const box = svg.getBoundingClientRect();
      const dx = e.clientX - (box.left + box.width / 2), dy = e.clientY - (box.top + box.height / 2);
      let pct = Math.round((((Math.atan2(dx, -dy) + Math.PI * 2) % (Math.PI * 2)) / (Math.PI * 2)) * 100);
      // across the top the angle wraps; stay at the end that was reached instead of jumping to the other
      if (value[ring] > 75 && pct < 25) pct = 100;
      if (value[ring] < 25 && pct > 75) pct = 0;
      value[ring] = pct;
      draw();
    });
    const release = () => {
      if (dragging !== ring) return;
      dragging = null;
      knob.classList.remove("held");
      send(ring);
    };
    knob.addEventListener("pointerup", release);
    knob.addEventListener("pointercancel", release);
    knob.addEventListener("keydown", (e) => {
      if (!(e.key in STEP)) return;
      e.preventDefault();
      value[ring] = Math.max(0, Math.min(100, value[ring] + STEP[e.key]));
      draw();
      clearTimeout(pending);
      pending = setTimeout(() => send(ring), 400);
    });
    knobs[ring] = knob;
  }
  document.getElementById("core").append(svg);

  function send(ring) {
    pending = null;
    if (state && value[ring] !== state.thresholds[ring]) commit({ [SETTING[ring]]: value[ring] });
  }

  function draw() {
    for (const ring of ["session", "week"]) {
      const [x, y] = point(RING[ring], value[ring]);
      const knob = knobs[ring];
      knob.setAttribute("transform", `translate(${x} ${y})`);
      knob.setAttribute("aria-valuenow", value[ring]);
      knob.setAttribute("aria-valuetext", `${value[ring]} %`);
      titles[ring].textContent = `${LABEL[ring]}: ${value[ring]} %`;
    }
    stopArc.setAttribute("d", value.session < 100 ? arc(RING.session, value.session, 100) : "");
    const line = state && state.usage.week.line;
    lineTick.style.display = line === undefined || line === null ? "none" : "";
    if (lineTick.style.display === "") {
      const [x0, y0] = point(RING.week - 5, line), [x1, y1] = point(RING.week + 5, line);
      Object.entries({ x1: x0, y1: y0, x2: x1, y2: y1 }).forEach(([k, v]) => lineTick.setAttribute(k, v));
    }
    const text = document.getElementById("thresholdText");
    const now = line === undefined || line === null ? "" : `, line now ${Math.round(line)} %`;
    text.textContent = `5h stop ${value.session} % · week ${value.week} % at reset${now}`;
    text.classList.toggle("held", dragging !== null || pending !== null);
  }

  /** The polled state; a knob that is held or has a change on its way keeps its own value. */
  function update(next) {
    state = next;
    for (const ring of ["session", "week"]) if (dragging !== ring && pending === null) value[ring] = next.thresholds[ring];
    draw();
  }

  return { update, init: (fn) => (commit = fn) };
})();
