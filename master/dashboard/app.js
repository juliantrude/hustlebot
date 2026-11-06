"use strict";
// Dashboard of the hustlebot master: chain cards, sorted active first and by weight. Data from /api/state.

const $ = (id) => document.getElementById(id);
const STATUS = { running: "running", waiting: "waiting", blocked: "blocked", paused: "inactive", stopping: "finishing", done: "done" };
// chain id -> card element. A card stays where it is in the DOM for good: moving an iframe reloads it, so the order
// on screen comes from CSS `order` alone.
const cards = new Map();

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return res.json();
}

function graphUrl(id, mini) {
  const data = encodeURIComponent(`/api/chains/${id}/graph.json`);
  return `/engine/?data=${data}${mini ? "&view=mini" : ""}`;
}

function card(chain) {
  let node = cards.get(chain.id);
  if (!node) {
    node = $("chainCard").content.firstElementChild.cloneNode(true);
    node.querySelector(".active").addEventListener("change", (e) => api(`/api/chains/${chain.id}`, { active: e.target.checked }).then(render));
    node.querySelectorAll("[data-weight]").forEach((b) =>
      b.addEventListener("click", () => {
        const w = Number(node.dataset.weight) + Number(b.dataset.weight);
        if (w >= 1) api(`/api/chains/${chain.id}`, { weight: w }).then(render);
      })
    );
    node.querySelector(".switch").addEventListener("click", (e) => e.stopPropagation());
    node.querySelector(".weight").addEventListener("click", (e) => e.stopPropagation());
    node.addEventListener("click", () => navigate(`/chains/${chain.id}`));
    cards.set(chain.id, node);
    $("chains").append(node);
  }
  // the preview reloads only when an increment changed state: reloading an iframe flickers
  if (node.dataset.graph !== chain.graph_version) {
    node.dataset.graph = chain.graph_version;
    const frame = node.querySelector(".preview iframe");
    // replace(), not src: a new src would add an entry to the browser history
    if (frame.contentWindow && frame.getAttribute("src")) frame.contentWindow.location.replace(graphUrl(chain.id, true));
    else frame.src = graphUrl(chain.id, true);
  }
  node.dataset.weight = chain.weight;
  node.dataset.project = chain.project;
  node.dataset.title = chain.title;
  node.classList.toggle("inactive", !chain.active);
  node.querySelector(".active").checked = chain.active;
  node.querySelector(".w").textContent = `${chain.weight} fr`;
  node.querySelector(".share").textContent = chain.share ? `${chain.points_in_use}/${chain.share} pts` : "no share";
  const status = node.querySelector(".status");
  status.className = `status ${chain.status}`;
  status.textContent = STATUS[chain.status] || chain.status;
  node.querySelector(".project").textContent = chain.project;
  node.querySelector(".title").textContent = chain.title;
  node.querySelector(".progress i").style.width = `${(100 * chain.done) / chain.total}%`;
  node.querySelector(".count").textContent = `${chain.done} / ${chain.total} increments`;
  const load = node.querySelector(".load");
  load.title = chain.sessions.length
    ? chain.sessions.map((s) => `${s.increment} · ${s.model} · ${s.points} pts`).join("\n")
    : "no session running";
  Load.update(chain.id, load, chain);
  return node;
}

function renderApprovals(list, chains) {
  const title = new Map(chains.map((c) => [c.id, `${c.project} · ${c.title}`]));
  $("approvals").hidden = list.length === 0;
  $("approvalList").replaceChildren(
    ...list.map((a) => {
      const li = document.createElement("li");
      const t = document.createElement("span");
      t.className = "t";
      t.textContent = `${title.get(a.chain) || a.chain}: ${a.text}`;
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

function render(state) {
  Load.global(state);
  $("corePct").textContent = `${Math.round(state.usage.session.percent)}%`;
  $("coreWeek").textContent = `week ${Math.round(state.usage.week.percent)}%`;
  $("core").title = `5h session ${state.usage.session.percent}%, resets ${state.usage.session.resets}\nweek ${state.usage.week.percent}%, resets ${state.usage.week.resets}`;
  $("points").textContent = state.total_points;
  $("pointsText").textContent = `${state.points_in_use} of ${state.total_points} session points burning`;
  renderApprovals(state.approvals, state.chains);
  state.chains.forEach((chain, index) => (card(chain).style.order = index));
  titles = new Map(state.chains.map((c) => [c.id, `${c.project} · ${c.title}`]));
  route();
  Flow.update(state, cards);
}

// --- routes: / is the list, /chains/<id> the chain's graph full screen --------------------------------

let titles = new Map();
let shown = null; // chain id whose graph is open

function navigate(path) {
  history.pushState(null, "", path);
  route();
}

function route() {
  const match = location.pathname.match(/^\/chains\/([\w-]+)$/);
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
  frame.title = "Chain graph";
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

const refresh = () => api("/api/state").then(render);
refresh();
setInterval(refresh, 1000);
