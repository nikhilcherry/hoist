
// ── layer: overlays, dialogs, drawers, menus ────────────────────────────────

let layerClose = null;
function openLayer(html, { onClose, kind = "dialog" } = {}) {
  closeLayer();
  const layer = $("#layer");
  const lastFocus = document.activeElement;
  layer.innerHTML = `<div class="overlay" data-close></div>${html}`;
  const box = layer.lastElementChild;
  box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
  layer.querySelector("[data-close]").addEventListener("click", () => closeLayer());
  layerClose = () => { onClose?.(); layer.innerHTML = ""; layerClose = null; lastFocus?.focus?.(); };
  setTimeout(() => (box.querySelector("[autofocus]") || box.querySelector("input,textarea,button"))?.focus(), 20);
  return box;
}
function closeLayer() { if (layerClose) layerClose(); closeMenu(); }

function confirmDialog({ title, body, ok = "Confirm", danger = false, typeToConfirm = null }) {
  return new Promise((resolve) => {
    const box = openLayer(`<div class="dialog"><div class="body"><h2>${esc(title)}</h2><div class="muted">${body}</div>
      ${typeToConfirm ? `<div class="field"><label for="ttc">Type <span class="mono">${esc(typeToConfirm)}</span> to confirm</label><input class="input mono" id="ttc" autocomplete="off" autofocus></div>` : ""}</div>
      <div class="foot"><button class="btn" data-no>Cancel</button><button class="btn ${danger ? "danger solid" : "primary"}" data-yes ${typeToConfirm ? "disabled" : ""}>${esc(ok)}</button></div></div>`,
      { onClose: () => resolve(false) });
    const yes = $("[data-yes]", box);
    if (typeToConfirm) $("#ttc", box).addEventListener("input", (e) => { yes.disabled = e.target.value.trim() !== typeToConfirm; });
    $("[data-no]", box).onclick = () => closeLayer();
    yes.onclick = () => { layerClose = null; $("#layer").innerHTML = ""; resolve(true); };
  });
}

function promptDialog({ title, label, textarea = false, ok = "OK" }) {
  return new Promise((resolve) => {
    const box = openLayer(`<div class="dialog" style="width:min(560px,calc(100vw - 32px))"><div class="body"><h2>${esc(title)}</h2>
      <div class="field"><label class="muted" style="font-weight:400" for="pd">${esc(label)}</label>${textarea ? `<textarea class="textarea" id="pd" rows="9" spellcheck="false" autofocus></textarea>` : `<input class="input" id="pd" autofocus>`}</div></div>
      <div class="foot"><button class="btn" data-no>Cancel</button><button class="btn primary" data-yes>${esc(ok)}</button></div></div>`, { onClose: () => resolve(null) });
    $("[data-no]", box).onclick = () => closeLayer();
    $("[data-yes]", box).onclick = () => { const v = $("#pd", box).value; layerClose = null; $("#layer").innerHTML = ""; resolve(v); };
  });
}

let menuEl = null;
function closeMenu() { menuEl?.remove(); menuEl = null; }
function openMenu(anchor, items) {
  closeMenu();
  menuEl = document.createElement("div"); menuEl.className = "menu"; menuEl.setAttribute("role", "menu");
  menuEl.innerHTML = items.map((it) => it === "-" ? "<hr>" : `<button role="menuitem" class="${it.danger ? "danger" : ""}" data-i="${items.indexOf(it)}" ${it.disabled ? "disabled" : ""}>${ic(it.icon || "dot")}${esc(it.label)}</button>`).join("");
  document.body.appendChild(menuEl);
  const r = anchor.getBoundingClientRect();
  const left = Math.min(r.right - menuEl.offsetWidth, innerWidth - menuEl.offsetWidth - 8);
  const top = r.bottom + 4 + menuEl.offsetHeight > innerHeight ? r.top - menuEl.offsetHeight - 4 : r.bottom + 4;
  menuEl.style.left = `${Math.max(8, left)}px`; menuEl.style.top = `${top}px`;
  menuEl.addEventListener("click", (e) => { const b = e.target.closest("[data-i]"); if (!b) return; const it = items[+b.dataset.i]; closeMenu(); it.run(); });
  menuEl.querySelector("button")?.focus();
}
document.addEventListener("click", (e) => { if (menuEl && !menuEl.contains(e.target) && !e.target.closest('[data-action="app-menu"]')) closeMenu(); });

