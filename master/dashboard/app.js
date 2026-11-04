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

function bar(el, percent) {
  el.style.width = `${Math.min(100, percent)}%`;
  el.parentElement.classList.toggle("hot", percent >= 80);
}

function graphUrl(id, mini) {
  const data = encodeURIComponent(`/api/chains/${id}/graph.json`);
  return `/engine/?data=${data}${mini ? "&view=mini" : ""}`;
}

function card(chain) {
  let node = cards.get(chain.id);
  if (!node) {
    node = $("chainCard").content.firstElementChild.cloneNode(true);
    node.querySelector(".preview iframe").src = graphUrl(chain.id, true);
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
  const agents = node.querySelector(".agents");
  agents.replaceChildren(
    ...[...chain.sessions]
      .sort((a, b) => a.model.localeCompare(b.model))
      .map((s) => {
        const a = document.createElement("span");
        a.className = `agent ${s.model}`;
        a.textContent = s.model[0].toUpperCase();
        a.title = `${s.model} · increment ${s.increment}`;
        return a;
      })
  );
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
  bar($("usageSession"), state.usage.session.percent);
  $("usageSessionText").textContent = `${state.usage.session.percent}% · resets ${state.usage.session.resets}`;
  bar($("usageWeek"), state.usage.week.percent);
  $("usageWeekText").textContent = `${state.usage.week.percent}% · resets ${state.usage.week.resets}`;
  $("points").textContent = state.total_points;
  $("pointsText").textContent = `${state.points_in_use} in use`;
  renderApprovals(state.approvals, state.chains);
  state.chains.forEach((chain, index) => (card(chain).style.order = index));
  titles = new Map(state.chains.map((c) => [c.id, `${c.project} · ${c.title}`]));
  route();
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
setInterval(refresh, 5000);
