
// ── views ───────────────────────────────────────────────────────────────────

const VIEWS = {};

VIEWS.overview = () => ({
  title: "Overview",
  crumbs: [["Overview", "/"]],
  layout: () => `${part("head")}${part("apps")}
    <div class="grid-3-1">${part("machine", "card")}${part("activity", "card")}</div>
    ${part("routes")}`,
  parts: {
    head() {
      const s = store.state;
      if (!s) return `<div class="title-row"><div class="titles"><h1>Overview</h1><div class="skeleton" style="width:340px;margin-top:10px"></div></div></div>`;
      const apps = s.apps.filter((a) => a.kind !== "db"), broken = s.apps.filter(isBroken);
      const lastDeploy = (store.deployments || []).find((d) => d.status !== "failed");
      let finding;
      if (!apps.length) finding = `Nothing is hoisted on <strong>${esc(s.host)}</strong> yet. Deploy a repo and every push to it goes live here.`;
      else if (broken.length) {
        const first = broken.slice(0, 2).map((a) => `<a href="/apps/${a.name}" data-link><strong>${esc(a.name)}</strong></a>: ${esc(diagnose(a))}`).join("; ");
        finding = `<strong>${broken.length} of ${apps.length} apps ${broken.length === 1 ? "isn't" : "aren't"} answering.</strong> ${first}${broken.length > 2 ? `; and ${broken.length - 2} more` : ""}.`;
      } else {
        const failed = apps.find((a) => a.repo?.failed_sha);
        finding = failed
          ? `All ${apps.length} apps answer, but <a href="/apps/${failed.name}/deployments" data-link><strong>${esc(failed.name)}</strong></a>'s latest push ${fmt.sha(failed.repo.failed_sha)} failed to build — it is still serving ${fmt.sha(failed.repo.sha)}.`
          : `All ${fmt.plural(apps.length, "app")} ${apps.length === 1 ? "answers" : "answer"}.` + (lastDeploy ? ` Last deploy: <a href="/apps/${lastDeploy.app}/deployments" data-link><strong>${esc(lastDeploy.app)}</strong></a> <span class="mono">${fmt.sha(lastDeploy.sha)}</span> “${esc(fmt.message(lastDeploy.commit))}”, ${fmt.ago(lastDeploy.at)}.` : "");
      }
      const sys = s.system;
      return `<div class="title-row">
        <div class="titles">
          <h1>${esc(s.host)}</h1>
          <div class="subline"><span>${fmt.plural(apps.length, "app")}</span><span>${sys.cloudflared ? "Tunnel connected" : '<span style="color:var(--negative)">Tunnel down</span>'}${s.domain ? ` · ${esc(s.domain)}` : ""}</span><span>Up ${fmt.dur(sys.uptime)}</span></div>
          <p class="finding ${broken.length ? "bad" : ""}">${finding}</p>
        </div>
        <div class="actions">
          ${broken[0] && !broken[0].adopted ? `<a class="btn" href="/apps/${broken[0].name}/logs" data-link>${ic("terminal")}Logs for ${esc(broken[0].name)}</a>` : ""}
          <button class="btn primary" data-action="new-app">${ic("plus")}New app</button>
        </div>
      </div>`;
    },
    apps() {
      const s = store.state; if (!s) return `<div class="card"><div class="card-body"><div class="skeleton" style="height:120px"></div></div></div>`;
      const apps = s.apps.filter((a) => a.kind !== "db"), dbs = s.apps.filter((a) => a.kind === "db");
      if (!apps.length && !dbs.length) return emptyApps();
      return `<div class="card">
        <div class="card-head"><h2>Apps</h2><div class="right">Public URLs are checked every 30 s</div></div>
        <div class="card-body flush">${apps.length ? appsTable(apps, { compact: true }) : '<div class="empty">No apps yet.</div>'}</div></div>
        ${dbs.length ? `<div class="card" style="margin-top:var(--gap)"><div class="card-head"><h2>Databases</h2><div class="right"><a href="/databases" data-link>Manage</a></div></div><div class="card-body flush">${dbTable(dbs)}</div></div>` : ""}`;
    },
    machine() {
      const s = store.state; if (!s) return "";
      const sys = s.system, pts = store.metrics?.system || [];
      const memPct = 100 * sys.mem_used / sys.mem_total;
      return `<div class="card-head"><h2>This machine</h2><div class="right legend"><span><i style="background:var(--chart-1)"></i>CPU</span><span><i style="background:var(--chart-2)"></i>Memory</span></div></div>
        <div class="card-body">
          <div class="kpis" style="margin-bottom:16px">
            ${kpi("CPU", fmt.pct(sys.cpu, 0), `${sys.cores} cores · load ${sys.load[0]}`)}
            ${kpi("Memory", `${fmt.bytes(sys.mem_used)}`, `of ${fmt.bytesText(sys.mem_total)} · ${memPct.toFixed(0)}%`, memPct > 90)}
            ${kpi("Disk", `${fmt.bytes(sys.disk_used)}`, `of ${fmt.bytesText(sys.disk_total)} · ${(100 * sys.disk_used / sys.disk_total).toFixed(0)}%`, sys.disk_used / sys.disk_total > 0.92)}
          </div>
          ${chart({ height: 150, yMax: 100, yFmt: (v) => `${v}%`, label: "CPU and memory, last hour", markers: deployMarkers(),
            series: [
              { label: "CPU", color: css("--chart-1"), area: true, fmt: (v) => `${v.toFixed(1)}%`, points: pts.map((p) => [p[0], p[1]]) },
              { label: "Memory", color: css("--chart-2"), fmt: (v) => `${v.toFixed(1)}%`, points: pts.map((p) => [p[0], 100 * p[2] / sys.mem_total]) },
            ] })}
        </div>`;
    },
    activity() {
      if (!store.state) return `<div class="card-body"><div class="skeleton" style="height:120px"></div></div>`;
      const jobs = store.state?.jobs || [];
      const deps = (store.deployments || []).slice(0, 8);
      const items = [
        ...jobs.map((j) => ({ t: j.started, html: feedJob(j) })),
        ...deps.map((d) => ({ t: Date.parse(d.at) / 1000, html: feedDeploy(d) })),
      ].sort((a, b) => b.t - a.t).slice(0, 7);
      return `<div class="card-head"><h2>Activity</h2><div class="right"><a href="/deployments" data-link>All deployments</a></div></div>
        <div class="feed">${items.length ? items.map((i) => i.html).join("") : `<div class="empty" style="padding:28px 16px">Deploys, restarts and rollbacks show up here.</div>`}</div>`;
    },
    routes() {
      const rules = (store.state?.routes?.rules || []).filter((r) => r.hostname && !r.managed);
      if (!rules.length) return "";
      return `<div class="card"><div class="card-head"><h2>Other routes on this tunnel</h2><div class="right">Hand-written in the tunnel config · <a href="/routes" data-link>All routes</a></div></div>
        <div class="card-body flush table-wrap"><table class="t stack"><thead><tr><th>Hostname</th><th>Goes to</th><th class="r">Public check</th><th class="r">Uptime, 1 h</th></tr></thead><tbody>
        ${rules.map((r) => `<tr><td><a class="name" href="https://${esc(r.hostname)}" target="_blank" rel="noopener">${esc(r.hostname)}</a></td><td class="sha hide-sm">${esc(r.service)}</td><td class="r">${probeText(r.probe)}</td><td class="r">${r.uptime == null ? '<span class="dash">—</span>' : fmt.pct(r.uptime, r.uptime === 100 ? 0 : 1)}</td></tr>`).join("")}
        </tbody></table></div></div>`;
    },
  },
});

function kpi(label, value, note = "", bad = false, noteCls = "") {
  return `<div class="kpi ${bad ? "bad" : ""}"><div class="label">${label}</div><div class="value">${value}</div>${note ? `<div class="note ${noteCls}">${note}</div>` : ""}</div>`;
}
function deployMarkers(app) {
  return (store.deployments || []).filter((d) => !app || d.app === app).slice(0, 12)
    .map((d) => ({ t: Date.parse(d.at) / 1000, label: d.status === "failed" ? `${fmt.sha(d.sha)} failed` : fmt.sha(d.sha), bad: d.status === "failed" }));
}
function feedJob(j) {
  const icon = j.status === "running" ? `<span class="ic run">${ic("refresh", 16, 'class="spin"')}</span>` : j.status === "ok" ? `<span class="ic ok">${ic("check")}</span>` : `<span class="ic bad">${ic("x")}</span>`;
  return `<a class="feed-item" href="#" data-action="show-job" data-job="${j.id}">${icon}<div class="what"><div>${esc(j.title)}</div><div class="muted mono" style="font-size:12px">${esc(j.command)}</div></div><div class="when">${j.status === "running" ? "running" : fmt.ago(j.ended || j.started)}</div></a>`;
}
function feedDeploy(d) {
  const ok = d.status !== "failed";
  return `<a class="feed-item" href="/apps/${d.app}/deployments" data-link><span class="ic ${ok ? "ok" : "bad"}">${ic(ok ? "git" : "x")}</span>
    <div class="what"><div><strong>${esc(d.app)}</strong> ${ok ? "deployed" : "failed to build"} <span class="mono" style="font-size:12px">${fmt.sha(d.sha)}</span></div><div class="muted">${esc(fmt.message(d.commit))}</div></div>
    <div class="when">${fmt.ago(d.at)}</div></a>`;
}
function emptyApps() {
  return `<div class="card"><div class="empty">
    <h2>Deploy your first app</h2>
    <p style="margin:0;max-width:460px">Point hoist at a GitHub repo: it builds it, runs it on this machine, gives it a public URL and redeploys on every push.</p>
    <button class="btn primary" data-action="new-app">${ic("plus")}New app</button>
    <span class="cmd">hoist up github.com/team/project</span>
  </div></div>`;
}

function appsTable(apps, { compact = false } = {}) {
  return `<div class="table-wrap"><table class="t stack apps-t"><thead><tr>
    <th>App</th><th>Status</th><th class="hide-sm">Source</th><th class="r">Public check</th>${compact ? "" : '<th class="r">Uptime, 1 h</th>'}<th class="r">CPU</th><th class="r">Memory</th><th class="r hide-sm">Last change</th><th></th></tr></thead><tbody>
    ${apps.map((a) => {
      const why = isBroken(a) ? diagnose(a) : "";
      return `<tr class="click" data-href="/apps/${a.name}">
        <td><div class="name">${esc(a.name)}</div><div class="sub">${a.public ? esc(a.hostname) : `${esc(fmt.host(a.url))} · LAN only`}</div></td>
        <td>${statusPill(a.status)}${why ? `<div class="sub" style="color:var(--negative);max-width:240px" title="${esc(why)}">${esc(why)}</div>` : ""}</td>
        <td class="hide-sm">${sourceCell(a)}</td>
        <td class="r">${a.public ? probeText(a.probe) : '<span class="dash">LAN only</span>'}</td>
        ${compact ? "" : `<td class="r hide-sm" ${a.uptime != null && a.uptime < 99 ? 'style="color:var(--negative)"' : ""}>${a.uptime == null ? '<span class="dash">—</span>' : fmt.pct(a.uptime, a.uptime === 100 ? 0 : 1)}</td>`}
        <td class="r hide-sm">${a.cpu == null ? '<span class="dash">—</span>' : fmt.pct(a.cpu)}</td>
        <td class="r hide-sm">${a.memory == null ? '<span class="dash">—</span>' : fmt.bytes(a.memory)}</td>
        <td class="r hide-sm">${a.repo?.deployed ? `<span title="${esc(fmt.when(a.repo.deployed))}">${fmt.ago(a.repo.deployed)}</span>` : `<span title="${esc(fmt.when(a.updated))}">${fmt.ago(a.updated)}</span>`}</td>
        <td class="r"><span class="row-actions"><a class="btn ghost icon sm" href="${esc(a.url)}" target="_blank" rel="noopener" title="Open ${esc(a.url)}" aria-label="Open">${ic("ext")}</a><button class="btn ghost icon sm" data-action="app-menu" data-app="${a.name}" aria-label="More actions for ${esc(a.name)}">${ic("more")}</button></span></td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
}