function toast(msg, bad = false, link = null) {
  const el = document.createElement("div"); el.className = `toast ${bad ? "bad" : ""}`;
  el.innerHTML = `<span>${esc(msg)}</span>${link ? `<a href="${link.href}" data-link>${esc(link.label)}</a>` : ""}`;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.transition = "opacity 200ms"; el.style.opacity = "0"; setTimeout(() => el.remove(), 220); }, bad ? 7000 : 4000);
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch { const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); }
  toast("Copied");
}

// ── jobs: watch a deploy as it happens ──────────────────────────────────────

function termLine(line) {
  const cls = /^(✓|deployed)/.test(line) ? "ok" : /^(✗|error|build failed|.*exited \d)/i.test(line) ? "bad" : /^(!|warning)/i.test(line) ? "warn" : /^\s*(→|·|\$)/.test(line) ? "dim" : "";
  return `<div class="${cls}">${esc(line) || " "}</div>`;
}
function streamJob(job, term, onDone) {
  term.innerHTML = "";
  const es = new EventSource(`/api/jobs/${job.id}/stream`);
  es.addEventListener("line", (e) => { const atBottom = term.scrollHeight - term.scrollTop - term.clientHeight < 30; term.insertAdjacentHTML("beforeend", termLine(JSON.parse(e.data))); if (atBottom) term.scrollTop = term.scrollHeight; });
  es.addEventListener("done", (e) => { es.close(); const j = JSON.parse(e.data); onDone?.(j); poll(); });
  es.onerror = () => { es.close(); api(`/api/jobs/${job.id}`).then((j) => { term.innerHTML = j.lines.map(termLine).join(""); if (j.status !== "running") onDone?.(j); }).catch(() => {}); };
  return es;
}
function jobDrawer(job, { title } = {}) {
  let es = null;
  const box = openLayer(`<div class="drawer"><div class="drawer-head"><h2 style="flex:1">${esc(title || job.title)}</h2><span id="job-state">${job.status === "running" ? '<span class="pill building"><span class="d"></span>Running</span>' : job.status === "ok" ? '<span class="pill ok">Done</span>' : '<span class="pill failed">Failed</span>'}</span><button class="btn ghost icon" data-close-x aria-label="Close">${ic("x")}</button></div>
    <div class="drawer-body"><div class="muted mono" style="font-size:12px">$ ${esc(job.command)}</div><div class="term" id="job-term" style="height:auto;flex:1;min-height:300px" aria-live="polite"></div></div>
    <div class="drawer-foot"><span class="note" id="job-note">${job.status === "running" ? "You can close this; the job keeps running." : ""}</span><button class="btn" data-close-x>Close</button>${job.app ? `<a class="btn primary" href="/apps/${esc(job.app)}" data-link>Open ${esc(job.app)}</a>` : ""}</div></div>`, { onClose: () => es?.close() });
  $$("[data-close-x]", box).forEach((b) => (b.onclick = () => closeLayer()));
  $$("a[data-link]", box).forEach((a) => a.addEventListener("click", () => closeLayer()));
  es = streamJob(job, $("#job-term", box), (j) => {
    $("#job-state", box).innerHTML = j.status === "ok" ? '<span class="pill ok">Done</span>' : '<span class="pill failed">Failed</span>';
    $("#job-note", box).textContent = j.status === "ok" ? `Finished in ${fmt.dur(j.ended - j.started)}.` : "The live version was not touched.";
  });
}

// ── new app ─────────────────────────────────────────────────────────────────

