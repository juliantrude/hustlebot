"use strict";
// Dashboard of the hustlemaster: hustle cards, sorted active first and by weight. Data from /api/state.

const $ = (id) => document.getElementById(id);
const STATUS = { running: "running", waiting: "waiting", blocked: "blocked", paused: "inactive", stopping: "finishing", done: "done" };
// hustle id -> card element. A card stays where it is in the DOM for good: moving an iframe reloads it, so the order
// on screen comes from CSS `order` alone.
const cards = new Map();

// the login cookie expired or the token file was replaced: log in again and come back here
function toLogin() {
  location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (res.status === 401) {
    toLogin();
    return new Promise(() => {}); // nothing to render while the browser leaves
  }
  return res.json();
}

function graphUrl(id, mini) {
  const data = encodeURIComponent(`/api/hustles/${id}/graph.json`);
  return `/engine/?data=${data}${mini ? "&view=mini" : ""}`;
}

function card(hustle) {
  let node = cards.get(hustle.id);
  if (!node) {
    node = $("hustleCard").content.firstElementChild.cloneNode(true);
    node.querySelector(".active").addEventListener("change", (e) => api(`/api/hustles/${hustle.id}`, { active: e.target.checked }).then(render));
    node.querySelectorAll("[data-weight]").forEach((b) =>
      b.addEventListener("click", () => {
        const w = Number(node.dataset.weight) + Number(b.dataset.weight);
        if (w >= 1) api(`/api/hustles/${hustle.id}`, { weight: w }).then(render);
      })
    );
    node.querySelector(".switch").addEventListener("click", (e) => e.stopPropagation());
    node.querySelector(".weight").addEventListener("click", (e) => e.stopPropagation());
    node.addEventListener("click", () => navigate(`/hustles/${hustle.id}`));
    cards.set(hustle.id, node);
    $("hustles").append(node);
  }
  // the preview reloads only when an increment changed state: reloading an iframe flickers
  if (node.dataset.graph !== hustle.graph_version) {
    node.dataset.graph = hustle.graph_version;
    const frame = node.querySelector(".preview iframe");
    // replace(), not src: a new src would add an entry to the browser history
    if (frame.contentWindow && frame.getAttribute("src")) frame.contentWindow.location.replace(graphUrl(hustle.id, true));
    else frame.src = graphUrl(hustle.id, true);
  }
  node.dataset.weight = hustle.weight;
  node.dataset.project = hustle.project;
  node.dataset.title = hustle.title;
  node.classList.toggle("inactive", !hustle.active);
  node.querySelector(".active").checked = hustle.active;
  node.querySelector(".w").textContent = `${hustle.weight} fr`;
  node.querySelector(".share").textContent = hustle.share ? `${hustle.points_in_use}/${hustle.share} pts` : "no share";
  const status = node.querySelector(".status");
  status.className = `status ${hustle.status}`;
  status.textContent = STATUS[hustle.status] || hustle.status;
  node.querySelector(".project").textContent = hustle.project;
  node.querySelector(".title").textContent = hustle.title;
  node.querySelector(".progress i").style.width = `${(100 * hustle.done) / hustle.total}%`;
  node.querySelector(".count").textContent = `${hustle.done} / ${hustle.total} increments`;
  const load = node.querySelector(".load");
  load.title = hustle.sessions.length
    ? hustle.sessions.map((s) => `${s.increment} · ${s.model} · ${s.points} pts`).join("\n")
    : "no session running";
  Load.update(hustle.id, load, hustle);
  return node;
}

function renderApprovals(list, hustles) {
  const title = new Map(hustles.map((c) => [c.id, `${c.project} · ${c.title}`]));
  $("approvals").hidden = list.length === 0;
  $("approvalList").replaceChildren(
    ...list.map((a) => {
      const li = document.createElement("li");
      const t = document.createElement("span");
      t.className = "t";
      t.textContent = `${title.get(a.hustle) || a.hustle}: ${a.text}`;
      li.append(t);
      for (const [cls, label] of [["yes", "Approve"], ["no", "Reject"]]) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = cls;
        b.textContent = label;
        b.addEventListener("click", () => api(`/api/approvals/${a.id}`, { decision: cls }).then(render));
        li.append(b);
      }
      return li;
    })
  );
}