VIEWS.apps = () => {
  let filter = "", which = "all";
  return {
    title: "Apps",
    crumbs: [["Apps", "/apps"]],
    layout: () => `<div class="title-row"><div class="titles"><h1>Apps</h1><div class="subline">Everything hoist runs or routes on this machine.</div></div>
      <div class="actions"><button class="btn primary" data-action="new-app">${ic("plus")}New app</button></div></div>
      ${part("tabs")}
      <div class="list-toolbar"><input class="input search" id="app-filter" placeholder="Filter by name, branch or URL" aria-label="Filter apps">${part("count", "count")}</div>
      ${part("table")}`,
    mount() {
      $("#app-filter").addEventListener("input", (e) => { filter = e.target.value.toLowerCase(); renderPart("table", true); renderPart("count", true); });
      $("#page").addEventListener("click", (e) => { const t = e.target.closest("[data-tab]"); if (!t) return; e.preventDefault(); which = t.dataset.tab; renderPart("tabs", true); renderPart("table", true); renderPart("count", true); });
    },
    parts: {
      tabs() {
        const apps = (store.state?.apps || []).filter((a) => a.kind !== "db");
        const n = { all: apps.length, broken: apps.filter(isBroken).length, git: apps.filter((a) => a.repo).length, stopped: apps.filter((a) => a.status === "stopped").length };
        const tab = (k, label) => `<a href="#" data-tab="${k}" aria-selected="${which === k}">${label} <span class="count">${n[k]}</span></a>`;
        return `<div class="tabs" role="tablist">${tab("all", "All")}${tab("broken", "Not answering")}${tab("git", "From git")}${tab("stopped", "Stopped")}</div>`;
      },
      count() { return `${fmt.plural(rows().length, "app")}`; },
      table() {
        if (!store.state) return `<div class="skeleton" style="height:160px"></div>`;
        if (!store.state.apps.length) return emptyApps();
        const r = rows();
        if (!r.length) return `<div class="card"><div class="empty">No apps match “${esc(filter)}”.</div></div>`;
        return `<div class="card">${appsTable(r)}</div>`;
      },
    },
  };
  function rows() {
    return (store.state?.apps || []).filter((a) => a.kind !== "db").filter((a) =>
      (which === "all" || (which === "broken" && isBroken(a)) || (which === "git" && a.repo) || (which === "stopped" && a.status === "stopped")) &&
      (!filter || [a.name, a.hostname, a.url, a.repo?.branch, a.repo?.url].some((v) => String(v || "").toLowerCase().includes(filter))));
  }
};

// ── one app ─────────────────────────────────────────────────────────────────

VIEWS.app = (name, tab = "overview") => {
  tab = tab || "overview";
  const d = () => store.detail[name];
  const a = () => d()?.row || appByName(name);
  const base = `/apps/${name}`;
  const TABS = [["overview", "Overview"], ["deployments", "Deployments"], ["logs", "Logs"], ["console", "Console"], ["environment", "Environment"], ["settings", "Settings"]];
  const v = {
    title: name,
    crumbs: [["Apps", "/apps"], [name, base]],
    layout: () => `${part("head")}${part("tabs")}<div id="tab-body">${tabLayout()}</div>`,
    parts: {
      head() {
        const app = a();
        if (!app) return store.state ? `<div class="empty"><h2>No app called ${esc(name)}</h2><a class="btn" href="/apps" data-link>All apps</a></div>` : `<div class="skeleton" style="width:300px;height:28px"></div>`;
        const busy = (store.state?.jobs || []).find((j) => j.app === name && j.status === "running");
        const primary = app.repo
          ? `<button class="btn primary" data-action="redeploy" data-app="${name}" ${busy ? "disabled" : ""}>${ic("refresh", 16, busy ? 'class="spin"' : "")}${busy ? "Working…" : "Redeploy"}</button>`
          : app.adopted ? "" : `<button class="btn primary" data-action="restart" data-app="${name}">${ic("refresh")}Restart</button>`;
        const why = isBroken(app) ? diagnose(app) : app.repo?.failed_sha ? diagnose(app) : "";
        return `<div class="title-row">
          <div class="titles">
            <h1>${esc(name)} ${statusPill(app.status)}${app.repo?.paused ? '<span class="pill paused">Auto-deploy paused</span>' : ""}${busy ? `<span class="pill building"><span class="d"></span>${esc(busy.title)}</span>` : ""}</h1>
            <div class="subline">
              ${app.kind === "db" ? `<span>${ic("db", 14, 'style="vertical-align:-2px"')} ${esc(ENGINE_LABEL[app.db?.engine] || "")} · ${esc(app.db?.image || "")}</span>` : `<a href="${esc(app.url)}" target="_blank" rel="noopener">${esc(app.public ? app.hostname : app.url)} ${ic("ext", 13, 'style="vertical-align:-2px"')}</a>
              <button class="btn ghost icon sm" data-action="copy" data-text="${esc(app.url)}" aria-label="Copy URL" title="Copy URL">${ic("copy", 14)}</button>`}
              ${app.preview_of ? `<span>Preview of <a href="/apps/${esc(app.preview_of)}" data-link>${esc(app.preview_of)}</a></span>` : ""}
              ${app.repo ? `<span>${ic("git", 14, 'style="vertical-align:-2px"')} ${esc(fmt.repo(app.repo.url))} · ${esc(app.repo.branch)}</span>` : ""}
              <span class="mono" style="font-size:12px">:${app.port}</span>
            </div>
            ${why ? `<p class="finding bad"><strong>${esc(why.charAt(0).toUpperCase() + why.slice(1))}.</strong></p>` : ""}
          </div>
          <div class="actions">
            ${app.kind === "db" ? "" : `<button class="btn" data-action="qr" data-app="${name}">${ic("qr")}QR</button>`}
            ${app.adopted ? "" : `<a class="btn" href="${base}/logs" data-link>${ic("terminal")}Logs</a>`}
            ${primary}
            <button class="btn icon" data-action="app-menu" data-app="${name}" aria-label="More actions">${ic("more")}</button>
          </div>
        </div>`;
      },
      tabs() {
        const app = a(); const hist = d()?.history?.length;
        return `<div class="tabs" role="tablist">${TABS.filter(([k]) => !(app?.adopted && ["logs", "console", "environment"].includes(k)) && !(k === "console" && store.state?.me !== "owner") && !(app?.kind === "db" && ["deployments", "environment"].includes(k))).map(([k, label]) =>
          `<a href="${k === "overview" ? base : `${base}/${k}`}" data-link role="tab" aria-selected="${tab === k}">${label}${k === "deployments" && hist ? ` <span class="count">${hist}</span>` : ""}${k === "environment" && app?.env_keys?.length ? ` <span class="count">${app.env_keys.length}</span>` : ""}</a>`).join("")}</div>`;
      },
    },
  };
  const tabViews = { overview: appOverview, deployments: appDeployments, logs: appLogs, console: appConsole, environment: appEnv, settings: appSettings };
  const sub = tabViews[tab](name, a, d, v);
  Object.assign(v.parts, sub.parts || {});
  function tabLayout() { return sub.layout(); }
  v.mount = () => sub.mount?.();
  v.unmount = () => sub.unmount?.();
  v.refresh = () => sub.refresh?.();
  return v;
};