function newAppDrawer() {
  const s = store.state || {}; const domain = s.domain || "";
  let mode = "git", nameTouched = false;
  const box = openLayer(`<div class="drawer" aria-labelledby="na-title"><div class="drawer-head"><h2 id="na-title" style="flex:1">New app</h2><button class="btn ghost icon" data-x aria-label="Close">${ic("x")}</button></div>
    <form class="drawer-body" id="na-form" autocomplete="off">
      <div class="seg" role="group" aria-label="Where the code comes from"><button type="button" data-mode="git" aria-pressed="true">${ic("git", 14, 'style="vertical-align:-2px"')} Git repository</button><button type="button" data-mode="folder" aria-pressed="false">${ic("folder", 14, 'style="vertical-align:-2px"')} Folder on ${esc(s.host || "this machine")}</button><button type="button" data-mode="template" aria-pressed="false">${ic("box", 14, 'style="vertical-align:-2px"')} Ready-made app</button></div>
      <div id="na-templates" class="hidden" role="radiogroup" aria-label="Ready-made apps" style="display:flex;flex-direction:column;gap:6px"></div>
      <div class="field"><label for="na-target" id="na-target-label">Repository</label><input class="input mono" id="na-target" placeholder="github.com/team/project" required autofocus spellcheck="false">
        <span class="hint" id="na-target-hint">Public or private: hoist uses this machine's git credentials. Every push to the branch redeploys.</span></div>
      <div class="row-2">
        <div class="field"><label for="na-name">Name</label><input class="input" id="na-name" placeholder="project" pattern="[a-z0-9][a-z0-9\\-]{0,39}" spellcheck="false"><span class="hint" id="na-url">${domain ? `project.${esc(domain)}` : ""}</span></div>
        <div class="field" id="na-branch-f"><label for="na-branch">Branch</label><input class="input mono" id="na-branch" placeholder="default branch" spellcheck="false"></div>
      </div>
      <details><summary style="cursor:pointer;font-weight:500">Build and start commands</summary>
        <div style="display:flex;flex-direction:column;gap:14px;margin-top:12px">
          <div class="field" id="na-build-f"><label for="na-build">Build command</label><input class="input mono" id="na-build" placeholder="detected: npm ci && npm run build, pip install…" spellcheck="false"></div>
          <div class="field"><label for="na-cmd">Start command</label><input class="input mono" id="na-cmd" placeholder="detected from package.json, Procfile, app.py…" spellcheck="false"><span class="hint">Use <span class="mono">$PORT</span> where the port goes; hoist picks a free one.</span></div>
        </div></details>
      <div class="field"><label for="na-env">Environment variables</label><textarea class="textarea" id="na-env" rows="4" placeholder="DATABASE_URL=postgres://…&#10;API_KEY=…" spellcheck="false"></textarea><span class="hint">One KEY=value per line. You can change them later.</span></div>
      <div class="field"><label>Where it is reachable</label>
        <div class="seg" role="group"><button type="button" data-pub="1" aria-pressed="true">${ic("globe", 14, 'style="vertical-align:-2px"')} Public URL</button><button type="button" data-pub="0" aria-pressed="false">LAN only</button></div>
        <span class="hint" id="na-pub-hint">Gets <span class="mono">https://name.${esc(domain || "your-domain")}</span> through the tunnel, plus a QR code.</span></div>
      <span class="err" id="na-err" role="alert"></span>
      <div id="na-job" class="hidden" style="display:flex;flex-direction:column;gap:8px"><div style="display:flex;align-items:center;gap:8px"><h3>Deploying</h3><span id="na-state"></span></div><div class="term" id="na-term"></div></div>
    </form>
    <div class="drawer-foot"><span class="note" id="na-note">Builds on this machine, then starts it as a service.</span><button class="btn" data-x>Cancel</button><button class="btn primary" id="na-go" form="na-form" type="submit">${ic("arrow")}Deploy</button></div></div>`);
  $$("[data-x]", box).forEach((b) => (b.onclick = () => closeLayer()));
  const target = $("#na-target", box), nameIn = $("#na-name", box);
  let pub = true;
  const guess = (v) => {
    v = v.trim().replace(/\/+$/, "").replace(/\.git$/, "");
    const last = v.split(/[/:]/).pop() || "";
    return last.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
  };
  const updUrl = () => { const n = nameIn.value || guess(target.value) || "name"; $("#na-url", box).textContent = pub && domain ? `${n}.${domain}` : "LAN address + QR"; };
  target.addEventListener("input", () => { if (!nameTouched) nameIn.value = guess(target.value); updUrl(); });
  nameIn.addEventListener("input", () => { nameTouched = true; updUrl(); });
  let tpl = null;
  const tplBox = $("#na-templates", box);
  api("/api/templates").then((r) => {
    tplBox.innerHTML = r.templates.map((x) => `<label class="tpl"><input type="radio" name="tpl" value="${x.id}"><span><strong>${esc(x.label)}</strong><span class="muted"> — ${esc(x.about)}</span><br><span class="faint mono" style="font-size:12px">${esc(x.image)}</span></span></label>`).join("") +
      (r.docker ? "" : `<div class="notice bad">Docker is not running on this machine, so ready-made apps cannot start.</div>`);
    $$("input[name=tpl]", tplBox).forEach((i) => i.addEventListener("change", () => { tpl = i.value; if (!nameTouched) { nameIn.value = tpl; updUrl(); } }));
  }).catch(() => {});
  $$("[data-mode]", box).forEach((b) => b.addEventListener("click", () => {
    mode = b.dataset.mode; $$("[data-mode]", box).forEach((x) => x.setAttribute("aria-pressed", x === b));
    const isTpl = mode === "template";
    tplBox.classList.toggle("hidden", !isTpl);
    target.closest(".field").classList.toggle("hidden", isTpl); target.required = !isTpl;
    $("details", box).classList.toggle("hidden", isTpl);
    $("#na-env", box).closest(".field").classList.toggle("hidden", isTpl);
    if (isTpl) { $("#na-branch-f", box).classList.add("hidden"); return; }
    $("#na-target-label", box).textContent = mode === "git" ? "Repository" : "Folder";
    target.placeholder = mode === "git" ? "github.com/team/project" : "/home/you/Projects/app";
    $("#na-target-hint", box).textContent = mode === "git" ? "Public or private: hoist uses this machine's git credentials. Every push to the branch redeploys." : "Runs straight from this folder. Edits show up after a restart; there is no deploy history.";
    $("#na-branch-f", box).classList.toggle("hidden", mode !== "git"); $("#na-build-f", box).classList.toggle("hidden", mode !== "git");
    target.focus();
  }));
  $$("[data-pub]", box).forEach((b) => b.addEventListener("click", () => {
    pub = b.dataset.pub === "1"; $$("[data-pub]", box).forEach((x) => x.setAttribute("aria-pressed", x === b));
    $("#na-pub-hint", box).innerHTML = pub ? `Gets <span class="mono">https://name.${esc(domain || "your-domain")}</span> through the tunnel, plus a QR code.` : "Reachable from the same Wi-Fi only. Good for a venue with no internet.";
    updUrl();
  }));
  $("#na-form", box).addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#na-err", box); err.textContent = "";
    if (mode === "template") {
      if (!tpl) { err.textContent = "Pick an app"; return; }
      const goBtn = $("#na-go", box); goBtn.disabled = true; goBtn.innerHTML = `${ic("refresh", 16, 'class="spin"')}Starting`;
      try {
        const r = await post("/api/templates", { id: tpl, name: nameIn.value.trim(), local: !pub });
        $("#na-job", box).classList.remove("hidden");
        $$("#na-form > :not(#na-job):not(#na-err)", box).forEach((el) => el.classList.add("hidden"));
        streamJob(r.job, $("#na-term", box), (j) => {
          goBtn.disabled = false; goBtn.type = "button";
          if (j.status === "ok") { goBtn.innerHTML = `Open ${esc(r.app)}`; goBtn.onclick = () => { closeLayer(); window.go(`/apps/${r.app}`); }; toast(`${r.app} is live`); }
          else goBtn.innerHTML = "Failed — see the log";
        });
      } catch (ex) { err.textContent = ex.message; goBtn.disabled = false; goBtn.innerHTML = `${ic("arrow")}Deploy`; }
      return;
    }
    const env = parseDotenv($("#na-env", box).value);
    const body = { target: target.value.trim(), name: nameIn.value.trim(), branch: $("#na-branch", box).value.trim(), build: $("#na-build", box).value.trim(), cmd: $("#na-cmd", box).value.trim(), env, local: !pub };
    if (mode === "git" && !/^(https?:\/\/|git@|ssh:\/\/|file:\/\/|github\.com\/|gitlab\.com\/|bitbucket\.org\/|codeberg\.org\/)/.test(body.target)) { err.textContent = "That does not look like a git URL — try github.com/team/project"; return; }
    const goBtn = $("#na-go", box); goBtn.disabled = true; goBtn.innerHTML = `${ic("refresh", 16, 'class="spin"')}Deploying`;
    try {
      const r = await post("/api/apps", body);
      $("#na-job", box).classList.remove("hidden");
      $("#na-state", box).innerHTML = '<span class="pill building"><span class="d"></span>Building</span>';
      $$("#na-form > :not(#na-job):not(#na-err)", box).forEach((el) => el.classList.add("hidden"));
      $("#na-note", box).textContent = "You can close this; the deploy keeps going.";
      streamJob(r.job, $("#na-term", box), (j) => {
        if (j.status === "ok") {
          $("#na-state", box).innerHTML = '<span class="pill ok">Live</span>';
          goBtn.disabled = false; goBtn.innerHTML = `Open ${esc(r.app)}`; goBtn.type = "button";
          goBtn.onclick = () => { closeLayer(); window.go(`/apps/${r.app}`); };
          toast(`${r.app} is live`, false, { href: `/apps/${r.app}`, label: "Open" });
        } else {
          $("#na-state", box).innerHTML = '<span class="pill failed">Failed</span>';
          goBtn.disabled = false; goBtn.innerHTML = "Back to the form"; goBtn.type = "button";
          goBtn.onclick = () => { $$("#na-form > *", box).forEach((el) => el.classList.remove("hidden")); $("#na-job", box).classList.add("hidden"); goBtn.type = "submit"; goBtn.onclick = null; goBtn.innerHTML = `${ic("arrow")}Deploy again`; };
        }
      });
    } catch (ex) { err.textContent = ex.message; goBtn.disabled = false; goBtn.innerHTML = `${ic("arrow")}Deploy`; }
  });
}
window.go = go;

