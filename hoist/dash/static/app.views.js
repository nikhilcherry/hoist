
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
      const apps = s.apps, broken = apps.filter(isBroken);
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
      if (!s.apps.length) return emptyApps();
      return `<div class="card">
        <div class="card-head"><h2>Apps</h2><div class="right">Public URLs are checked every 30 s</div></div>
        <div class="card-body flush">${appsTable(s.apps, { compact: true })}</div></div>`;
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
        const apps = store.state?.apps || [];
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
    return (store.state?.apps || []).filter((a) =>
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
  const TABS = [["overview", "Overview"], ["deployments", "Deployments"], ["logs", "Logs"], ["environment", "Environment"], ["settings", "Settings"]];
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
              <a href="${esc(app.url)}" target="_blank" rel="noopener">${esc(app.public ? app.hostname : app.url)} ${ic("ext", 13, 'style="vertical-align:-2px"')}</a>
              <button class="btn ghost icon sm" data-action="copy" data-text="${esc(app.url)}" aria-label="Copy URL" title="Copy URL">${ic("copy", 14)}</button>
              ${app.repo ? `<span>${ic("git", 14, 'style="vertical-align:-2px"')} ${esc(fmt.repo(app.repo.url))} · ${esc(app.repo.branch)}</span>` : ""}
              <span class="mono" style="font-size:12px">:${app.port}</span>
            </div>
            ${why ? `<p class="finding bad"><strong>${esc(why.charAt(0).toUpperCase() + why.slice(1))}.</strong></p>` : ""}
          </div>
          <div class="actions">
            <button class="btn" data-action="qr" data-app="${name}">${ic("qr")}QR</button>
            ${app.adopted ? "" : `<a class="btn" href="${base}/logs" data-link>${ic("terminal")}Logs</a>`}
            ${primary}
            <button class="btn icon" data-action="app-menu" data-app="${name}" aria-label="More actions">${ic("more")}</button>
          </div>
        </div>`;
      },
      tabs() {
        const app = a(); const hist = d()?.history?.length;
        return `<div class="tabs" role="tablist">${TABS.filter(([k]) => !(app?.adopted && ["logs", "environment"].includes(k))).map(([k, label]) =>
          `<a href="${k === "overview" ? base : `${base}/${k}`}" data-link role="tab" aria-selected="${tab === k}">${label}${k === "deployments" && hist ? ` <span class="count">${hist}</span>` : ""}${k === "environment" && app?.env_keys?.length ? ` <span class="count">${app.env_keys.length}</span>` : ""}</a>`).join("")}</div>`;
      },
    },
  };
  const tabViews = { overview: appOverview, deployments: appDeployments, logs: appLogs, environment: appEnv, settings: appSettings };
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
    layout: () => `${part("route")}${part("live")}${part("kpis")}<div class="grid-3-1">${part("charts", "card")}${part("qr", "card")}</div>${part("recent")}`,
    parts: {
      route() { const app = a(); return app ? routeStrip(app) : ""; },
      live() {
        const app = a(); if (!app?.repo) return "";
        const last = app.repo.last || {};
        return `<div class="live-card">
          <div><div class="k">Live deployment</div><div class="commit" style="margin-top:4px">${esc(fmt.message(app.repo.commit) || "—")}</div>
            <div class="meta"><span class="mono" style="font-size:12px">${fmt.sha(app.repo.sha)}</span><span>${esc(fmt.author(app.repo.commit) || "")}</span><span>${ic("git", 13, 'style="vertical-align:-2px"')} ${esc(app.repo.branch)}</span></div></div>
          <div><div class="k">Deployed</div><div class="v" title="${esc(fmt.when(app.repo.deployed))}">${fmt.ago(app.repo.deployed)}</div><div class="k" style="margin-top:2px">${esc(last.trigger ? { push: "by a push", manual: "by hand", rollback: "as a rollback", initial: "first deploy" }[last.trigger] || last.trigger : "")}</div></div>
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
        return `<div class="kpis">
          ${app.public ? kpi("Public check", app.probe ? (app.probe.code ? `${app.probe.code}` : "—") : "—", app.probe ? (app.probe.up ? `${app.probe.ms} ms just now` : esc(app.probe.error || `answered ${app.probe.code}`)) : "first check within 30 s", app.probe && !app.probe.up) : kpi("Reachable at", `<span style="font-size:20px">LAN</span>`, esc(fmt.host(app.url)))}
          ${kpi("Uptime, last hour", app.uptime == null ? "—" : fmt.pct(app.uptime, app.uptime === 100 ? 0 : 1), probes.length ? `${fmt.plural(fails, "failed check")} of ${probes.length}` : "no checks yet", app.uptime != null && app.uptime < 99, fails ? "bad" : "")}
          ${kpi("Median response", p50 == null ? "—" : fmt.ms(p50), ms.length ? `slowest ${ms[ms.length - 1]} ms` : "public URLs only")}
          ${kpi("CPU", app.cpu == null ? "—" : fmt.pct(app.cpu), app.pid ? `PID ${app.pid}` : app.adopted ? "not managed by hoist" : "not running")}
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

function routeStrip(app) {
  const hops = [];
  const arrow = `<svg class="arrow" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 2l5 4-5 4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
  const hop = (cls, icon, k, v, n, mono = false) => `<div class="hop ${cls}">${hops.length < 4 ? arrow : ""}<div class="k">${ic(icon, 14)}${k}</div><div class="v ${mono ? "mono" : ""}" title="${esc(String(v).replace(/<[^>]+>/g, ""))}">${v}</div><div class="n" title="${esc(String(n).replace(/<[^>]+>/g, ""))}">${n}</div></div>`;
  const r = app.repo, last = r?.last;
  // 1. source
  hops.push(r
    ? hop(r.paused ? "warn" : r.watching ? "" : "bad", "git", "Source", `${esc(r.branch)} @ ${fmt.sha(r.sha)}`, r.paused ? "auto-deploy paused" : r.watching ? `${esc(fmt.repo(r.url))}` : "watcher is not running", true)
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
        <td class="hide-sm">${esc(TRIG[h.trigger] || h.trigger || "—")}</td>
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
    es = new EventSource(`/api/apps/${name}/logs/stream?kind=${kind}`);
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
    layout: () => `${part("info", "card")}${a()?.adopted ? "" : limitsCard(name, a())}${part("deploy", "card")}${part("danger", "card")}`,
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
        </div>`;
      },
      danger() {
        const app = a(); if (!app) return "";
        return `<div class="card-head"><h2>Process</h2></div>
          <div class="dl">
            ${app.adopted ? "" : `<div class="dt">${app.status === "stopped" ? "Start" : "Stop"}</div><div class="dd" style="display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap"><span class="muted">${app.status === "stopped" ? "Start the process again on the same port and URL." : "Stop the process but keep its URL, config and history. The URL answers 502 until you start it."}</span>${app.status === "stopped" ? `<button class="btn" data-action="start" data-app="${name}">${ic("play")}Start</button>` : `<button class="btn" data-action="stop" data-app="${name}">${ic("stop")}Stop</button>`}</div>`}
            <div class="dt">Take down</div><div class="dd" style="display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap"><span class="muted">Stops ${esc(name)}, removes its tunnel route${app.repo ? ", its watcher and its checkout" : ""}. The DNS record stays.</span><button class="btn danger" data-action="delete" data-app="${name}">${ic("trash")}Take down</button></div>
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
            <td class="hide-sm">${esc(TRIG[x.trigger] || x.trigger)}</td><td class="r hide-sm">${x.seconds != null ? fmt.dur(x.seconds) : "—"}</td><td class="r"><span title="${esc(fmt.when(x.at))}">${fmt.ago(x.at)}</span></td></tr>`).join("")}
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
  layout: () => `${part("head")}${part("kpis")}<div class="grid-1-1">${part("cpu", "card")}${part("mem", "card")}</div><div class="grid-3-1" style="align-items:start"><div style="display:flex;flex-direction:column;gap:var(--gap)">${part("checks", "card")}${part("hogs")}</div><div style="display:flex;flex-direction:column;gap:var(--gap)">${notifyCard()}${part("access", "card")}</div></div>`,
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
        <span class="hint">Discord, Slack, ntfy and Telegram links work as pasted; anything else gets JSON.</span></div>
      <div class="actions"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" id="notify-test">Send a test</button></div>
    </form></div>`;
}
function mountNotify() {
  const kindNames = { discord: "Discord", slack: "Slack", ntfy: "ntfy", telegram: "Telegram", json: "JSON webhook" };
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

VIEWS.notfound = () => ({
  title: "Not found", crumbs: [["Not found", "/"]],
  layout: () => `<div class="empty"><h2>There is no page here</h2><a class="btn" href="/" data-link>Back to the overview</a></div>`,
});