function appOverview(name, a, d) {
  return {
    layout: () => `${part("route")}${part("live")}${part("previews")}${part("kpis")}<div class="grid-3-1">${part("charts", "card")}${part("qr", "card")}</div>${part("stackbackups", "card")}${part("recent")}`,
    parts: {
      route() { const app = a(); return !app ? "" : app.kind === "db" ? dbConnectCard(app) : routeStrip(app); },
      stackbackups() { const app = a(); return app?.kind === "compose" && d()?.backups ? backupsCard(name, app, d()) : ""; },
      previews() {
        const app = a(); if (!app?.repo || app.preview_of) return "";
        const kids = (store.state?.apps || []).filter((x) => x.preview_of === name);
        const failed = Object.entries(app.repo.preview_failed || {});
        if (!app.repo.previews && !kids.length) return "";
        return `<div class="card"><div class="card-head"><h2>Branch previews</h2><div class="right">${app.repo.previews ? "Every branch gets its own app, removed when the branch is deleted" : "Off — existing previews stay until taken down"}</div></div>
          <div class="card-body flush">${kids.length ? appsTable(kids, { compact: true }) : `<div class="empty" style="padding:24px 16px">Push a branch other than ${esc(app.repo.branch)} and its preview appears here within a minute.</div>`}
          ${failed.length ? `<div style="padding:10px 16px;border-top:1px solid var(--rule);color:var(--negative)">${failed.map(([b, sha]) => `${esc(b)} <span class="sha">${fmt.sha(sha)}</span> failed to build — push a fix to retry`).join("<br>")}</div>` : ""}</div></div>`;
      },
      live() {
        const app = a(); if (!app?.repo) return "";
        const last = app.repo.last || {};
        return `<div class="live-card">
          <div><div class="k">Live deployment</div><div class="commit" style="margin-top:4px">${esc(fmt.message(app.repo.commit) || "—")}</div>
            <div class="meta"><span class="mono" style="font-size:12px">${fmt.sha(app.repo.sha)}</span><span>${esc(fmt.author(app.repo.commit) || "")}</span><span>${ic("git", 13, 'style="vertical-align:-2px"')} ${esc(app.repo.branch)}</span></div></div>
          <div><div class="k">Deployed</div><div class="v" title="${esc(fmt.when(app.repo.deployed))}">${fmt.ago(app.repo.deployed)}</div><div class="k" style="margin-top:2px">${esc(last.trigger ? ({ push: "by a push", manual: "redeployed", rollback: "as a rollback", initial: "first deploy" }[last.trigger] || last.trigger) + (last.by ? ` by ${last.by}` : "") : "")}</div></div>
          <div><div class="k">Build time</div><div class="v num">${last.seconds != null ? fmt.dur(last.seconds) : "—"}</div><div class="k" style="margin-top:2px">${app.repo.build?.length ? fmt.plural(app.repo.build.length, "step") : "no build step"}</div></div>
          <div><div class="k">Auto-deploy</div><div class="v">${app.repo.paused ? '<span style="color:var(--warning)">Paused</span>' : app.repo.watching ? "On every push" : '<span style="color:var(--negative)">Watcher stopped</span>'}</div><div class="k" style="margin-top:2px">${app.repo.paused ? "pushes are ignored" : "checks every 20 s"}</div></div>
          <div><div class="k">Running since</div><div class="v">${app.since ? fmt.ago(app.since).replace(" ago", "") : "—"}</div><div class="k" style="margin-top:2px">${app.restarts ? `${fmt.plural(app.restarts, "crash restart")}` : "no crash restarts"}</div></div>
        </div>`;
      },
      kpis() {
        const app = a(); if (!app) return "";
        const probes = d()?.probes || [];
        const ms = probes.filter((p) => p.ms != null).map((p) => p.ms).sort((x, y) => x - y);
        const p50 = ms.length ? ms[Math.floor(ms.length / 2)] : null;
        const fails = probes.filter((p) => !p.up).length;
        if (app.kind === "db") return `<div class="kpis">
          ${kpi("Listening on", `<span style="font-size:20px" class="mono">:${app.port}</span>`, "this machine only")}
          ${kpi("CPU", app.cpu == null ? "—" : fmt.pct(app.cpu), "the database container")}
          ${kpi("Memory", app.memory == null ? "—" : fmt.bytes(app.memory), app.limits?.memory ? `capped at ${esc(app.limits.memory)}` : "no limit set")}
          ${kpi("Running since", app.since ? fmt.ago(app.since).replace(" ago", "") : "—", app.restarts ? fmt.plural(app.restarts, "crash restart") : "no crash restarts")}
        </div>`;
        return `<div class="kpis">
          ${app.kind === "db" ? kpi("Listening on", `<span style="font-size:20px" class="mono">:${app.port}</span>`, "this machine only") : app.public ? kpi("Public check", app.probe ? (app.probe.code ? `${app.probe.code}` : "—") : "—", app.probe ? (app.probe.up ? `${app.probe.ms} ms just now` : esc(app.probe.error || `answered ${app.probe.code}`)) : "first check within 30 s", app.probe && !app.probe.up) : kpi("Reachable at", `<span style="font-size:20px">LAN</span>`, esc(fmt.host(app.url)))}
          ${kpi("Uptime, last hour", app.uptime == null ? "—" : fmt.pct(app.uptime, app.uptime === 100 ? 0 : 1), probes.length ? `${fmt.plural(fails, "failed check")} of ${probes.length}` : "no checks yet", app.uptime != null && app.uptime < 99, fails ? "bad" : "")}
          ${kpi("Median response", p50 == null ? "—" : fmt.ms(p50), ms.length ? `slowest ${ms[ms.length - 1]} ms` : "public URLs only")}
          ${kpi("CPU", app.cpu == null ? "—" : fmt.pct(app.cpu), app.kind === "compose" ? `${fmt.plural(app.containers || 0, "container")}` : app.pid ? `PID ${app.pid}` : app.adopted ? "not managed by hoist" : "not running")}
          ${kpi("Memory", app.memory == null ? "—" : fmt.bytes(app.memory), app.memory == null ? "" : `of ${fmt.bytesText(store.state?.system.mem_total)} on this machine`)}
        </div>`;
      },
      charts() {
        const app = a(); const series = d()?.series || []; const probes = d()?.probes || [];
        if (app?.adopted) return `<div class="card-head"><h2>Resources</h2></div><div class="card-body"><div class="empty">hoist routes to this process but does not run it, so it has no resource figures here.</div></div>`;
        return `<div class="card-head"><h2>Last hour</h2><div class="right legend"><span><i style="background:var(--chart-1)"></i>Memory</span>${app?.public ? '<span><i style="background:var(--chart-2)"></i>Response time</span>' : ""}<span>Dashed lines are deploys</span></div></div>
          <div class="card-body" style="display:flex;flex-direction:column;gap:16px">
            ${chart({ height: 130, label: "Memory", yFmt: (v) => fmt.bytesText(v), markers: deployMarkers(name), empty: "No samples yet — figures appear while the dashboard runs.",
              series: [{ label: "Memory", color: css("--chart-1"), area: true, fmt: (v) => fmt.bytesText(v), points: series.map((p) => [p[0], p[2]]) }] })}
            ${app?.public ? chart({ height: 110, label: "Response time", yFmt: (v) => `${Math.round(v)} ms`, markers: deployMarkers(name), empty: "The first public check runs within 30 seconds.",
              series: [{ label: "Response", color: css("--chart-2"), fmt: (v) => `${v} ms`, points: probes.map((p) => [p.t, p.ms]) }] }) : ""}
          </div>`;
      },
      qr() {
        const app = a(); if (!app) return "";
        if (app.kind === "db") return backupsCard(name, app, d());
        return `<div class="card-head"><h2>Open on a phone</h2></div>
          <div class="qr-card"><img src="/api/qr.svg?text=${encodeURIComponent(app.url)}" alt="QR code for ${esc(app.url)}" width="132" height="132">
          <div><div class="url">${esc(app.public ? app.hostname : fmt.host(app.url))}</div><p class="muted" style="margin:6px 0 12px">Point a judge's camera at it.${app.public ? "" : " Works on the same Wi-Fi only."}</p>
          <div class="actions"><button class="btn sm" data-action="copy" data-text="${esc(app.url)}">${ic("copy", 14)}Copy</button><button class="btn sm" data-action="qr" data-app="${name}">${ic("qr", 14)}Full screen</button></div></div></div>`;
      },
      recent() {
        const hist = d()?.history || []; const app = a();
        if (!app?.repo) return "";
        return `<div class="card"><div class="card-head"><h2>Recent deployments</h2><div class="right"><a href="/apps/${name}/deployments" data-link>View all ${hist.length}</a></div></div>
          <div class="card-body flush">${deployTable(name, hist.slice(0, 5), app)}</div></div>`;
      },
    },
  };
}

function backupsCard(name, app, detail) {
  const b = detail?.backups; const list = b?.backups || [];
  return `<div class="card-head"><h2>Backups</h2><div class="right"><button class="switch" role="switch" aria-checked="${!!b?.daily}" data-action="backup-schedule" data-on="${b?.daily ? "0" : "1"}" data-app="${name}" aria-label="Daily backups"></button>Daily</div></div>
    <div class="card-body flush">${list.length ? `<div class="feed">${list.slice(0, 5).map((x) => `<div class="feed-item" style="grid-template-columns:minmax(0,1fr) auto"><div class="what"><div class="mono" style="font-size:12.5px">${esc(x.file)}</div><div class="muted" style="font-size:12px">${fmt.bytesText(x.size)} · ${fmt.ago(x.at)}</div></div>
      <div class="actions" style="flex-wrap:nowrap"><a class="btn ghost icon sm" href="${apiPath(`/api/apps/${name}/backups/${x.file}`)}" download title="Download" aria-label="Download ${esc(x.file)}">${ic("download", 14)}</a><button class="btn ghost sm" data-action="restore" data-app="${name}" data-file="${esc(x.file)}">${ic("undo", 14)}Restore</button></div></div>`).join("")}</div>`
      : `<div class="empty" style="padding:20px 16px">No backups yet. ${app.kind === "db" ? `Data lives in the volume <span class="mono">${esc(app.db?.volume)}</span>.` : "The stack's Docker volumes are archived together, with its containers paused for a moment."}</div>`}
      <div style="padding:12px 16px;border-top:1px solid var(--rule)"><button class="btn sm" data-action="backup" data-app="${name}">${ic("download", 14)}Back up now</button> <span class="muted" style="font-size:12px;margin-left:8px">The newest 10 are kept${(detail?.backups?.offsite) ? ` · also copied to ${esc(detail.backups.offsite)}` : " · <span class=\"mono\">hoist backup " + esc(name) + " --offsite gdrive:hoist</span> copies them off this machine"}</span></div></div>`;
}