// ── new database ────────────────────────────────────────────────────────────

function newDbDialog(engine = "postgres") {
  let pick = engine || "postgres";
  const taken = new Set((store.state?.apps || []).map((a) => a.name));
  const suggest = (e) => { let n = e === "postgres" ? "db" : e; let i = 2; while (taken.has(n)) n = `${e}-${i++}`; return n; };
  const box = openLayer(`<div class="dialog" style="width:min(520px,calc(100vw - 32px))"><form class="body" id="db-form">
    <h2>New database</h2>
    <div class="seg" role="group" aria-label="Engine">${Object.entries(ENGINE_LABEL).map(([k, l]) => `<button type="button" data-engine="${k}" aria-pressed="${k === pick}">${l}</button>`).join("")}</div>
    <div class="field"><label for="db-name">Name</label><input class="input" id="db-name" value="${esc(suggest(pick))}" pattern="[a-z0-9][a-z0-9\\-]{0,39}" required spellcheck="false" autofocus></div>
    <p class="muted" style="margin:0" id="db-note"></p>
    <span class="err" id="db-err" role="alert"></span>
    <div class="term hidden" id="db-term" style="height:180px"></div>
  </form><div class="foot"><button class="btn" data-x>Cancel</button><button class="btn primary" form="db-form" type="submit" id="db-go">${ic("db")}Start</button></div></div>`);
  const note = { postgres: "postgres:16-alpine · DATABASE_URL", mysql: "mysql:8.4 · DATABASE_URL", mariadb: "mariadb:11 · DATABASE_URL", redis: "redis:7-alpine with a password · REDIS_URL", mongo: "mongo:7 · MONGODB_URL" };
  const paint = () => { $("#db-note", box).textContent = `${note[pick]}. Its own volume and a generated password; only this machine can reach it.`; };
  paint();
  $$("[data-engine]", box).forEach((b) => b.addEventListener("click", () => {
    const old = suggest(pick); pick = b.dataset.engine; $$("[data-engine]", box).forEach((x) => x.setAttribute("aria-pressed", x === b));
    if ($("#db-name", box).value === old) $("#db-name", box).value = suggest(pick); paint();
  }));
  $("[data-x]", box).onclick = () => closeLayer();
  $("#db-form", box).addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#db-go", box); btn.disabled = true; btn.innerHTML = `${ic("refresh", 16, 'class="spin"')}Starting`;
    try {
      const r = await post("/api/databases", { engine: pick, name: $("#db-name", box).value.trim() });
      $("#db-term", box).classList.remove("hidden");
      streamJob(r.job, $("#db-term", box), (j) => {
        if (j.status === "ok") { btn.disabled = false; btn.innerHTML = `Open ${esc(r.app)}`; btn.type = "button"; btn.onclick = () => { closeLayer(); go(`/apps/${r.app}`); }; toast(`${r.app} is running`); }
        else { btn.disabled = false; btn.innerHTML = "Try again"; }
      });
    } catch (err) { $("#db-err", box).textContent = err.message; btn.disabled = false; btn.innerHTML = `${ic("db")}Start`; }
  });
}

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "link-form") return;
  e.preventDefault();
  const route = current(); const db = route.params[0];
  const target = $("#link-app").value, v = $("#link-var").value.trim();
  if (!target) return;
  try { await post(`/api/apps/${db}/link`, { app: target, var: v }); toast(`${target} now has ${v} and was restarted`); poll(); }
  catch (err) { toast(err.message, true); }
});