function renderAutoApproved(list, hustles) {
  const title = new Map(hustles.map((c) => [c.id, `${c.project} · ${c.title}`]));
  $("autoApproved").hidden = list.length === 0;
  $("autoApprovedList").replaceChildren(
    ...list.map((a) => {
      const li = document.createElement("li");
      const t = document.createElement("span");
      t.className = "t";
      t.textContent = `${title.get(a.hustle) || a.hustle}: ${a.text}`;
      const when = document.createElement("span");
      when.className = "muted";
      when.textContent = a.when;
      li.append(t, when);
      if (a.revocable) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "revoke";
        b.textContent = "Revoke";
        b.addEventListener("click", () => api(`/api/approvals/${a.id}`, { decision: "revoke" }).then(render));
        li.append(b);
      } else {
        li.classList.add("settled");
        li.title = "a later confirmation built on it";
      }
      return li;
    })
  );
}

function renderSettings(settings) {
  $("allowFable").checked = settings.allow_fable;
  const modes = $("autoApprove");
  if (modes.childElementCount === 0) {
    for (const mode of settings.auto_approve_modes) {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.dataset.mode = mode;
      b.textContent = mode;
      b.addEventListener("click", () => api("/api/settings", { auto_approve: mode }).then(render));
      modes.append(b);
    }
  }
  for (const b of modes.children) b.setAttribute("aria-checked", String(b.dataset.mode === settings.auto_approve));
  $("growth").hidden = settings.auto_approve !== "limited";
  $("growthValue").textContent = `${settings.auto_approve_growth} %`;
  $("growthValue").dataset.value = settings.auto_approve_growth;
}

function render(state) {
  Load.global(state);
  $("corePct").textContent = `${Math.round(state.usage.session.percent)}%`;
  $("coreWeek").textContent = `week ${Math.round(state.usage.week.percent)}%`;
  $("core").title = `5h session ${state.usage.session.percent}%, resets ${state.usage.session.resets}\nweek ${state.usage.week.percent}%, resets ${state.usage.week.resets}`;
  $("points").textContent = state.total_points;
  $("pointsText").textContent = `${state.points_in_use} of ${state.total_points} session points burning`;
  renderApprovals(state.approvals, state.hustles);
  renderAutoApproved(state.auto_approved, state.hustles);
  renderSettings(state.settings);
  state.hustles.forEach((hustle, index) => (card(hustle).style.order = index));
  titles = new Map(state.hustles.map((c) => [c.id, `${c.project} · ${c.title}`]));
  route();
  Flow.update(state, cards);
  Thresholds.update(state);
}

// --- routes: / is the list, /hustles/<id> the hustle's graph full screen --------------------------------

let titles = new Map();
let shown = null; // hustle id whose graph is open

function navigate(path) {
  history.pushState(null, "", path);
  route();
}

function route() {
  const match = location.pathname.match(/^\/hustles\/([\w-]+)$/);
  const id = match ? match[1] : null;
  $("overlay").hidden = !id;
  document.title = id ? `${titles.get(id) || id} · hustlebot` : "hustlebot";
  if (id) $("overlayTitle").textContent = titles.get(id) || id;
  if (id === shown) return;
  shown = id;
  // A fresh iframe each time: navigating an existing one would add entries to the browser history.
  $("overlayFrame").replaceWith(graphFrame(id));
}

function graphFrame(id) {
  const frame = document.createElement("iframe");
  frame.id = "overlayFrame";
  frame.title = "Hustle graph";
  if (!id) return frame;
  frame.src = graphUrl(id, false);
  // Escape inside the graph page (same origin) closes it too
  frame.addEventListener("load", () => frame.contentWindow.addEventListener("keydown", closeOnEscape));
  return frame;
}

function closeOnEscape(e) {
  if (e.key === "Escape" && shown) navigate("/");
}

window.addEventListener("popstate", route);
$("overlayClose").addEventListener("click", () => navigate("/"));
document.addEventListener("keydown", closeOnEscape);
document.querySelectorAll("[data-points]").forEach((b) =>
  b.addEventListener("click", () => api("/api/settings", { total_points: Number($("points").textContent) + Number(b.dataset.points) }).then(render))
);

$("allowFable").addEventListener("change", (e) => api("/api/settings", { allow_fable: e.target.checked }).then(render));
document.querySelectorAll("[data-growth]").forEach((b) =>
  b.addEventListener("click", () => {
    const growth = Number($("growthValue").dataset.value) + Number(b.dataset.growth);
    if (growth >= 0) api("/api/settings", { auto_approve_growth: growth }).then(render);
  })
);

Thresholds.init((changes) => api("/api/settings", changes).then(render));

const refresh = () => api("/api/state").then(render);
refresh();
setInterval(refresh, 1000);