function routeStrip(app) {
  const hops = [];
  const arrow = `<svg class="arrow" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 2l5 4-5 4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
  const hop = (cls, icon, k, v, n, mono = false) => `<div class="hop ${cls}">${hops.length < 4 ? arrow : ""}<div class="k">${ic(icon, 14)}${k}</div><div class="v ${mono ? "mono" : ""}" title="${esc(String(v).replace(/<[^>]+>/g, ""))}">${v}</div><div class="n" title="${esc(String(n).replace(/<[^>]+>/g, ""))}">${n}</div></div>`;
  const r = app.repo, last = r?.last;
  // 1. source
  hops.push(r
    ? hop(r.paused ? "warn" : r.watching ? "" : "bad", "git", "Source", `${esc(r.branch)} @ ${fmt.sha(r.sha)}`, r.paused ? "auto-deploy paused" : r.watching ? `${esc(fmt.repo(r.url))}` : "watcher is not running", true)
    : app.template ? hop("", "box", "Source", `Ready-made · ${esc(app.template)}`, "Docker image")
    : app.adopted ? hop("off", "plug", "Source", "Existing process", "started outside hoist")
    : hop("", "folder", "Source", esc((app.workdir || "").split("/").pop()), esc(app.workdir || "")));
  // 2. build
  hops.push(r
    ? (r.failed_sha ? hop("bad", "hammer", "Build", "Last push failed", `${fmt.sha(r.failed_sha)} · serving ${fmt.sha(r.sha)}`)
      : hop("", "hammer", "Build", r.build?.length ? (last?.seconds != null ? `Built in ${fmt.dur(last.seconds)}` : "Built") : "No build step", last ? `${fmt.ago(last.at)}` : ""))
    : hop("off", "hammer", "Build", "Runs from source", "no build on deploy"));
  // 3. process
  const procCls = { up: "", starting: "warn", stopped: "off", failed: "bad", down: "bad" }[app.status] ?? "bad";
  hops.push(app.adopted ? hop(app.status === "up" ? "" : "bad", "box", "Process", "Not managed", app.status === "up" ? "answering" : "not answering")
    : app.kind === "compose" && app.status === "up" ? hop("", "box", "Containers", `${fmt.plural(app.containers || 0, "container")}`, `docker compose · ${app.memory != null ? fmt.bytesText(app.memory) : ""}`)
    : hop(procCls, "box", "Process", app.pid ? `PID ${app.pid}` : STATUS_WORDS[app.status] || app.status,
      app.status === "up" ? `up ${fmt.dur(app.since ? Date.now() / 1000 - app.since : null)}${app.memory != null ? ` · ${fmt.bytesText(app.memory)}` : ""}` : esc(diagnose(app) || "not running")));
  // 4. port
  const listening = app.status === "up" || (app.status === "starting" ? false : app.status === "up");
  hops.push(hop(app.status === "up" ? "" : app.status === "stopped" ? "off" : "bad", "plug", "Port", `127.0.0.1:${app.port}`, app.status === "up" ? "listening" : "nothing listening", true));
  // 5. public
  hops.push(app.public
    ? hop(!app.probe ? "off" : app.probe.up ? "" : "bad", "globe", "Public URL", esc(app.hostname),
      app.probe ? (app.probe.up ? `${app.probe.code} in ${app.probe.ms} ms${app.uptime != null ? ` · ${app.uptime}% this hour` : ""}` : esc(app.probe.code ? `answers ${app.probe.code}` : app.probe.error || "unreachable")) : "first check within 30 s")
    : hop("off", "globe", "Network", esc(fmt.host(app.url)), "LAN only, no tunnel"));
  void listening;
  return `<div class="route" aria-label="How a request reaches ${esc(app.name)}">${hops.join("")}</div>`;
}

function deployTable(name, hist, app) {
  if (!hist.length) return `<div class="empty">No deploys recorded yet. Push to ${esc(app?.repo?.branch || "the branch")} and it shows up here within 20 seconds.</div>`;
  const liveIdx = hist.findIndex((h) => h.sha === app?.repo?.sha && h.status !== "failed");
  const TRIG = { push: "Push", manual: "Redeploy", rollback: "Rollback", initial: "First deploy" };
  return `<div class="table-wrap"><table class="t stack"><thead><tr><th>Status</th><th>Commit</th><th class="hide-sm">Trigger</th><th class="r hide-sm">Build</th><th class="r">When</th><th></th></tr></thead><tbody>
    ${hist.map((h, i) => {
      const live = i === liveIdx, failed = h.status === "failed";
      return `<tr>
        <td class="nowrap"><span class="dep-status"><span class="ring ${failed ? "bad" : ""} ${live ? "live" : ""}"></span>${failed ? '<span style="color:var(--negative)">Failed</span>' : live ? "<strong>Live</strong>" : h.status === "unhealthy" ? '<span style="color:var(--warning)">Unhealthy</span>' : '<span class="muted">Ready</span>'}</span></td>
        <td><div class="nowrap"><span class="sha">${fmt.sha(h.sha)}</span> ${esc(fmt.message(h.commit))}</div><div class="sub">${esc(fmt.author(h.commit))}${failed && h.error ? ` · <span style="color:var(--negative)">${esc(h.error)}</span>` : ""}</div></td>
        <td class="hide-sm">${esc(TRIG[h.trigger] || h.trigger || "—")}${h.by ? `<div class="sub">by ${esc(h.by)}</div>` : ""}</td>
        <td class="r hide-sm">${h.seconds != null ? fmt.dur(h.seconds) : "—"}</td>
        <td class="r"><span title="${esc(fmt.when(h.at))}">${fmt.ago(h.at)}</span></td>
        <td class="r nowrap">${h.log?.length ? `<button class="btn ghost sm" data-action="deploy-log" data-app="${name}" data-at="${esc(h.at)}">${ic("terminal", 14)}Log</button>` : ""}${!live && !failed && h.sha !== app?.repo?.sha ? `<button class="btn ghost sm" data-action="rollback" data-app="${name}" data-sha="${esc(h.sha)}" data-msg="${esc(fmt.message(h.commit))}">${ic("undo", 14)}Roll back</button>` : ""}</td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
}

function appDeployments(name, a, d) {
  return {
    layout: () => `${part("banner")}${part("list")}`,
    parts: {
      banner() {
        const app = a(); if (!app) return "";
        if (!app.repo) return `<div class="notice info"><span>${ic("folder")}</span><div class="grow">This app runs from a folder on this machine, so there is no deploy history. Deploy it from a git URL to get push-to-deploy and rollbacks.</div></div>`;
        if (app.repo.paused) return `<div class="notice"><span>${ic("pause")}</span><div class="grow"><strong>Auto-deploy is paused.</strong> Pushes to ${esc(app.repo.branch)} are ignored${app.repo.last?.trigger === "rollback" ? " because you rolled back" : ""}. Resume to deploy the newest commit on the next check.</div><button class="btn" data-action="resume" data-app="${name}">${ic("play")}Resume</button></div>`;
        return `<div class="notice info"><span>${ic("git")}</span><div class="grow">Every push to <strong>${esc(app.repo.branch)}</strong> on ${esc(fmt.repo(app.repo.url))} deploys here within 20 seconds. A push that fails to build never replaces the live version.</div><button class="btn" data-action="pause" data-app="${name}">${ic("pause")}Pause</button></div>`;
      },
      list() {
        const app = a(); if (!app?.repo) return "";
        return `<div class="card">${deployTable(name, d()?.history || [], app)}</div>`;
      },
    },
  };
}

function appLogs(name, a) {
  let kind = "app", follow = true, wrap = false, q = "", es = null, lines = [], dropped = 0;
  const MAX = 4000;
  const lineHtml = (l) => {
    const cls = l.p <= 3 ? "err" : l.p === 4 ? "warn" : /^\$ /.test(l.msg) ? "cmd" : "";
    let m = esc(l.msg);
    if (q) m = m.replace(new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/&/g, "&amp;").replace(/</g, "&lt;"), "gi"), (x) => `<mark>${x}</mark>`);
    return `<div class="l ${cls}"><code class="ts">${esc(fmt.clock(l.t))}</code><code class="m">${m || " "}</code></div>`;
  };
  const visible = () => (q ? lines.filter((l) => l.msg.toLowerCase().includes(q.toLowerCase())) : lines);
  function paint() {
    const el = $("#logview"); if (!el) return;
    const v = visible();
    el.innerHTML = (dropped ? `<div class="gap">${dropped} older lines trimmed from view</div>` : "") +
      (v.length ? v.map(lineHtml).join("") : `<div class="empty">${q ? `No lines match “${esc(q)}”.` : kind === "app" ? "No output yet. Anything the app prints to stdout or stderr appears here." : "No deploys have run since this app was created."}</div>`);
    $("#log-count").textContent = q ? `${v.length} of ${lines.length} lines` : `${lines.length} lines`;
    if (follow) el.scrollTop = el.scrollHeight;
  }
  function append(l) {
    lines.push(l);
    if (lines.length > MAX) { lines.shift(); dropped++; }
    const el = $("#logview"); if (!el) return;
    if (q && !l.msg.toLowerCase().includes(q.toLowerCase())) { $("#log-count").textContent = `${visible().length} of ${lines.length} lines`; return; }
    $(".empty", el)?.remove();
    el.insertAdjacentHTML("beforeend", lineHtml(l));
    if (el.children.length > MAX + 1) el.firstElementChild.remove();
    $("#log-count").textContent = q ? `${visible().length} of ${lines.length} lines` : `${lines.length} lines`;
    if (follow) el.scrollTop = el.scrollHeight;
  }
  async function load() {
    es?.close(); lines = []; dropped = 0;
    $("#logview").innerHTML = `<div class="empty">Loading…</div>`;
    try {
      const r = await api(`/api/apps/${name}/logs?kind=${kind}&n=1000`);
      lines = r.lines; $("#log-unit").textContent = r.unit; paint();
    } catch (e) { $("#logview").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    es = new EventSource(apiPath(`/api/apps/${name}/logs/stream?kind=${kind}`));
    es.addEventListener("line", (e) => append(JSON.parse(e.data)));
    es.onerror = () => { $("#log-live").textContent = "Reconnecting…"; };
    es.onopen = () => { $("#log-live").textContent = "Streaming"; };
  }
  return {
    layout: () => `<div class="logs-toolbar">
        <div class="seg" role="group" aria-label="Which log"><button data-kind="app" aria-pressed="true">App output</button><button data-kind="deploys" aria-pressed="false">Deploys</button></div>
        <input class="input search" id="log-q" placeholder="Find in logs" aria-label="Find in logs">
        <span class="muted" id="log-count"></span>
        <span class="spacer"></span>
        <span class="live" style="display:flex"><span class="beat"></span><span id="log-live">Connecting</span></span>
        <button class="btn sm" id="log-follow" aria-pressed="true">${ic("arrow", 14, 'style="transform:rotate(90deg)"')}Follow</button>
        <button class="btn sm" id="log-wrap" aria-pressed="false">Wrap</button>
        <button class="btn sm icon" id="log-dl" title="Download" aria-label="Download log">${ic("download", 14)}</button>
      </div>
      <div class="logview" id="logview" tabindex="0" aria-label="Log output"></div>
      <div class="muted" style="font-size:12px">From <span class="mono" id="log-unit"></span> in the user journal. Lines with errors are red.</div>`,
    mount() {
      $$(".seg [data-kind]").forEach((b) => b.addEventListener("click", () => {
        kind = b.dataset.kind; $$(".seg [data-kind]").forEach((x) => x.setAttribute("aria-pressed", x === b)); load();
      }));
      $("#log-q").addEventListener("input", (e) => { q = e.target.value; paint(); });
      const fb = $("#log-follow");
      fb.addEventListener("click", () => { follow = !follow; fb.setAttribute("aria-pressed", follow); fb.classList.toggle("primary", follow); if (follow) paint(); });
      fb.classList.add("primary");
      $("#log-wrap").addEventListener("click", (e) => { wrap = !wrap; e.currentTarget.setAttribute("aria-pressed", wrap); e.currentTarget.classList.toggle("primary", wrap); $("#logview").classList.toggle("wrap", wrap); });
      $("#log-dl").addEventListener("click", () => {
        const blob = new Blob([lines.map((l) => `${new Date(l.t * 1000).toISOString()} ${l.msg}`).join("\n")], { type: "text/plain" });
        const u = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = u; link.download = `${name}-${kind}.log`; link.click(); URL.revokeObjectURL(u);
      });
      $("#logview").addEventListener("scroll", (e) => {
        const el = e.currentTarget; const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        if (!atBottom && follow) { follow = false; fb.setAttribute("aria-pressed", false); fb.classList.remove("primary"); }
      });
      load();
    },
    unmount() { es?.close(); },
  };
}

let termSession = null;
function loadOnce(src, css) {
  if (css && !document.querySelector(`link[href="${css}"]`)) { const l = document.createElement("link"); l.rel = "stylesheet"; l.href = css; document.head.appendChild(l); }
  if (document.querySelector(`script[src="${src}"]`)) return Promise.resolve();
  return new Promise((ok, bad) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = bad; document.head.appendChild(s); });
}
async function openTerminal(name, a) {
  termSession?.close();
  const note = $("#term-note");
  try {
    await loadOnce("/static/vendor/xterm.js", "/static/vendor/xterm.css");
    await loadOnce("/static/vendor/addon-fit.js");
  } catch { note.textContent = "Could not load the terminal."; return; }
  const host = $("#xterm"); if (!host) return;
  if (currentServer !== "local") { note.textContent = "The interactive terminal opens on that machine's own dashboard. “Run one command” works from here."; return; }
  const term = new window.Terminal({ cursorBlink: true, fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 13, theme: { background: "#0c0e0d", foreground: "#e6ebe8", cursor: "#4cc27f", selectionBackground: "#2a3a30" }, scrollback: 5000 });
  const fit = new window.FitAddon.FitAddon(); term.loadAddon(fit); term.open(host); fit.fit();
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/apps/${name}/terminal`);
  ws.binaryType = "arraybuffer";
  const sendSize = () => { if (ws.readyState === 1) ws.send(`r${term.cols},${term.rows}`); };
  ws.onopen = () => { const app = a(); note.textContent = `Connected — ${app?.kind === "db" ? "a shell inside the database container" : app?.kind === "compose" ? "a shell inside the stack's web service" : `a login shell in ${app?.workdir || "the app folder"} with the app's environment`}. Closing the tab ends the session; it is written to the audit log.`; sendSize(); term.focus(); };
  ws.onmessage = (e) => term.write(new Uint8Array(e.data));
  ws.onclose = () => { note.textContent = "Session ended. Switch tabs and back to start a new one."; };
  term.onData((d) => { if (ws.readyState === 1) ws.send("d" + d); });
  const ro = new ResizeObserver(() => { try { fit.fit(); sendSize(); } catch {} }); ro.observe(host);
  termSession = { close() { ro.disconnect(); try { ws.close(); } catch {} term.dispose(); termSession = null; }, focus() { term.focus(); }, send(text) { if (ws.readyState !== 1) return false; ws.send("d" + text); term.focus(); return true; } };
}

function appConsole(name, a) {
  const history = [];
  let hIdx = -1;
  const chipsFor = () => {
    const app = a();
    if (app?.kind === "db") return { postgres: ["psql -U hoist -c '\\dt'", "psql -U hoist -c '\\l'", "env | sort", "df -h /var/lib/postgresql/data"], mysql: ["mysql -u hoist -p -e 'show databases'", "env | sort", "df -h /var/lib/mysql"], mariadb: ["mariadb -u hoist -p -e 'show databases'", "env | sort", "df -h /var/lib/mysql"], redis: ["redis-cli --no-auth-warning info memory | head -8", "df -h /data", "ls -la /data"], mongo: ["mongosh --quiet --eval 'db.stats()'", "env | sort", "df -h /data/db"] }[app.db?.engine] || ["ls", "env | sort"];
    if (app?.kind === "compose") return ["ls -la", "env | sort | head -20", "ps aux | head", "df -h", "cat /etc/os-release | head -3"];
    return ["ls -la", "env | sort", "git log --oneline -5", "ps aux --sort=-%mem | head -5", "df -h ."];
  };
  const chipsHtml = () => chipsFor().map((c) => `<button type="button" class="btn sm" data-chip="${esc(c)}"><span class="mono">${esc(c)}</span></button>`).join("");
  const examples = () => {
    const app = a();
    if (app?.kind === "db") return { postgres: "psql -U hoist -c '\\dt'", mysql: "mysql -u hoist -p$MYSQL_PASSWORD -e 'show tables'", mariadb: "mariadb -u hoist -p$MARIADB_PASSWORD -e 'show tables'", redis: "redis-cli --no-auth-warning -a $(cat /proc/1/cmdline | tr '\\0' ' ' | sed 's/.*requirepass //;s/ .*//') dbsize", mongo: "mongosh --quiet --eval 'db.stats()'" }[app.db?.engine] || "ls";
    if (app?.kind === "compose") return "ls && env | sort | head -20";
    return app?.repo ? "ls && git log -1 2>/dev/null || true" : "ls";
  };
  return {
    parts: {
      cnote() {
        const app = a(); if (!app) return "";
        return `<div class="notice info"><span>${ic("terminal")}</span><div class="grow">Runs one command ${app.kind === "db" ? "inside the database container" : app.kind === "compose" ? "inside the stack's web service" : "in the app's folder with its environment variables"} and streams the output. Good for migrations, seeding and quick checks. Each command is written to the audit log.</div></div>`;
      },
    },
    layout: () => `<div class="seg" role="group" aria-label="Console mode"><button type="button" data-cmode="term" aria-pressed="true">Terminal</button><button type="button" data-cmode="run" aria-pressed="false">Run one command</button></div>
      <div id="term-wrap"><div id="xterm" style="height:300px;padding:8px;background:#0c0e0d;border-radius:var(--radius-card);overflow:hidden"></div>
        <div class="muted" style="font-size:12px;margin-top:6px" id="term-note">Connecting…</div>
        <div class="card" style="margin-top:var(--gap)"><div class="card-head"><h2>Quick commands</h2><div class="right">Click to type one into the terminal</div></div>
          <div class="card-body" style="display:flex;flex-wrap:wrap;gap:8px" id="term-chips">${chipsHtml()}</div></div></div>
      <div id="run-wrap" class="hidden" style="display:flex;flex-direction:column;gap:var(--gap)">${part("cnote")}
      <form class="card" id="console-form"><div class="card-body" style="display:flex;gap:8px;align-items:center">
        <span class="mono" style="color:var(--muted)">$</span>
        <input class="input mono" id="console-cmd" placeholder="${esc(examples())}" autocomplete="off" spellcheck="false" autofocus aria-label="Command">
        <button class="btn primary" type="submit">${ic("play", 14)}Run</button></div></form>
      <div class="term" id="console-out" style="height:calc(100vh - 460px);min-height:280px"><div class="dim">Output appears here.</div></div></div>`,
    unmount() { termSession?.close(); },
    refresh() { const el = $("#term-chips"); if (!el) return; const html = chipsHtml(); if (el.__html !== html) { el.__html = html; el.innerHTML = html; } },
    mount() {
      $$("[data-cmode]").forEach((b) => b.addEventListener("click", () => {
        $$("[data-cmode]").forEach((x) => x.setAttribute("aria-pressed", x === b));
        const term = b.dataset.cmode === "term";
        $("#term-wrap").classList.toggle("hidden", !term); $("#run-wrap").classList.toggle("hidden", term);
        if (term) termSession?.focus(); else $("#console-cmd").focus();
      }));
      $("#term-chips").addEventListener("click", (e) => { const b = e.target.closest("[data-chip]"); if (!b) return; if (!termSession?.send(b.dataset.chip + "\r")) toast("The terminal is not connected", true); });
      openTerminal(name, a);
      const input = $("#console-cmd"), out = $("#console-out");
      input.addEventListener("keydown", (e) => {
        if (e.key === "ArrowUp" && history.length) { hIdx = Math.min(hIdx + 1, history.length - 1); input.value = history[history.length - 1 - hIdx]; e.preventDefault(); }
        if (e.key === "ArrowDown") { hIdx = Math.max(hIdx - 1, -1); input.value = hIdx < 0 ? "" : history[history.length - 1 - hIdx]; e.preventDefault(); }
      });
      $("#console-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const command = input.value.trim() || examples(); history.push(command); hIdx = -1;
        const btn = $("#console-form button"); btn.disabled = true;
        try {
          const r = await post(`/api/apps/${name}/exec`, { command });
          streamJob(r.job, out, (j) => { btn.disabled = false; out.insertAdjacentHTML("beforeend", `<div class="${j.code === 0 ? "dim" : "bad"}">exit ${j.code} · ${fmt.dur(j.ended - j.started)}</div>`); input.select(); });
        } catch (err) { btn.disabled = false; toast(err.message, true); }
      });
    },
  };
}

function parseDotenv(text) {
  const out = {};
  for (let line of text.split(/\r?\n/)) {
    line = line.trim(); if (!line || line.startsWith("#")) continue;
    line = line.replace(/^export\s+/, "");
    const i = line.indexOf("="); if (i < 1) continue;
    const k = line.slice(0, i).trim(); let v = line.slice(i + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    out[k] = v;
  }
  return out;
}

function appEnv(name, a, d) {
  let rows = null, saved = {}, reveal = false;
  const key = (r) => r.k.trim();
  function load() {
    saved = { ...(d()?.env || {}) };
    rows = Object.entries(saved).map(([k, v]) => ({ k, v, orig: k }));
    if (!rows.length) rows.push({ k: "", v: "", orig: null });
  }
  function dirty() {
    const now = Object.fromEntries(rows.filter((r) => key(r)).map((r) => [key(r), r.v]));
    const keys = new Set([...Object.keys(now), ...Object.keys(saved)]);
    return [...keys].filter((k) => now[k] !== saved[k]).length;
  }
  function paint() {
    const el = $("#env-editor"); if (!el || !rows) return;
    const n = dirty();
    el.innerHTML = `<div class="card-head"><h2>Environment variables</h2><div class="right"><button class="btn ghost sm" data-env="reveal">${ic(reveal ? "eyeoff" : "eye", 14)}${reveal ? "Hide values" : "Show values"}</button><button class="btn ghost sm" data-env="paste">${ic("copy", 14)}Paste .env</button></div></div>
      <div class="card-body flush" style="padding-top:12px">
        <div class="env-head"><span>Name</span><span>Value</span><span></span></div>
        ${rows.map((r, i) => {
          const changed = r.orig === null ? key(r) !== "" : saved[r.orig] !== r.v || r.orig !== key(r);
          return `<div class="env-row ${changed ? "changed" : ""}">
            <input class="input" data-i="${i}" data-f="k" value="${esc(r.k)}" placeholder="DATABASE_URL" aria-label="Variable name" spellcheck="false" autocomplete="off">
            <input class="input" data-i="${i}" data-f="v" value="${esc(r.v)}" placeholder="value" aria-label="Value of ${esc(r.k)}" type="${reveal ? "text" : "password"}" spellcheck="false" autocomplete="off">
            <button class="btn ghost icon sm" data-env="del" data-i="${i}" aria-label="Remove ${esc(r.k)}">${ic("x", 14)}</button></div>`;
        }).join("")}
        <div style="padding:6px 16px 12px"><button class="btn sm" data-env="add">${ic("plus", 14)}Add variable</button></div>
        <div class="env-foot"><span class="note">${n ? `${fmt.plural(n, "change")} not saved. Saving restarts ${esc(name)} so it picks them up.` : `${fmt.plural(Object.keys(saved).length, "variable")}. <span class="mono">PORT</span> and <span class="mono">HOIST_APP</span> are set by hoist.`}</span>
          <button class="btn" data-env="reset" ${n ? "" : "disabled"}>Discard</button>
          <button class="btn primary" data-env="save" ${n ? "" : "disabled"}>Save and restart</button></div>
      </div>`;
  }
  return {
    layout: () => `<div class="card" id="env-editor"><div class="card-body"><div class="skeleton" style="height:120px"></div></div></div>`,
    refresh() { if (!rows && d()) { load(); paint(); } },
    mount() {
      if (d()) { load(); paint(); }
      const el = $("#env-editor");
      el.addEventListener("input", (e) => {
        const i = e.target.dataset.i; if (i == null) return;
        rows[i][e.target.dataset.f] = e.target.value;
        const n = dirty(); $(".env-foot .note", el).innerHTML = n ? `${fmt.plural(n, "change")} not saved. Saving restarts ${esc(name)} so it picks them up.` : "No changes.";
        $('[data-env="save"]', el).disabled = !n; $('[data-env="reset"]', el).disabled = !n;
        e.target.closest(".env-row").classList.add("changed");
      });
      el.addEventListener("click", async (e) => {
        const b = e.target.closest("[data-env]"); if (!b) return;
        const act = b.dataset.env;
        if (act === "add") { rows.push({ k: "", v: "", orig: null }); paint(); $$(".env-row .input[data-f=k]", el).pop().focus(); }
        if (act === "del") { rows.splice(+b.dataset.i, 1); if (!rows.length) rows.push({ k: "", v: "", orig: null }); paint(); }
        if (act === "reveal") { reveal = !reveal; paint(); }
        if (act === "reset") { load(); paint(); }
        if (act === "paste") {
          const text = await promptDialog({ title: "Paste a .env file", label: "One KEY=value per line. Comments and export are ignored; existing names are replaced.", textarea: true, ok: "Add variables" });
          if (text == null) return;
          const parsed = parseDotenv(text); const n = Object.keys(parsed).length;
          for (const [k, v] of Object.entries(parsed)) { const r = rows.find((x) => key(x) === k); if (r) r.v = v; else rows.push({ k, v, orig: null }); }
          rows = rows.filter((r) => key(r) || rows.length === 1);
          paint(); toast(`${fmt.plural(n, "variable")} added — save to apply`);
        }
        if (act === "save") {
          const env = {}; const bad = rows.find((r) => key(r) && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(key(r)));
          if (bad) return toast(`“${key(bad)}” is not a valid variable name`, true);
          for (const r of rows) if (key(r)) env[key(r)] = r.v;
          b.disabled = true; b.innerHTML = `${ic("refresh", 14, 'class="spin"')}Saving`;
          try {
            const r = await post(`/api/apps/${name}/env`, { env });
            saved = env; rows = Object.entries(env).map(([k, v]) => ({ k, v, orig: k })); if (!rows.length) rows.push({ k: "", v: "", orig: null });
            toast(r.restarted ? `Saved ${fmt.plural(r.count, "variable")} and restarted ${name}` : `Saved ${fmt.plural(r.count, "variable")}`);
            paint(); poll();
          } catch (err) { toast(err.message, true); paint(); }
        }
      });
    },
  };
}

function appSettings(name, a) {
  return {
    layout: () => `${part("info", "card")}${a()?.adopted || a()?.kind === "compose" ? "" : limitsCard(name, a())}${part("deploy", "card")}${part("danger", "card")}`,
    mount() {
      const f = $("#limits-form"); if (!f) return;
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        const b = $("button[type=submit]", f); b.disabled = true;
        try {
          await post(`/api/apps/${name}/limits`, { memory: $("#lim-mem").value.trim(), cpu: $("#lim-cpu").value.trim() });
          toast(`Limits saved and ${name} restarted`); poll();
        } catch (err) { toast(err.message, true); } finally { b.disabled = false; }
      });
    },
    parts: {
      info() {
        const app = a(); if (!app) return "";
        const rows = [
          ["Public URL", app.public ? `<a href="${esc(app.url)}" target="_blank" rel="noopener">${esc(app.url)}</a>` : `${esc(app.url)} <span class="muted">(LAN only)</span>`],
          ["Port", `<span class="mono">${app.port}</span>`],
          ["Start command", app.cmd ? `<code>${esc(app.cmd)}</code>` : '<span class="dash">—</span>'],
          ["Working directory", `<code>${esc(app.workdir || "—")}</code>`],
          ["systemd unit", app.adopted ? '<span class="muted">none — hoist does not run this process</span>' : `<code>hoist-${esc(name)}.service</code> <span class="muted">(user unit)</span>`],
          ["Created", `${esc(fmt.when(app.created))}`],
        ];
        return `<div class="card-head"><h2>Runtime</h2></div><div class="dl">${rows.map(([k, v]) => `<div class="dt">${k}</div><div class="dd">${v}</div>`).join("")}</div>`;
      },
      deploy() {
        const app = a(); if (!app?.repo) return "";
        const r = app.repo;
        return `<div class="card-head"><h2>Git deploys</h2></div><div class="dl">
          <div class="dt">Repository</div><div class="dd"><code>${esc(r.url)}</code></div>
          <div class="dt">Branch</div><div class="dd">${esc(r.branch)}</div>
          <div class="dt">Build steps</div><div class="dd">${r.build?.length ? r.build.map((s) => `<div><code>${esc(s)}</code></div>`).join("") : '<span class="muted">none</span>'}</div>
          <div class="dt">Auto-deploy</div><div class="dd" style="display:flex;align-items:center;gap:12px"><button class="switch" role="switch" aria-checked="${!r.paused}" data-action="${r.paused ? "resume" : "pause"}" data-app="${name}" aria-label="Auto-deploy"></button><span>${r.paused ? "Paused — pushes are ignored" : "On — checks every 20 seconds"}</span></div>
          ${app.preview_of ? "" : `<div class="dt">Instant deploys</div><div class="dd" style="display:flex;flex-direction:column;gap:8px"><div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap"><span>${r.webhook ? "A webhook is set up — pushes deploy within seconds." : "Off — pushes are noticed by the 20-second check."}</span><button class="btn sm" data-action="webhook" data-app="${name}">${ic("link", 14)}${r.webhook ? "Issue a new secret" : "Set up a webhook"}</button></div><div id="webhook-out"></div></div>`}
          ${app.preview_of ? "" : `<div class="dt">Branch previews</div><div class="dd" style="display:flex;align-items:center;gap:12px"><button class="switch" role="switch" aria-checked="${!!r.previews}" data-action="previews" data-on="${r.previews ? "0" : "1"}" data-app="${name}" aria-label="Branch previews"></button><span>${r.previews ? "On — every branch gets its own app (up to 5)" : "Off"}</span></div>`}
        </div>`;
      },
      danger() {
        const app = a(); if (!app) return "";
        return `<div class="card-head"><h2>Process</h2></div>
          <div class="dl">
            ${app.adopted ? "" : `<div class="dt">${app.status === "stopped" ? "Start" : "Stop"}</div><div class="dd" style="display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap"><span class="muted">${app.status === "stopped" ? "Start the process again on the same port and URL." : "Stop the process but keep its URL, config and history. The URL answers 502 until you start it."}</span>${app.status === "stopped" ? `<button class="btn" data-action="start" data-app="${name}">${ic("play")}Start</button>` : `<button class="btn" data-action="stop" data-app="${name}">${ic("stop")}Stop</button>`}</div>`}
            <div class="dt">Take down</div><div class="dd" style="display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap"><span class="muted">${app.kind === "db" ? `Stops ${esc(name)} and removes its container. You choose whether its data goes too.` : `Stops ${esc(name)}, removes its tunnel route${app.repo ? ", its watcher and its checkout" : ""}. The DNS record stays.`}</span><button class="btn danger" data-action="delete" data-app="${name}">${ic("trash")}Take down</button></div>
          </div>`;
      },
    },
  };
}

function limitsCard(name, app) {
  const l = app?.limits || {};
  const used = app?.memory != null ? `Using ${fmt.bytesText(app.memory)} right now.` : "";
  return `<form class="card" id="limits-form"><div class="card-head"><h2>Resource limits</h2><div class="right">${used}</div></div>
    <div class="card-body" style="display:flex;flex-direction:column;gap:14px">
      <p class="muted" style="margin:0">Cap a runaway app before it takes the whole laptop with it. Leave a field empty for no limit.</p>
      <div class="row-2">
        <div class="field"><label for="lim-mem">Memory</label><input class="input mono" id="lim-mem" placeholder="no limit — e.g. 512M or 2G" value="${esc(l.memory || "")}" spellcheck="false"><span class="hint">Slowed down at 90%, restarted if it passes the limit.</span></div>
        <div class="field"><label for="lim-cpu">CPU</label><input class="input mono" id="lim-cpu" placeholder="no limit — e.g. 50% or 200%" value="${esc(l.cpu || "")}" spellcheck="false"><span class="hint">100% is one full core.</span></div>
      </div>
      <div class="actions"><button class="btn primary" type="submit">Save and restart</button></div>
    </div></form>`;
}

// ── deployments, routes, machine ────────────────────────────────────────────

VIEWS.deployments = () => {
  let which = "all";
  return {
    title: "Deployments",
    crumbs: [["Deployments", "/deployments"]],
    layout: () => `<div class="title-row"><div class="titles"><h1>Deployments</h1><div class="subline">Every build hoist ran from git, newest first.</div></div></div>${part("tabs")}${part("list")}`,
    mount() { $("#page").addEventListener("click", (e) => { const t = e.target.closest("[data-tab]"); if (!t) return; e.preventDefault(); which = t.dataset.tab; renderPart("tabs", true); renderPart("list", true); }); },
    parts: {
      tabs() {
        const all = store.deployments || [];
        const n = { all: all.length, failed: all.filter((x) => x.status === "failed").length, rollback: all.filter((x) => x.trigger === "rollback").length };
        const tab = (k, l) => `<a href="#" data-tab="${k}" aria-selected="${which === k}">${l} <span class="count">${n[k]}</span></a>`;
        return `<div class="tabs">${tab("all", "All")}${tab("failed", "Failed")}${tab("rollback", "Rollbacks")}</div>`;
      },
      list() {
        if (!store.deployments) return `<div class="skeleton" style="height:160px"></div>`;
        const rows = store.deployments.filter((x) => which === "all" || (which === "failed" ? x.status === "failed" : x.trigger === "rollback"));
        if (!rows.length) return `<div class="card"><div class="empty"><h2>${which === "all" ? "No deploys yet" : "Nothing here"}</h2>${which === "all" ? `<p style="margin:0">Deploy an app from a git URL and each push lands here.</p><button class="btn primary" data-action="new-app">${ic("plus")}New app</button>` : ""}</div></div>`;
        const TRIG = { push: "Push", manual: "Redeploy", rollback: "Rollback", initial: "First deploy" };
        return `<div class="card"><div class="table-wrap"><table class="t stack"><thead><tr><th>App</th><th>Status</th><th>Commit</th><th class="hide-sm">Trigger</th><th class="r hide-sm">Build</th><th class="r">When</th></tr></thead><tbody>
          ${rows.map((x) => `<tr class="click" data-href="/apps/${x.app}/deployments"><td class="name">${esc(x.app)}</td>
            <td class="nowrap"><span class="dep-status"><span class="ring ${x.status === "failed" ? "bad" : ""} ${x.live ? "live" : ""}"></span>${x.status === "failed" ? '<span style="color:var(--negative)">Failed</span>' : x.live ? "<strong>Live</strong>" : '<span class="muted">Ready</span>'}</span></td>
            <td><div class="nowrap"><span class="sha">${fmt.sha(x.sha)}</span> ${esc(fmt.message(x.commit))}</div><div class="sub">${esc(fmt.author(x.commit))}${x.error ? ` · <span style="color:var(--negative)">${esc(x.error)}</span>` : ""}</div></td>
            <td class="hide-sm">${esc(TRIG[x.trigger] || x.trigger)}${x.by ? `<div class="sub">by ${esc(x.by)}</div>` : ""}</td><td class="r hide-sm">${x.seconds != null ? fmt.dur(x.seconds) : "—"}</td><td class="r"><span title="${esc(fmt.when(x.at))}">${fmt.ago(x.at)}</span></td></tr>`).join("")}
          </tbody></table></div></div>`;
      },
    },
  };
};

VIEWS.routes = () => ({
  title: "Routes",
  crumbs: [["Routes", "/routes"]],
  layout: () => `${part("head")}${part("list")}`,
  parts: {
    head() {
      const r = store.state?.routes || {};
      return `<div class="title-row"><div class="titles"><h1>Routes</h1><div class="subline">
        <span>Every hostname this machine's tunnel answers, in the order cloudflared matches them.</span></div>
        <div class="subline">${r.tunnel ? `<span>Tunnel <span class="mono" style="font-size:12px">${esc(r.tunnel)}</span></span>` : ""}${r.config ? `<span>Config <span class="mono" style="font-size:12px">${esc(r.config)}</span></span>` : ""}<span>${store.state?.system.cloudflared ? "cloudflared running" : '<span style="color:var(--negative)">cloudflared is not running</span>'}</span></div></div></div>
        ${r.error ? `<div class="notice bad"><span>${ic("alert")}</span><div class="grow">${esc(r.error)}</div></div>` : ""}`;
    },
    list() {
      const rules = store.routes || store.state?.routes?.rules;
      if (!rules) return `<div class="skeleton" style="height:160px"></div>`;
      return `<div class="card"><div class="table-wrap"><table class="t stack"><thead><tr><th>Hostname</th><th class="hide-sm">Goes to</th><th>Owner</th><th class="r">Last check</th><th style="width:28%" class="hide-sm">Last hour</th></tr></thead><tbody>
        ${rules.map((r) => {
          const owner = r.catchall ? '<span class="muted">Everything else</span>' : r.managed === "_dashboard" ? "This dashboard" : r.managed ? `<a href="/apps/${esc(r.managed)}" data-link>${esc(r.managed)}</a>` : '<span class="muted">Hand-written</span>';
          const probes = r.probes || [];
          const last = r.managed && r.managed !== "_dashboard" ? appByName(r.managed)?.probe : probes[probes.length - 1] || r.probe;
          const strip = r.managed && r.managed !== "_dashboard" ? probeStrip(store.detail[r.managed]?.probes || probes) : probeStrip(probes);
          return `<tr><td>${r.hostname ? `<a class="name" href="https://${esc(r.hostname)}" target="_blank" rel="noopener">${esc(r.hostname)}</a>` : '<span class="muted">* (catch-all)</span>'}</td>
            <td class="sha hide-sm">${esc(r.service)}</td><td>${owner}</td>
            <td class="r">${r.hostname ? probeText(last) : '<span class="dash">—</span>'}</td>
            <td class="hide-sm">${r.hostname ? strip : ""}</td></tr>`;
        }).join("")}</tbody></table></div></div>
        <p class="muted" style="margin:0;font-size:13px">hoist only edits rules marked <span class="mono">hoist:</span>. Hand-written rules are left exactly as they are.</p>`;
    },
  },
});

VIEWS.machine = () => ({
  title: "Machine",
  crumbs: [["Machine", "/machine"]],
  layout: () => `${part("head")}${part("kpis")}<div class="grid-1-1">${part("cpu", "card")}${part("mem", "card")}</div>${part("net", "card")}<div class="grid-3-1" style="align-items:start"><div style="display:flex;flex-direction:column;gap:var(--gap)">${part("checks", "card")}${part("hogs")}</div><div style="display:flex;flex-direction:column;gap:var(--gap)">${notifyCard()}${part("access", "card")}</div></div>`,
  mount() {
    api("/api/doctor").then((r) => { store.doctor = r.checks; renderPart("checks", true); }).catch(() => {});
    mountNotify();
  },
  parts: {
    head() {
      const s = store.state;
      return `<div class="title-row"><div class="titles"><h1>${esc(s?.host || "Machine")}</h1><div class="subline">${s ? `<span>Up ${fmt.dur(s.system.uptime)}</span><span>${s.system.cores} CPU cores</span><span>Load ${s.system.load.join(" · ")}</span>` : ""}</div></div></div>`;
    },
    kpis() {
      const s = store.state?.system; if (!s) return "";
      const memPct = 100 * s.mem_used / s.mem_total, diskPct = 100 * s.disk_used / s.disk_total;
      return `<div class="kpis">
        ${kpi("CPU", fmt.pct(s.cpu, 0), `load ${s.load[0]} on ${s.cores} cores`)}
        ${kpi("Memory", fmt.bytes(s.mem_used), `${memPct.toFixed(0)}% of ${fmt.bytesText(s.mem_total)}`, memPct > 90)}
        ${kpi("Swap", fmt.bytes(s.swap_used), s.swap_total ? `of ${fmt.bytesText(s.swap_total)}` : "no swap")}
        ${kpi("Disk", fmt.bytes(s.disk_used), `${diskPct.toFixed(0)}% of ${fmt.bytesText(s.disk_total)}`, diskPct > 92)}
        ${kpi("Tunnel", s.cloudflared ? "Up" : "Down", s.cloudflared ? "cloudflared is running" : "public URLs will not answer", !s.cloudflared)}
      </div>`;
    },
    cpu() {
      const pts = store.metrics?.system || [];
      return `<div class="card-head"><h2>CPU</h2><div class="right">last hour</div></div><div class="card-body">${chart({ height: 160, yMax: 100, yFmt: (v) => `${v}%`, label: "CPU", markers: deployMarkers(), series: [{ label: "CPU", color: css("--chart-1"), area: true, fmt: (v) => `${v.toFixed(1)}%`, points: pts.map((p) => [p[0], p[1]]) }] })}</div>`;
    },
    mem() {
      const pts = store.metrics?.system || []; const total = store.state?.system.mem_total || 1;
      return `<div class="card-head"><h2>Memory</h2><div class="right">of ${fmt.bytesText(total)}</div></div><div class="card-body">${chart({ height: 160, yMax: total, yFmt: (v) => fmt.bytesText(v), label: "Memory", series: [{ label: "Used", color: css("--chart-2"), area: true, fmt: (v) => fmt.bytesText(v), points: pts.map((p) => [p[0], p[2]]) }] })}</div>`;
    },
    net() {
      const pts = store.metrics?.system || []; const s = store.state?.system;
      const rate = (v) => v == null ? "—" : `${fmt.bytesText(v)}/s`;
      return `<div class="card-head"><h2>Network</h2><div class="right legend"><span><i style="background:var(--chart-1)"></i>In ${rate(s?.net_rx)}</span><span><i style="background:var(--chart-2)"></i>Out ${rate(s?.net_tx)}</span></div></div>
        <div class="card-body">${chart({ height: 130, label: "Network", yFmt: (v) => `${fmt.bytesText(v)}/s`, empty: "Collecting — rates appear after the first few seconds.",
          series: [
            { label: "In", color: css("--chart-1"), area: true, fmt: rate, points: pts.filter((p) => p.length > 3).map((p) => [p[0], p[3]]) },
            { label: "Out", color: css("--chart-2"), fmt: rate, points: pts.filter((p) => p.length > 3).map((p) => [p[0], p[4]]) },
          ] })}</div>`;
    },
    checks() {
      const c = store.doctor;
      return `<div class="card-head"><h2>Setup checks</h2><div class="right">what <span class="mono">hoist doctor</span> reports</div></div>
        <div class="checks">${!c ? '<div class="card-body"><div class="skeleton" style="height:90px"></div></div>' : c.map((x) => `<div class="check"><span class="ic ${x.level}">${ic(x.level === "ok" ? "check" : x.level === "warn" ? "alert" : "x")}</span><div><div>${esc(x.text)}</div>${x.fix.length ? `<div class="fixes">${x.fix.map((f) => `<div class="fix"><code>${esc(f)}</code><button class="btn ghost icon sm" data-action="copy" data-text="${esc(f)}" aria-label="Copy command">${ic("copy", 14)}</button></div>`).join("")}</div>` : ""}</div></div>`).join("")}</div>`;
    },
    access() {
      const cmds = [["Keep it running after you close the terminal", "hoist dash --service"], ["Publish it, so you can check on demos from your phone", "hoist dash --public dash"], ["Sign every browser out", "hoist dash --reset-token"]];
      return `<div class="card-head"><h2>This dashboard</h2></div><div class="card-body" style="display:flex;flex-direction:column;gap:14px">
        <p class="muted" style="margin:0">Signed in with the token from <span class="mono">hoist dash</span>. Anyone with that link can deploy to this machine, so share it only with your team.</p>
        ${cmds.map(([t, c]) => `<div><div style="margin-bottom:6px">${t}</div><div class="fix"><code>${c}</code><button class="btn ghost icon sm" data-action="copy" data-text="${c}" aria-label="Copy command">${ic("copy", 14)}</button></div></div>`).join("")}
      </div>`;
    },
    hogs() {
      const apps = (store.state?.apps || []).filter((a) => a.memory != null).sort((x, y) => y.memory - x.memory);
      if (!apps.length) return "";
      const max = apps[0].memory;
      return `<div class="card"><div class="card-head"><h2>Memory by app</h2><div class="right">hoist-managed processes only</div></div>
        <div class="card-body" style="display:flex;flex-direction:column;gap:12px">${apps.map((a) => `<div style="display:grid;grid-template-columns:160px minmax(0,1fr) 90px;gap:12px;align-items:center"><a href="/apps/${a.name}" data-link class="name">${esc(a.name)}</a><div class="bar"><i style="width:${(100 * a.memory / max).toFixed(1)}%"></i></div><div class="num" style="text-align:right">${fmt.bytes(a.memory)}</div></div>`).join("")}</div></div>`;
    },
  },
});

function notifyCard() {
  return `<div class="card" id="notify-card"><div class="card-head"><h2>Notifications</h2><div class="right" id="notify-kind"></div></div>
    <form class="card-body" id="notify-form" style="display:flex;flex-direction:column;gap:10px">
      <p class="muted" style="margin:0">A message when a deploy lands, a push fails to build, or a public URL stops answering for a minute.</p>
      <div class="field"><label for="notify-url">Webhook URL</label><input class="input mono" id="notify-url" placeholder="https://discord.com/api/webhooks/…" spellcheck="false" autocomplete="off">
        <span class="hint">Discord, Slack, ntfy and Telegram links work as pasted; email is <span class="mono">smtps://user:app-password@smtp.gmail.com:465/?to=you@x.com</span>; anything else gets JSON.</span></div>
      <div class="actions"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" id="notify-test">Send a test</button></div>
    </form></div>`;
}
function mountNotify() {
  const kindNames = { discord: "Discord", slack: "Slack", ntfy: "ntfy", telegram: "Telegram", email: "email", json: "JSON webhook" };
  const show = (r) => { $("#notify-url").value = r.url || ""; $("#notify-kind").textContent = r.url ? `Sending to ${kindNames[r.kind] || r.kind}` : "Off"; };
  api("/api/notify").then(show).catch(() => {});
  $("#notify-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const r = await post("/api/notify", { url: $("#notify-url").value.trim() }); show(r); toast(r.url ? `Notifications go to ${kindNames[r.kind]}` : "Notifications off"); }
    catch (err) { toast(err.message, true); }
  });
  $("#notify-test").addEventListener("click", async (e) => {
    e.currentTarget.disabled = true;
    try { await post("/api/notify/test"); toast("Test message sent"); } catch (err) { toast(`Not delivered: ${err.data?.detail || err.message}`, true); }
    finally { e.currentTarget.disabled = false; }
  });
}

function dbTable(dbs) {
  return `<div class="table-wrap"><table class="t stack apps-t"><thead><tr><th>Database</th><th>Status</th><th class="hide-sm">Engine</th><th class="r">Address</th><th class="r hide-sm">Memory</th><th></th></tr></thead><tbody>
    ${dbs.map((a) => `<tr class="click" data-href="/apps/${a.name}"><td><div class="name">${esc(a.name)}</div><div class="sub">${esc(a.db?.volume || "")}</div></td>
      <td>${statusPill(a.status)}</td><td class="hide-sm">${esc(ENGINE_LABEL[a.db?.engine] || "")}<div class="sub">${esc(a.db?.image || "")}</div></td>
      <td class="r mono" style="font-size:13px">127.0.0.1:${a.port}</td><td class="r hide-sm">${a.memory == null ? '<span class="dash">—</span>' : fmt.bytes(a.memory)}</td>
      <td class="r"><span class="row-actions"><button class="btn ghost sm" data-action="copy" data-text="${esc(a.db?.url || "")}">${ic("copy", 14)}URL</button><button class="btn ghost icon sm" data-action="app-menu" data-app="${a.name}" aria-label="More actions for ${esc(a.name)}">${ic("more")}</button></span></td></tr>`).join("")}
  </tbody></table></div>`;
}

function dbConnectCard(app) {
  const masked = (app.db?.url || "").replace(/(:\/\/[^:]*:)([^@]+)(@)/, (m, a, p, b) => a + "•".repeat(10) + b);
  const others = (store.state?.apps || []).filter((x) => x.kind !== "db" && !x.adopted);
  const linked = others.filter((x) => (x.env_keys || []).includes(app.db?.env_var));
  return `<div class="card"><div class="card-head"><h2>Connect</h2><div class="right">Reachable from this machine only</div></div>
    <div class="card-body" style="display:flex;flex-direction:column;gap:14px">
      <div class="fix"><code id="db-url" data-masked="1">${esc(masked)}</code>
        <button class="btn ghost sm" data-action="reveal-db" data-url="${esc(app.db?.url || "")}" data-masked="${esc(masked)}">${ic("eye", 14)}Show</button>
        <button class="btn ghost sm" data-action="copy" data-text="${esc(app.db?.url || "")}">${ic("copy", 14)}Copy</button></div>
      <form class="actions" id="link-form" style="align-items:flex-end">
        <div class="field" style="min-width:220px;flex:1"><label for="link-app">Give an app this database</label>
          <select class="select" id="link-app">${others.length ? others.map((x) => `<option value="${esc(x.name)}">${esc(x.name)}</option>`).join("") : "<option value=''>No apps yet</option>"}</select></div>
        <div class="field" style="width:200px"><label for="link-var">As</label><input class="input mono" id="link-var" value="${esc(app.db?.env_var || "DATABASE_URL")}" spellcheck="false"></div>
        <button class="btn primary" type="submit" ${others.length ? "" : "disabled"}>${ic("link")}Link and restart</button>
      </form>
      <div class="muted" style="font-size:13px">${linked.length ? `Linked to ${linked.map((x) => `<a href="/apps/${esc(x.name)}/environment" data-link>${esc(x.name)}</a>`).join(", ")}.` : "Not linked to any app yet."} Same as <span class="mono">hoist link ${esc(app.name)} &lt;app&gt;</span>.</div>
    </div></div>`;
}

VIEWS.databases = () => ({
  title: "Databases",
  crumbs: [["Databases", "/databases"]],
  layout: () => `<div class="title-row"><div class="titles"><h1>Databases</h1><div class="subline">PostgreSQL, MySQL, Redis and MongoDB in Docker, with their own volume and password, reachable from this machine only.</div></div>
    <div class="actions"><button class="btn primary" data-action="new-db">${ic("plus")}New database</button></div></div>${part("list")}`,
  parts: {
    list() {
      if (!store.state) return `<div class="skeleton" style="height:140px"></div>`;
      const dbs = store.state.apps.filter((a) => a.kind === "db");
      if (!dbs.length) return `<div class="card"><div class="empty"><h2>No databases yet</h2><p style="margin:0;max-width:480px">Start one in a few seconds, then link it to an app: hoist puts the connection URL in the app's environment and restarts it.</p>
        <div class="actions" style="justify-content:center">${Object.entries(ENGINE_LABEL).map(([k, l]) => `<button class="btn" data-action="new-db" data-engine="${k}">${l}</button>`).join("")}</div>
        <span class="cmd">hoist db postgres --name votes</span></div></div>`;
      return `<div class="card">${dbTable(dbs)}</div>`;
    },
  },
});

VIEWS.team = () => ({
  title: "Team",
  crumbs: [["Team", "/team"]],
  layout: () => `<div class="title-row"><div class="titles"><h1>Team</h1><div class="subline">Everyone gets their own sign-in link. Every change in the dashboard is logged with who made it.</div></div></div>
    <div class="grid-3-1" style="align-items:start">${part("people", "card")}${part("add", "card")}</div>${part("audit", "card")}`,
  parts: {
    people() {
      const t = store.team; if (!t) return `<div class="card-body"><div class="skeleton" style="height:100px"></div></div>`;
      return `<div class="card-head"><h2>People</h2><div class="right">${fmt.plural(t.people.length, "person", "people")}</div></div>
        <div class="table-wrap"><table class="t stack"><thead><tr><th>Name</th><th>Role</th><th class="hide-sm">Added</th><th class="r">Last active</th><th></th></tr></thead><tbody>
        ${t.people.map((p) => `<tr><td class="name">${esc(p.name)}${p.name === t.me ? ' <span class="pill">You</span>' : ""}</td><td>${p.role === "owner" ? "Owner" : t.me === "owner" ? `<select class="select" style="width:auto;height:28px" data-role-of="${esc(p.name)}" aria-label="Role of ${esc(p.name)}"><option value="member" ${p.role === "member" ? "selected" : ""}>Member</option><option value="viewer" ${p.role === "viewer" ? "selected" : ""}>Viewer</option></select>` : p.role === "viewer" ? "Viewer" : "Member"}</td>
          <td class="hide-sm">${p.added ? esc(fmt.when(p.added)) : '<span class="dash">—</span>'}</td><td class="r">${p.seen ? fmt.ago(p.seen) : '<span class="dash">not since this dashboard started</span>'}</td>
          <td class="r">${t.me === "owner" && p.role !== "owner" ? `<button class="btn ghost sm danger" data-action="remove-user" data-user="${esc(p.name)}">Remove</button>` : ""}</td></tr>`).join("")}
        </tbody></table></div>`;
    },
    add() {
      const t = store.team; if (!t) return "";
      if (t.me !== "owner") return `<div class="card-head"><h2>Add a teammate</h2></div><div class="card-body muted">Only the owner can add or remove people.</div>`;
      return `<div class="card-head"><h2>Add a teammate</h2></div>
        <form class="card-body" id="add-user" style="display:flex;flex-direction:column;gap:12px">
          <div class="field"><label for="new-user">Name</label><input class="input" id="new-user" placeholder="shreyas" pattern="[a-z][a-z0-9_\\-]{0,23}" required autocomplete="off" spellcheck="false"><span class="hint">Lowercase, used in the audit log.</span></div>
          <div class="field"><label for="new-role">Role</label><select class="select" id="new-role"><option value="member">Member — deploy, restart, edit env and roll back</option><option value="viewer">Viewer — see everything, change nothing</option></select></div>
          <button class="btn primary" type="submit">${ic("plus")}Create sign-in link</button>
          <div id="new-link" class="hidden"></div>
        </form>`;
    },
    audit() {
      const rows = store.team?.audit || [];
      return `<div class="card-head"><h2>Audit log</h2><div class="right">newest first · env values are never logged</div></div>
        ${rows.length ? `<div class="table-wrap"><table class="t stack"><thead><tr><th>When</th><th>Who</th><th>What</th><th>App</th><th class="hide-sm">Detail</th></tr></thead><tbody>
          ${rows.slice(0, 100).map((r) => `<tr><td class="nowrap" title="${esc(fmt.when(r.t))}">${fmt.ago(r.t)}</td><td class="name">${esc(r.user)}</td><td>${esc(r.action)}</td>
            <td>${!r.target ? '<span class="dash">—</span>' : appByName(r.target) ? `<a href="/apps/${esc(r.target)}" data-link>${esc(r.target)}</a>` : esc(r.target)}</td><td class="hide-sm muted">${esc(r.detail || "")}</td></tr>`).join("")}
          </tbody></table></div>` : `<div class="empty">Nothing yet. Deploys, restarts, env changes and rollbacks from the dashboard show up here.</div>`}`;
    },
  },
  mount() {
    $("#page").addEventListener("change", async (e) => {
      const who = e.target.dataset?.roleOf; if (!who) return;
      try { await post(`/api/team/${who}/role`, { role: e.target.value }); toast(`${who} is now a ${e.target.value}`); poll(); }
      catch (err) { toast(err.message, true); }
    });
    $("#page").addEventListener("submit", async (e) => {
      if (e.target.id !== "add-user") return;
      e.preventDefault();
      const name = $("#new-user").value.trim();
      try {
        const r = await post("/api/team", { name, role: $("#new-role").value, origin: location.origin });
        const box = $("#new-link"); box.classList.remove("hidden");
        box.innerHTML = `<div class="notice info" style="flex-direction:column;gap:8px"><strong>Send this to ${esc(r.name)}. It is shown once.</strong><div class="fix" style="width:100%"><code>${esc(r.link)}</code><button class="btn ghost icon sm" type="button" data-action="copy" data-text="${esc(r.link)}" aria-label="Copy link">${ic("copy", 14)}</button></div></div>`;
        $("#new-user").value = "";
        $('[data-part="add"]').dataset.hold = "1";
        poll();
      } catch (err) { toast(err.message, true); }
    });
  },
});

VIEWS.servers = () => ({
  title: "Machines",
  crumbs: [["Machines", "/servers"]],
  layout: () => `<div class="title-row"><div class="titles"><h1>Machines</h1><div class="subline">Each machine runs its own hoist. Link them here to manage all of them from this dashboard; tokens stay on this machine.</div></div></div>
    ${part("list", "card")}<div class="grid-3-1" style="align-items:start">${part("help", "card")}${linkCard()}</div>`,
  parts: {
    list() {
      const f = store.fleet; if (!f) return `<div class="card-body"><div class="skeleton" style="height:100px"></div></div>`;
      const rows = [{ id: "local", name: f.local.host, ok: true, host: f.local.host, apps: f.local.apps, broken: f.local.broken, system: f.local.system, local: true }, ...f.servers];
      return `<div class="card-head"><h2>All machines</h2><div class="right">${fmt.plural(rows.length, "machine")}</div></div>
        <div class="table-wrap"><table class="t stack"><thead><tr><th>Machine</th><th>Status</th><th class="r">Apps</th><th class="hide-sm">Not answering</th><th class="r hide-sm">CPU</th><th class="r hide-sm">Memory</th><th class="r hide-sm">Latency</th><th></th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td><div class="name">${esc(r.name)}${r.id === currentServer ? ' <span class="pill">Viewing</span>' : ""}</div><div class="sub">${r.local ? "this machine" : esc(r.url || "")}</div></td>
          <td>${r.ok ? '<span class="pill up"><span class="d"></span>Reachable</span>' : `<span class="pill down"><span class="d"></span>Not answering</span><div class="sub" style="color:var(--negative)">${esc(r.error || "")}</div>`}</td>
          <td class="r">${r.apps ?? "—"}</td><td class="hide-sm">${r.broken?.length ? `<span style="color:var(--negative)">${r.broken.map(esc).join(", ")}</span>` : r.ok ? '<span class="muted">none</span>' : '<span class="dash">—</span>'}</td>
          <td class="r hide-sm">${r.system?.cpu != null ? fmt.pct(r.system.cpu, 0) : '<span class="dash">—</span>'}</td>
          <td class="r hide-sm">${r.system?.mem_total ? fmt.pct(100 * r.system.mem_used / r.system.mem_total, 0) : '<span class="dash">—</span>'}</td>
          <td class="r hide-sm">${r.latency_ms != null ? fmt.ms(r.latency_ms) : '<span class="dash">—</span>'}</td>
          <td class="r nowrap">${r.id === currentServer ? "" : `<button class="btn sm" data-action="switch-server" data-server="${esc(r.id)}">Open</button>`}${r.local ? "" : ` <button class="btn ghost sm danger" data-action="remove-server" data-server="${esc(r.id)}">Unlink</button>`}</td></tr>`).join("")}
        </tbody></table></div>`;
    },
    help() {
      return `<div class="card-head"><h2>How it works</h2></div><div class="card-body" style="display:flex;flex-direction:column;gap:10px">
        <p style="margin:0">On the other machine, install hoist and run <span class="mono">hoist dash --service</span> (and <span class="mono">hoist dash --public dash</span> if it is not on your network). Paste its address and the owner link it prints.</p>
        <p class="muted" style="margin:0">Opening a machine switches this whole dashboard to it: apps, deploys, logs, databases, backups and rollbacks all act there. Only the owner of this dashboard can do this; the other machine's link never reaches the browser.</p></div>`;
    },
  },
  mount() {
    $("#page").addEventListener("submit", async (e) => {
      if (e.target.id !== "link-machine") return;
      e.preventDefault();
      const b = $("button[type=submit]", e.target); b.disabled = true;
      try {
        const r = await post("/api/servers", { name: $("#m-name").value, url: $("#m-url").value, token: $("#m-token").value });
        toast(`${r.name} linked`); e.target.reset(); poll();
      } catch (err) { toast(err.message, true); } finally { b.disabled = false; }
    });
  },
});

function linkCard() {
  return `<form class="card" id="link-machine"><div class="card-head"><h2>Link a machine</h2></div><div class="card-body" style="display:flex;flex-direction:column;gap:12px">
    <div class="field"><label for="m-name">Name</label><input class="input" id="m-name" placeholder="koustav-laptop" required autocomplete="off"></div>
    <div class="field"><label for="m-url">Dashboard address</label><input class="input mono" id="m-url" placeholder="https://dash.example.com" required autocomplete="off" spellcheck="false"></div>
    <div class="field"><label for="m-token">Owner link or token</label><input class="input mono" id="m-token" type="password" placeholder="https://…/?token=…" required autocomplete="off"><span class="hint">Checked before it is saved; stored only on this machine, readable by you alone.</span></div>
    <button class="btn primary" type="submit">${ic("link")}Link</button></div></form>`;
}

VIEWS.notfound = () => ({
  title: "Not found", crumbs: [["Not found", "/"]],
  layout: () => `<div class="empty"><h2>There is no page here</h2><a class="btn" href="/" data-link>Back to the overview</a></div>`,
});