// ── QR full screen ──────────────────────────────────────────────────────────

function qrDialog(name) {
  const a = appByName(name); if (!a) return;
  const box = openLayer(`<div class="dialog" style="width:min(440px,calc(100vw - 32px));top:8vh"><div class="body" style="align-items:center;text-align:center">
    <img src="/api/qr.svg?text=${encodeURIComponent(a.url)}" alt="QR code for ${esc(a.url)}" style="width:min(340px,70vw);height:auto;image-rendering:pixelated;background:#fff;border-radius:8px">
    <div style="font-size:18px;font-weight:600;word-break:break-all">${esc(a.public ? a.hostname : fmt.host(a.url))}</div>
    <div class="muted">${a.public ? "Scan to open it on a phone." : "Same Wi-Fi only."}</div></div>
    <div class="foot"><button class="btn" data-action="copy" data-text="${esc(a.url)}">${ic("copy")}Copy URL</button><button class="btn primary" data-x>Done</button></div></div>`);
  $("[data-x]", box).onclick = () => closeLayer();
}

// ── command palette ─────────────────────────────────────────────────────────

function palette() {
  const apps = store.state?.apps || [];
  const items = [
    { g: "Go to", label: "Overview", icon: "overview", run: () => go("/") },
    { g: "Go to", label: "Apps", icon: "apps", run: () => go("/apps") },
    { g: "Go to", label: "Deployments", icon: "deploys", run: () => go("/deployments") },
    { g: "Go to", label: "Routes", icon: "routes", run: () => go("/routes") },
    { g: "Go to", label: "Machine", icon: "machine", run: () => go("/machine") },
    { g: "Go to", label: "Databases", icon: "db", run: () => go("/databases") },
    { g: "Go to", label: "Team", icon: "team", run: () => go("/team") },
    { g: "Action", label: "New database", icon: "db", run: () => newDbDialog() },
    { g: "Action", label: "New app", icon: "plus", hint: "N", run: () => newAppDrawer() },
    ...apps.flatMap((a) => [
      { g: "App", label: a.name, icon: "box", hint: STATUS_WORDS[a.status], run: () => go(`/apps/${a.name}`) },
      ...(a.adopted ? [] : [{ g: a.name, label: `Logs for ${a.name}`, icon: "terminal", run: () => go(`/apps/${a.name}/logs`) }]),
      ...(a.repo ? [{ g: a.name, label: `Redeploy ${a.name}`, icon: "refresh", run: () => doAction("redeploy", a.name) }] : []),
      ...(a.adopted ? [] : [{ g: a.name, label: `Restart ${a.name}`, icon: "refresh", run: () => doAction("restart", a.name) }]),
      { g: a.name, label: `Open ${a.public ? a.hostname : a.url}`, icon: "ext", run: () => window.open(a.url, "_blank", "noopener") },
      { g: a.name, label: `QR code for ${a.name}`, icon: "qr", run: () => qrDialog(a.name) },
      { g: a.name, label: `Environment of ${a.name}`, icon: "key", run: () => go(`/apps/${a.name}/environment`) },
    ]),
    { g: "Action", label: "Toggle dark theme", icon: "moon", run: () => toggleTheme() },
    { g: "Action", label: "Sign out", icon: "logout", run: () => doAction("logout") },
  ];
  let sel = 0, shown = items;
  const box = openLayer(`<div class="dialog palette"><input id="pal-q" placeholder="Search apps and actions" aria-label="Search apps and actions" autofocus autocomplete="off"><ul id="pal-list" role="listbox"></ul></div>`);
  const list = $("#pal-list", box), q = $("#pal-q", box);
  const paint = () => {
    list.innerHTML = shown.length ? shown.slice(0, 40).map((it, i) => `<li role="option" data-i="${i}" aria-selected="${i === sel}">${ic(it.icon)}<span>${esc(it.label)}</span><span class="hint">${esc(it.hint || it.g)}</span></li>`).join("") : `<div class="none">Nothing matches.</div>`;
    list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  };
  const score = (it, s) => { const t = `${it.label} ${it.g}`.toLowerCase(); if (t.startsWith(s)) return 0; const i = t.indexOf(s); if (i > -1) return 1 + i / 100; let j = 0; for (const c of t) if (c === s[j]) j++; return j === s.length ? 5 : -1; };
  q.addEventListener("input", () => { const s = q.value.toLowerCase().trim(); shown = s ? items.map((it) => [score(it, s), it]).filter(([sc]) => sc >= 0).sort((a, b) => a[0] - b[0]).map(([, it]) => it) : items; sel = 0; paint(); });
  q.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, Math.min(shown.length, 40) - 1); paint(); e.preventDefault(); }
    if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); paint(); e.preventDefault(); }
    if (e.key === "Enter" && shown[sel]) { const it = shown[sel]; closeLayer(); it.run(); }
  });
  list.addEventListener("click", (e) => { const li = e.target.closest("[data-i]"); if (!li) return; const it = shown[+li.dataset.i]; closeLayer(); it.run(); });
  list.addEventListener("mousemove", (e) => { const li = e.target.closest("[data-i]"); if (li && +li.dataset.i !== sel) { sel = +li.dataset.i; paint(); } });
  paint();
}

