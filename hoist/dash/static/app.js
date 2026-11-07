/* hoist dash — the client. No framework, no build step.
   Views render HTML strings into "parts"; polling refreshes parts in place
   and skips any part that holds focus, so typing is never interrupted. */
"use strict";

// ── tiny helpers ────────────────────────────────────────────────────────────

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const ICONS = {
  overview: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  apps: '<path d="M12 3 3.5 7.5 12 12l8.5-4.5L12 3Z"/><path d="m3.5 12 8.5 4.5 8.5-4.5"/><path d="m3.5 16.5 8.5 4.5 8.5-4.5"/>',
  deploys: '<circle cx="12" cy="12" r="3.2"/><path d="M3 12h5.8M15.2 12H21"/>',
  routes: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3Z"/>',
  machine: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.6-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.6 4.5L20 16"/><path d="M20 20v-4h-4"/>',
  play: '<path d="M7 5v14l11-7L7 5Z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  ext: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3M21 14v.01M14 21h3M21 17v4h-4"/>',
  more: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  alert: '<path d="M12 4 2.8 19.5h18.4L12 4Z"/><path d="M12 10v4M12 17v.01"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/>',
  logout: '<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4"/><path d="M10 16l-4-4 4-4M6 12h10"/>',
  git: '<circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="9" r="2.2"/><path d="M6 8.2v7.6M18 11.2c0 3-3 3.6-6 4.2-2 .4-4 .9-4.6 2"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
  box: '<path d="M4 7.5 12 3l8 4.5v9L12 21l-8-4.5v-9Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>',
  hammer: '<path d="m14 6 4 4-9.5 9.5a2.1 2.1 0 0 1-3-3L14 6Z"/><path d="m12.5 4.5 2-2 7 7-2 2"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M10 2v4M14 2v4M10 18v4M14 18v4M2 10h4M2 14h4M18 10h4M18 14h4"/>',
  plug: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0V8ZM12 17v4"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3Z"/>',
  terminal: '<path d="m5 7 5 5-5 5M12 17h7"/>',
  eye: '<path d="M2.5 12S6 5 12 5s9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7Z"/><circle cx="12" cy="12" r="2.8"/>',
  eyeoff: '<path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6 0 9.5 7 9.5 7a17 17 0 0 1-3 3.9M6.4 6.4A17 17 0 0 0 2.5 12S6 19 12 19a9.7 9.7 0 0 0 4.6-1.2"/><path d="M9.9 9.9a2.8 2.8 0 0 0 4.2 4.2"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M15 8l2 2"/>',
  phone: '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  dot: '<circle cx="12" cy="12" r="4"/>',
  db: '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.8"/><path d="M4.5 5.5v13c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8v-13"/><path d="M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8"/>',
  team: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c.6-3.4 3-5.4 6-5.4s5.4 2 6 5.4"/><circle cx="17" cy="9" r="2.4"/><path d="M16.5 14.3c2.4.2 4 1.8 4.5 4.7"/>',
  branch: '<circle cx="6" cy="5" r="2.2"/><circle cx="6" cy="19" r="2.2"/><circle cx="18" cy="7" r="2.2"/><path d="M6 7.2v9.6M18 9.2c0 4-6 3-11 7.5"/>',
  link: '<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/>',
};
const ic = (name, size = 16, extra = "") =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${ICONS[name] || ""}</svg>`;

// ── formatting ──────────────────────────────────────────────────────────────

const fmt = {
  bytes(n) {
    if (n == null) return "—";
    const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; let v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)}<span class="unit">${u[i]}</span>`;
  },
  bytesText(n) { return fmt.bytes(n).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(); },
  pct(v, d = 1) { return v == null ? "—" : `${(+v).toFixed(d)}<span class="unit">%</span>`; },
  ms(v) { return v == null ? "—" : `${v}<span class="unit">ms</span>`; },
  ts(v) { if (v == null) return null; return typeof v === "number" ? v * 1000 : Date.parse(v); },
  ago(v) {
    const t = fmt.ts(v); if (!t || isNaN(t)) return "—";
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 45) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) { const h = Math.floor(s / 3600); return `${h} h ago`; }
    const d = Math.floor(s / 86400); return d === 1 ? "yesterday" : `${d} days ago`;
  },
  dur(s) {
    if (s == null) return "—";
    if (s < 60) return `${s < 10 ? (+s).toFixed(1) : Math.round(s)} s`;
    if (s < 3600) return `${Math.floor(s / 60)} m ${Math.round(s % 60)} s`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} m`;
    return `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`;
  },
  when(v) { const t = fmt.ts(v); return t ? new Date(t).toLocaleString() : ""; },
  clock(v) { const t = fmt.ts(v); return t ? new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""; },
  sha(s) { return s ? String(s).slice(0, 7) : "—"; },
  host(url) { try { return new URL(url).host; } catch { return url || ""; } },
  repo(url) { if (!url) return ""; return url.replace(/^https?:\/\//, "").replace(/^git@([^:]+):/, "$1/").replace(/\.git$/, "").replace(/^file:\/\//, ""); },
  plural(n, one, many) { return `${n} ${n === 1 ? one : many || one + "s"}`; },
  subject(commit) { return String(commit || "").replace(/^[0-9a-f]{7,}\s+/, ""); },
  author(commit) { const m = String(commit || "").match(/^[0-9a-f]{7,}\s+([^:]+):/); return m ? m[1] : ""; },
  message(commit) { const s = fmt.subject(commit); const i = s.indexOf(": "); return i > -1 ? s.slice(i + 2) : s; },
};

const ENGINE_LABEL = { postgres: "PostgreSQL", mysql: "MySQL", mariadb: "MariaDB", redis: "Redis", mongo: "MongoDB" };
const STATUS_WORDS = { up: "Live", down: "Down", starting: "Starting", failed: "Crashed", stopped: "Stopped", inactive: "Inactive" };
const statusPill = (s) => `<span class="pill ${esc(s)}"><span class="d"></span>${STATUS_WORDS[s] || esc(s)}</span>`;

// ── api ─────────────────────────────────────────────────────────────────────

// Which machine the dashboard is looking at: "local" or a linked machine's id.
// Everything but sign-in and the machine list goes through /api/remote/<id>/.
// Kept per tab (sessionStorage), never across visits: opening the dashboard
// later must not silently land you on another machine.
let currentServer = (() => { try { return sessionStorage.getItem("hoist-server") || "local"; } catch { return "local"; } })();
const LOCAL_ONLY = /^\/api\/(servers|login|logout|qr\.svg)/;
function apiPath(path) {
  if (currentServer === "local" || !path.startsWith("/api/") || LOCAL_ONLY.test(path)) return path;
  return `/api/remote/${currentServer}/${path.slice(5)}`;
}
function switchServer(id) {
  currentServer = id || "local";
  try { sessionStorage.setItem("hoist-server", currentServer); } catch {}
  store.state = null; store.detail = {}; store.deployments = null; store.routes = null; store.team = null; store.metrics = null; store.doctor = null;
  if (current().name === "app" || current().name === "notfound") history.replaceState({}, "", "/");
  mount(); poll();
}

async function api(path, opts = {}) {
  path = apiPath(path);
  const init = { headers: {}, ...opts };
  if (opts.body !== undefined) {
    init.method = opts.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  if ((init.method || "GET") !== "GET") init.headers["X-Hoist"] = "1";
  const r = await fetch(path, init);
  if (r.status === 401) { location.replace("/"); throw new Error("signed out"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(data.error || `${r.status} ${r.statusText}`); e.data = data; e.status = r.status; throw e; }
  return data;
}
const post = (path, body = {}) => api(path, { method: "POST", body });

// ── store and polling ───────────────────────────────────────────────────────

const store = { state: null, metrics: null, detail: {}, deployments: null, routes: null, doctor: null, lastOk: 0, failing: false };
const appByName = (n) => store.state?.apps.find((a) => a.name === n);

async function poll() {
  try {
    store.state = await api("/api/state");
    if (currentServer !== "local" && !store.fleet) store.fleet = await api("/api/servers").catch(() => null);
    store.lastOk = Date.now(); store.failing = false;
    const route = current();
    if (route.name === "app") {
      const detail = await api(`/api/apps/${route.params[0]}`).catch(() => null);
      if (["db", "compose"].includes(detail?.row?.kind)) detail.backups = await api(`/api/apps/${route.params[0]}/backups`).catch(() => null);
      store.detail[route.params[0]] = detail;
    }
    if (route.name === "overview" || route.name === "machine") store.metrics = await api("/api/metrics");
    if (route.name === "deployments" || route.name === "overview") store.deployments = (await api("/api/deployments")).deployments;
    if (route.name === "routes") store.routes = (await api("/api/routes")).rules;
    if (route.name === "team") store.team = await api("/api/team");
    if (route.name === "servers") { store.fleet = await api("/api/servers"); store.fleetBroken = store.fleet.servers.filter((x) => !x.ok || x.broken?.length).length; }
  } catch (e) {
    if (e.message !== "signed out") store.failing = true;
    if (currentServer !== "local" && e.status === 404 && /no such machine/.test(e.message)) switchServer("local");
  }
  paintChrome();
  refresh();
}

let pollTimer = null;
function startPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(() => { if (!document.hidden) poll(); }, 3000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
}
setInterval(() => {
  const el = $("#live"); if (!el) return;
  const age = (Date.now() - store.lastOk) / 1000;
  el.classList.toggle("stale", age > 10 && !store.failing);
  el.classList.toggle("off", store.failing);
  $("#live-text").textContent = store.failing ? "Can't reach this machine" : !store.lastOk ? "Connecting" : age < 5 ? "Live" : `Updated ${Math.round(age)} s ago`;
}, 1000);

// ── router ──────────────────────────────────────────────────────────────────

const ROUTES = [
  ["overview", /^\/$/],
  ["apps", /^\/apps\/?$/],
  ["app", /^\/apps\/([a-z0-9-]+)(?:\/(deployments|logs|console|environment|settings))?\/?$/],
  ["deployments", /^\/deployments\/?$/],
  ["routes", /^\/routes\/?$/],
  ["machine", /^\/machine\/?$/],
  ["databases", /^\/databases\/?$/],
  ["team", /^\/team\/?$/],
  ["servers", /^\/servers\/?$/],
];
function current() {
  for (const [name, re] of ROUTES) { const m = location.pathname.match(re); if (m) return { name, params: m.slice(1) }; }
  return { name: "notfound", params: [] };
}
function go(path, replace = false) {
  if (path === location.pathname + location.search) return;
  history[replace ? "replaceState" : "pushState"]({}, "", path);
  mount();
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-link], a[href^='/']:not([target])");
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
  const href = a.getAttribute("href");
  if (!href || href.startsWith("/api/") || href.startsWith("/static/")) return;
  e.preventDefault(); $("#shell").classList.remove("nav-open"); go(href);
});
window.addEventListener("popstate", mount);

// ── views ───────────────────────────────────────────────────────────────────
// A view: { title, crumbs:[[label,href]], parts:{key: () => html}, layout(): html with
// <div data-part="key">, mount?(), unmount?() }. Charts register specs while rendering.

let view = null;
const chartSpecs = {};

function mount() {
  if (view?.unmount) view.unmount();
  closeLayer();
  const route = current();
  const make = VIEWS[route.name] || VIEWS.notfound;
  view = make(...route.params);
  document.title = `${view.title} · hoist`;
  $("#page").innerHTML = view.layout();
  for (const key of Object.keys(view.parts || {})) renderPart(key, true);
  $("#sheet").scrollTop = 0;
  paintChrome();
  if (view.mount) view.mount();
  if (["app", "overview", "deployments", "routes", "machine", "databases", "team", "servers"].includes(route.name)) poll();
}

function renderPart(key, force = false) {
  const el = document.querySelector(`[data-part="${key}"]`);
  if (!el || !view?.parts?.[key]) return;
  if (!force && el.contains(document.activeElement) && document.activeElement !== document.body) return;
  if (!force && el.dataset.hold === "1") return;
  const html = view.parts[key]();
  if (el.__html === html) { drawCharts(el); return; }
  el.__html = html; el.innerHTML = html;
  drawCharts(el);
}
function refresh() {
  if (!view) return;
  for (const key of Object.keys(view.parts || {})) renderPart(key);
  if (view.refresh) view.refresh();
}
const part = (key, cls = "") => `<div data-part="${key}" class="${cls}"></div>`;

// ── chrome: nav, crumbs, footer ─────────────────────────────────────────────

function paintChrome() {
  const s = store.state; const route = current();
  const linked = currentServer !== "local" ? (store.fleet?.servers || []).find((x) => x.id === currentServer) : null;
  $("#brand-host").textContent = linked ? linked.name : (s?.host || "");
  $("#brand-host").title = currentServer === "local" ? "This machine" : `Linked machine — managed through this dashboard`;
  document.body.classList.toggle("remote", currentServer !== "local");
  document.body.classList.toggle("viewer", s?.role === "viewer");
  const all = s?.apps || [];
  const apps = all.filter((a) => a.kind !== "db");
  const dbs = all.filter((a) => a.kind === "db");
  const broken = apps.filter(isBroken).length;
  const navItem = (href, label, icon, active, count = "") =>
    `<a href="${href}" data-link ${active ? 'aria-current="page"' : ""}>${ic(icon)}<span>${label}</span>${count}</a>`;
  const appLinks = apps.slice(0, 12).map((a) =>
    `<a class="app-link" href="/apps/${a.name}" data-link ${route.name === "app" && route.params[0] === a.name ? 'aria-current="page"' : ""} title="${esc(a.name)} — ${STATUS_WORDS[a.status] || a.status}">
      <span class="dot ${a.status}"></span><span class="label">${a.preview_of ? '<span class="faint">↳ </span>' : ""}${esc(a.name)}</span></a>`).join("");
  $("#nav").innerHTML =
    navItem("/", "Overview", "overview", route.name === "overview") +
    navItem("/apps", "Apps", "apps", route.name === "apps", broken ? `<span class="count bad" title="${broken} not answering">${broken} down</span>` : `<span class="count">${apps.length || ""}</span>`) +
    navItem("/deployments", "Deployments", "deploys", route.name === "deployments") +
    navItem("/routes", "Routes", "routes", route.name === "routes") +
    navItem("/databases", "Databases", "db", route.name === "databases", dbs.length ? `<span class="count">${dbs.length}</span>` : "") +
    navItem("/machine", "Machine", "machine", route.name === "machine") +
    navItem("/team", "Team", "team", route.name === "team") +
    (s?.role === "owner" || !s ? navItem("/servers", "Machines", "routes", route.name === "servers", store.fleetBroken ? `<span class="count bad">${store.fleetBroken}</span>` : "") : "") +
    (apps.length ? `<div class="nav-group">Apps</div>${appLinks}` : "");
  const dark = document.documentElement.dataset.theme === "dark";
  $("#sidebar-foot").innerHTML = `
    <div class="nav">
      <a href="#" data-action="new-app">${ic("plus")}<span>New app</span><kbd style="margin-left:auto">N</kbd></a>
      <a href="#" data-action="theme">${ic(dark ? "sun" : "moon")}<span>${dark ? "Light" : "Dark"} theme</span></a>
      <a href="#" data-action="logout">${ic("logout")}<span>Sign out</span></a>
    </div>
    <div class="meta">${s?.me ? `Signed in as ${esc(s.me)} · ` : ""}hoist ${esc(s?.version || "")}</div>`;
  $("#crumbs").innerHTML = (currentServer !== "local" ? `<button class="pill paused" data-action="switch-server" data-server="local" title="Back to this machine" style="border:0;cursor:pointer">on ${esc(linked?.name || currentServer)} ×</button>` : "") +
    (view?.crumbs || []).map(([label, href], i, all) =>
    i === all.length - 1 ? `<span class="here">${esc(label)}</span>` : `<a href="${href}" data-link>${esc(label)}</a><span class="sep">/</span>`).join("");
  $("#search-btn").innerHTML = `${ic("search")}<span>Search apps and actions</span><kbd>Ctrl K</kbd>`;
  $("#menu-btn").innerHTML = ic("menu", 18);
}

// ── diagnosis: why an app is not answering ──────────────────────────────────

function isBroken(a) {
  if (a.status === "stopped") return false;
  if (["down", "failed"].includes(a.status)) return true;
  return a.public && a.probe && !a.probe.up;
}
function diagnose(a) {
  if (a.status === "stopped") return "stopped from the dashboard";
  if (a.adopted && a.status === "down") return `nothing is listening on port ${a.port}`;
  if (a.status === "failed") return `the process keeps crashing (${fmt.plural(a.restarts, "restart")})`;
  if (a.status === "down") return "its service is not running";
  if (a.status === "starting") return `the service is running but nothing answers on port ${a.port} yet`;
  if (a.public && a.probe && !a.probe.up) return a.probe.code ? `its public URL answers ${a.probe.code}` : `its public URL is unreachable (${a.probe.error || "no response"})`;
  if (a.repo?.failed_sha) return `the push ${fmt.sha(a.repo.failed_sha)} failed to build — still serving ${fmt.sha(a.repo.sha)}`;
  return "";
}
function probeText(p) {
  if (!p) return '<span class="dash">waiting for first check</span>';
  if (!p.code) return `<span style="color:var(--negative)">unreachable</span>`;
  return `<span ${p.up ? "" : 'style="color:var(--negative)"'}>${p.code}</span> <span class="muted">·</span> ${fmt.ms(p.ms)}`;
}
function sourceCell(a) {
  if (a.kind === "db") return `<div class="nowrap">${ic("db", 14, 'style="vertical-align:-2px;color:var(--muted)"')} ${esc(ENGINE_LABEL[a.db?.engine] || a.db?.engine)}</div><div class="sub">${esc(a.db?.image || "")}</div>`;
  if (a.repo) {
    return `<div class="nowrap">${ic("git", 14, 'style="vertical-align:-2px;color:var(--muted)"')} ${a.dockerfile ? "Dockerfile · " : a.kind === "compose" ? "Compose · " : ""}${esc(a.repo.branch)} <span class="sha">${fmt.sha(a.repo.sha)}</span>${a.preview_of ? ' <span class="pill">Preview</span>' : ""}${a.repo.paused ? ' <span class="pill paused">Paused</span>' : ""}${a.repo.failed_sha ? ' <span class="pill failed">Build failed</span>' : ""}</div>
            <div class="sub">${a.preview_of ? `Preview of ${esc(a.preview_of)} · ` : ""}${esc(fmt.message(a.repo.commit))}</div>`;
  }
  if (a.template) return `<div class="nowrap">${ic("box", 14, 'style="vertical-align:-2px;color:var(--muted)"')} Ready-made · ${esc(a.template)}</div><div class="sub">Docker image, data in a volume</div>`;
  if (a.adopted) return `<div class="nowrap">${ic("plug", 14, 'style="vertical-align:-2px;color:var(--muted)"')} Existing process</div><div class="sub">hoist only routes to it</div>`;
  return `<div class="nowrap">${ic("folder", 14, 'style="vertical-align:-2px;color:var(--muted)"')} Local folder</div><div class="sub">${esc(a.workdir || "")}</div>`;
}

// ── charts (one hue on the main series, quiet chrome) ───────────────────────

let chartSeq = 0;
function chart(spec) { const id = `c${++chartSeq}`; chartSpecs[id] = spec; return `<div class="chart" data-chart="${id}" style="height:${spec.height || 160}px"></div>`; }

function drawCharts(root) {
  for (const el of $$("[data-chart]", root)) {
    const spec = chartSpecs[el.dataset.chart]; if (!spec) continue;
    drawLine(el, spec);
  }
}

function drawLine(el, spec) {
  const W = Math.max(200, el.clientWidth), H = spec.height || 160;
  const padL = 44, padR = 8, padT = 10, padB = 22;
  const series = spec.series.filter((s) => s.points.length);
  if (!series.length) {
    el.innerHTML = `<div class="empty" style="height:${H}px;justify-content:center;padding:0">${esc(spec.empty || "No samples yet — the dashboard keeps the last hour while it runs.")}</div>`;
    return;
  }
  const now = Date.now() / 1000;
  const t0 = spec.from ?? Math.min(...series.map((s) => s.points[0][0]));
  const t1 = Math.max(now, ...series.map((s) => s.points[s.points.length - 1][0]));
  let yMax = spec.yMax ?? Math.max(...series.flatMap((s) => s.points.map((p) => p[1]).filter((v) => v != null)), 0);
  if (!spec.yMax) yMax = niceMax(yMax || 1);
  const x = (t) => padL + ((t - t0) / Math.max(1, t1 - t0)) * (W - padL - padR);
  const y = (v) => padT + (1 - v / yMax) * (H - padT - padB);
  const ticks = [0, 0.5, 1].map((f) => yMax * f);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(spec.label || "chart")}">`;
  for (const v of ticks) svg += `<line class="gl" x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}"/><text class="tick" x="${padL - 8}" y="${y(v) + 4}" text-anchor="end">${esc(spec.yFmt ? spec.yFmt(v) : Math.round(v))}</text>`;
  const span = t1 - t0, step = span > 2400 ? 900 : span > 900 ? 300 : 60;
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
    svg += `<text class="tick" x="${x(t)}" y="${H - 6}" text-anchor="middle">${new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</text>`;
  }
  for (const m of spec.markers || []) {
    if (m.t < t0 || m.t > t1) continue;
    svg += `<line class="marker ${m.bad ? "bad" : ""}" x1="${x(m.t)}" x2="${x(m.t)}" y1="${padT}" y2="${H - padB}"/><text class="marker-label ${m.bad ? "bad" : ""}" x="${x(m.t) + 4}" y="${padT + 10}">${esc(m.label)}</text>`;
  }
  for (const s of series) {
    let d = "", area = "", open = false, first = null, last = null;
    for (const [t, v] of s.points) {
      if (v == null) { if (open && s.area) area += `L${last} ${y(0)}Z`; open = false; continue; }
      const px = x(t).toFixed(1), py = y(Math.min(v, yMax)).toFixed(1);
      d += `${open ? "L" : "M"}${px} ${py}`;
      if (s.area) area += open ? `L${px} ${py}` : `M${px} ${y(0)}L${px} ${py}`;
      if (!open) first = px; last = px; open = true;
    }
    if (open && s.area) area += `L${last} ${y(0)}Z`;
    if (s.area) svg += `<path class="ar" d="${area}" fill="${s.color}"/>`;
    svg += `<path class="ln" d="${d}" stroke="${s.color}"/>`;
  }
  svg += `<line class="hover-line" x1="0" x2="0" y1="${padT}" y2="${H - padB}" visibility="hidden"/></svg>`;
  el.innerHTML = svg;
  const tip = document.createElement("div"); tip.className = "chart-tip hidden"; el.appendChild(tip);
  const line = $(".hover-line", el);
  el.onmousemove = (e) => {
    const r = el.getBoundingClientRect(); const px = e.clientX - r.left;
    if (px < padL || px > W - padR) { tip.classList.add("hidden"); line.setAttribute("visibility", "hidden"); return; }
    const t = t0 + ((px - padL) / (W - padL - padR)) * (t1 - t0);
    const rows = series.map((s) => {
      let best = s.points[0];
      for (const p of s.points) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
      return `<div><i style="display:inline-block;width:8px;height:2px;background:${s.color};margin-right:6px;vertical-align:middle"></i>${esc(s.label)} <b>${best[1] == null ? "—" : esc(s.fmt ? s.fmt(best[1]) : best[1])}</b></div>`;
    }).join("");
    tip.innerHTML = `<div class="muted">${new Date(t * 1000).toLocaleTimeString()}</div>${rows}`;
    tip.classList.remove("hidden");
    tip.style.left = `${Math.min(px + 12, W - tip.offsetWidth - 4)}px`; tip.style.top = "4px";
    line.setAttribute("x1", px); line.setAttribute("x2", px); line.setAttribute("visibility", "visible");
  };
  el.onmouseleave = () => { tip.classList.add("hidden"); line.setAttribute("visibility", "hidden"); };
}
function niceMax(v) {
  const p = Math.pow(10, Math.floor(Math.log10(v))); const f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
}
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
window.addEventListener("resize", () => { clearTimeout(window.__rz); window.__rz = setTimeout(() => drawCharts(document), 120); });

function probeStrip(probes, n = 60) {
  const cells = probes.slice(-n);
  const pad = Array.from({ length: Math.max(0, n - cells.length) }, () => '<i class="none"></i>').join("");
  return `<div class="strip" role="img" aria-label="Public checks, last hour">${pad}${cells.map((p) =>
    `<i class="${p.up ? "" : "bad"}" title="${esc(new Date(p.t * 1000).toLocaleTimeString())} — ${p.code ? p.code + " in " + p.ms + " ms" : "unreachable"}"></i>`).join("")}</div>`;
}