// ── actions ─────────────────────────────────────────────────────────────────

function toggleTheme() {
  const dark = document.documentElement.dataset.theme !== "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  try { localStorage.setItem("hoist-theme", dark ? "dark" : "light"); } catch {}
  paintChrome();
  if (view) for (const key of Object.keys(view.parts || {})) renderPart(key, true);
}

async function doAction(action, name, el) {
  try {
    if (action === "new-app") return newAppDrawer();
    if (action === "new-db") return newDbDialog(el?.dataset.engine);
    if (action === "reveal-db") {
      const code = $("#db-url"); const show = code.dataset.masked === "1";
      code.textContent = show ? el.dataset.url : el.dataset.masked; code.dataset.masked = show ? "0" : "1";
      el.innerHTML = `${ic(show ? "eyeoff" : "eye", 14)}${show ? "Hide" : "Show"}`;
      $('[data-part="route"]').dataset.hold = show ? "1" : "";
      return;
    }
    if (action === "backup") { const r = await post(`/api/apps/${name}/backup`); return jobDrawer(r.job, { title: `Backing up ${name}` }); }
    if (action === "restore") {
      const ok = await confirmDialog({ title: `Restore ${name} from ${el.dataset.file}?`, body: "Everything written since that backup is replaced. Take a fresh backup first if you might want it back.", ok: "Restore", danger: true });
      if (!ok) return;
      const r = await post(`/api/apps/${name}/restore`, { file: el.dataset.file });
      return jobDrawer(r.job, { title: `Restoring ${name}` });
    }
    if (action === "backup-schedule") {
      const on = el.dataset.on === "1";
      await post(`/api/apps/${name}/backup-schedule`, { on });
      toast(on ? `${name} is backed up every day` : `Daily backups of ${name} off`); return poll();
    }
    if (action === "previews") {
      const on = el.dataset.on === "1";
      await post(`/api/apps/${name}/previews`, { on });
      toast(on ? `Every branch of ${name} now gets a preview` : `Branch previews off for ${name}`);
      return poll();
    }
    if (action === "remove-user") {
      const who = el.dataset.user;
      const ok = await confirmDialog({ title: `Remove ${who}?`, body: `${esc(who)}'s link stops working at once. Their entries stay in the audit log.`, ok: "Remove", danger: true });
      if (!ok) return;
      await post(`/api/team/${who}/remove`); toast(`${who} removed`); return poll();
    }
    if (action === "theme") return toggleTheme();
    if (action === "logout") { await post("/api/logout"); location.replace("/"); return; }
    if (action === "copy") return copyText(el.dataset.text);
    if (action === "qr") return qrDialog(name);
    if (action === "show-job") { const j = await api(`/api/jobs/${el.dataset.job}`); return jobDrawer(j); }
    if (action === "app-menu") return appMenu(el, name);
    if (action === "deploy-log") {
      const h = (store.detail[name]?.history || []).find((x) => x.at === el.dataset.at); if (!h) return;
      const box = openLayer(`<div class="drawer"><div class="drawer-head"><h2 style="flex:1">${esc(name)} · <span class="mono" style="font-size:15px">${fmt.sha(h.sha)}</span></h2>${h.status === "failed" ? '<span class="pill failed">Failed</span>' : '<span class="pill ok">Built</span>'}<button class="btn ghost icon" data-x aria-label="Close">${ic("x")}</button></div>
        <div class="drawer-body"><div><strong>${esc(fmt.message(h.commit))}</strong><div class="muted">${esc(fmt.author(h.commit))} · ${esc(fmt.when(h.at))} · ${h.seconds != null ? fmt.dur(h.seconds) : ""}</div></div><div class="term" style="height:auto;flex:1">${(h.log || []).map(termLine).join("")}</div></div></div>`);
      $("[data-x]", box).onclick = () => closeLayer();
      return;
    }
    if (action === "redeploy") {
      const r = await post(`/api/apps/${name}/redeploy`);
      return jobDrawer(r.job, { title: `Redeploying ${name}` });
    }
    if (action === "rollback") {
      const sha = el.dataset.sha;
      const ok = await confirmDialog({ title: `Roll back ${name} to ${sha.slice(0, 7)}?`, body: `“${esc(el.dataset.msg || "")}” is rebuilt and goes live. Auto-deploy pauses so the next push does not undo it; resume it when the fix is in.`, ok: "Roll back" });
      if (!ok) return;
      const r = await post(`/api/apps/${name}/rollback`, { sha });
      return jobDrawer(r.job, { title: `Rolling back ${name}` });
    }
    if (action === "delete") {
      const a = appByName(name);
      const ok = await confirmDialog({ title: `Take down ${name}?`, danger: true, ok: "Take down", typeToConfirm: name,
        body: `The process stops and <strong>${esc(a?.public ? a.hostname : a?.url || name)}</strong> stops answering. ${a?.repo ? "Its watcher and checkout are removed. " : ""}The DNS record and your code are left alone.` });
      if (!ok) return;
      let purge = false;
      if (a?.kind === "db") purge = await confirmDialog({ title: `Delete ${name}'s data too?`, body: `The volume <span class="mono">${esc(a.db?.volume)}</span> holds every row. Keep it and a database with the same name picks it up again; delete it and it is gone for good.`, ok: "Delete the data", danger: true });
      const r = await post(`/api/apps/${name}/delete`, { purge });
      jobDrawer(r.job, { title: `Taking down ${name}` });
      if (current().name === "app") go("/apps");
      return;
    }
    if (action === "stop") {
      const ok = await confirmDialog({ title: `Stop ${name}?`, body: "Its URL answers 502 until you start it again. Config, environment and history are kept.", ok: "Stop", danger: true });
      if (!ok) return;
    }
    if (["restart", "stop", "start", "pause", "resume"].includes(action)) {
      const words = { restart: "Restarting", stop: "Stopping", start: "Starting", pause: "Pausing auto-deploy for", resume: "Resuming auto-deploy for" };
      if (el) { el.disabled = true; }
      toast(`${words[action]} ${name}…`);
      await post(`/api/apps/${name}/${action}`);
      const done = { restart: "restarted", stop: "stopped", start: "started", pause: "will ignore pushes", resume: "deploys on every push again" };
      toast(`${name} ${done[action]}`);
      await poll();
      return;
    }
  } catch (e) {
    if (e.status === 409 && e.data?.job) { toast(e.message, true); return jobDrawer(e.data.job); }
    toast(e.message, true);
  } finally { if (el && el.isConnected) el.disabled = false; }
}

function appMenu(anchor, name) {
  const a = appByName(name); if (!a) return;
  const items = a.kind === "db" ? [
    { label: "Copy connection URL", icon: "copy", run: () => copyText(a.db?.url || "") },
    "-",
  ] : [
    { label: "Open", icon: "ext", run: () => window.open(a.url, "_blank", "noopener") },
    { label: "Copy URL", icon: "copy", run: () => copyText(a.url) },
    { label: "QR code", icon: "qr", run: () => qrDialog(name) },
    "-",
  ];
  if (!a.adopted) items.push({ label: "Logs", icon: "terminal", run: () => go(`/apps/${name}/logs`) });
  if (a.repo) items.push({ label: "Redeploy", icon: "refresh", run: () => doAction("redeploy", name) });
  if (a.repo) items.push(a.repo.paused ? { label: "Resume auto-deploy", icon: "play", run: () => doAction("resume", name) } : { label: "Pause auto-deploy", icon: "pause", run: () => doAction("pause", name) });
  if (!a.adopted) items.push({ label: "Restart", icon: "refresh", run: () => doAction("restart", name) });
  if (!a.adopted) items.push(a.status === "stopped" ? { label: "Start", icon: "play", run: () => doAction("start", name) } : { label: "Stop", icon: "stop", run: () => doAction("stop", name) });
  items.push("-", { label: "Take down…", icon: "trash", danger: true, run: () => doAction("delete", name) });
  openMenu(anchor, items);
}

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (el) { e.preventDefault(); e.stopPropagation(); doAction(el.dataset.action, el.dataset.app, el); return; }
  const row = e.target.closest("tr[data-href]");
  if (row && !e.target.closest("a,button")) go(row.dataset.href);
});

document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); palette(); return; }
  if (e.key === "Escape") { if (menuEl) closeMenu(); else if (layerClose) closeLayer(); else $("#shell").classList.remove("nav-open"); return; }
  if (typing || layerClose || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "n") { e.preventDefault(); newAppDrawer(); }
  if (e.key === "/") { e.preventDefault(); palette(); }
});

$("#search-btn").addEventListener("click", () => palette());
$("#menu-btn").addEventListener("click", () => $("#shell").classList.toggle("nav-open"));
$("#sheet").addEventListener("click", () => $("#shell").classList.remove("nav-open"));

// ── start ───────────────────────────────────────────────────────────────────

mount();
poll();
startPolling();
