(function () {
  "use strict";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, 4000);
  }

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  }

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } },
  };

  // ---- scan status / run now (every page) --------------------------------
  const statusEl = $("#scan-status");
  const runBtn = $("#run-now");
  let wasRunning = false;
  let pollTimer = null;

  function fmt(iso) { return iso ? iso.replace("T", " ").slice(0, 16) : ""; }

  async function refreshStatus() {
    let s;
    try { s = await api("GET", "/api/scan/status"); } catch (e) { return; }
    if (s.running) {
      runBtn.disabled = true;
      statusEl.textContent = s.total ? `Scanning ${s.current}/${s.total}: ${s.show}` : "Scanning…";
      wasRunning = true;
      schedulePoll(2000);
      return;
    }
    runBtn.disabled = false;
    const parts = [];
    if (s.last_run) parts.push(`Last scan ${fmt(s.last_run.finished_at || s.last_run.started_at)} (${s.last_run.status})`);
    parts.push(s.next_run ? `next ${fmt(s.next_run)}` : "schedule off");
    statusEl.textContent = parts.join(" · ");
    if (wasRunning) {
      wasRunning = false;
      toast("Scan finished. Refreshing…");
      setTimeout(() => location.reload(), 800);
      return;
    }
    schedulePoll(60000);
  }
  function schedulePoll(ms) { clearTimeout(pollTimer); pollTimer = setTimeout(refreshStatus, ms); }

  runBtn.addEventListener("click", async () => {
    runBtn.disabled = true;
    try {
      await api("POST", "/api/scan/run");
      statusEl.textContent = "Starting scan…";
      wasRunning = true;
      schedulePoll(1000);
    } catch (e) {
      toast(e.message);
      refreshStatus();
    }
  });
  refreshStatus();

  // ---- table filter helper ----------------------------------------------
  function bindFilter(input, rows, extra, emptyEl) {
    const apply = () => {
      const q = input.value.trim().toLowerCase();
      let shown = 0;
      rows().forEach((tr) => {
        const ok = (!q || tr.dataset.search.includes(q)) && (!extra || extra(tr));
        tr.hidden = !ok;
        if (ok) shown++;
      });
      if (emptyEl) emptyEl.hidden = shown > 0;
    };
    input.addEventListener("input", apply);
    return apply;
  }

  // ---- missing episodes page --------------------------------------------
  const missing = $("#missing-table");
  if (missing) {
    const hide = $("#hide-queued");
    hide.checked = store.get("hideQueued") === "1";
    const rows = () => $$("tbody tr", missing);
    const apply = bindFilter($("#filter"), rows, (tr) => !(hide.checked && tr.classList.contains("queued")), $("#no-matches"));
    hide.addEventListener("change", () => { store.set("hideQueued", hide.checked ? "1" : "0"); apply(); });
    apply();

    missing.addEventListener("change", async (ev) => {
      const box = ev.target;
      if (!box.classList.contains("flag")) return;
      const tr = box.closest("tr");
      try {
        await api("POST", `/api/episodes/${tr.dataset.id}/flag`, { flagged: box.checked });
        tr.classList.toggle("queued", box.checked);
        $("#count-queued").textContent = $$("input.flag:checked", missing).length;
        if (hide.checked && box.checked) setTimeout(apply, 400);
      } catch (e) {
        box.checked = !box.checked;
        toast(e.message);
      }
    });
  }

  // ---- shows page ---------------------------------------------------------
  const showsTable = $("#shows-table");
  if (showsTable) {
    bindFilter($("#show-filter"), () => $$("tbody tr", showsTable));
    showsTable.addEventListener("change", async (ev) => {
      const tr = ev.target.closest("tr");
      const body = {};
      if (ev.target.classList.contains("min-season")) body.min_season = ev.target.value;
      else if (ev.target.classList.contains("ignore")) body.ignored = ev.target.checked;
      else return;
      try {
        const r = await api("PATCH", `/api/shows/${tr.dataset.id}`, body);
        if ("ignored" in body) tr.classList.toggle("dim", body.ignored);
        toast(`Saved. ${r.note}`);
      } catch (e) { toast(e.message); }
    });
  }

  // ---- settings page ------------------------------------------------------
  const pathsTable = $("#paths-table");
  if (pathsTable) {
    const newPath = $("#new-path");

    async function addPath() {
      try {
        const r = await api("POST", "/api/paths", { path: newPath.value });
        toast(`Added ${r.path} (${r.shows_found} show folder${r.shows_found === 1 ? "" : "s"} found)`);
        setTimeout(() => location.reload(), 900);
      } catch (e) { toast(e.message); }
    }
    $("#add-path-btn").addEventListener("click", addPath);
    newPath.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); addPath(); } });

    pathsTable.addEventListener("click", async (ev) => {
      if (!ev.target.classList.contains("path-remove")) return;
      const tr = ev.target.closest("tr");
      if (!confirm(`Remove ${tr.cells[0].textContent} from the scan locations?`)) return;
      try { await api("DELETE", `/api/paths/${tr.dataset.id}`); tr.remove(); toast("Removed."); }
      catch (e) { toast(e.message); }
    });
    pathsTable.addEventListener("change", async (ev) => {
      if (!ev.target.classList.contains("path-enabled")) return;
      const tr = ev.target.closest("tr");
      try { await api("PATCH", `/api/paths/${tr.dataset.id}`, { enabled: ev.target.checked }); toast("Saved."); }
      catch (e) { ev.target.checked = !ev.target.checked; toast(e.message); }
    });

    // Folder browser
    const dlg = $("#browse-dialog");
    let current = "";
    async function browse(path) {
      try {
        const r = await api("GET", `/api/browse?path=${encodeURIComponent(path || "/")}`);
        current = r.path;
        $("#browse-path").textContent = r.path || "Drives";
        const list = $("#browse-list");
        list.innerHTML = "";
        if (r.parent !== null && r.parent !== undefined) {
          const up = document.createElement("li");
          up.textContent = "⬑ ..";
          up.addEventListener("click", () => browse(r.parent));
          list.appendChild(up);
        }
        let shows = 0;
        r.dirs.forEach((d) => {
          const li = document.createElement("li");
          const name = document.createElement("span");
          name.textContent = "📁 " + d.name;
          li.appendChild(name);
          if (d.show) {
            shows++;
            const tag = document.createElement("span");
            tag.className = "pill ok";
            tag.textContent = "show";
            li.appendChild(tag);
          }
          li.addEventListener("click", () => browse(d.path));
          list.appendChild(li);
        });
        $("#browse-hint").textContent = shows ? `${shows} show folder${shows === 1 ? "" : "s"} here` : "";
        $("#browse-select").disabled = !current;
      } catch (e) { toast(e.message); }
    }
    $("#browse-btn").addEventListener("click", () => { dlg.showModal(); browse(newPath.value.trim() || "/"); });
    $("#browse-close").addEventListener("click", () => dlg.close());
    $("#browse-select").addEventListener("click", () => { newPath.value = current; dlg.close(); newPath.focus(); });

    // TheTVDB connection test
    $("#tvdb-test").addEventListener("click", async () => {
      const out = $("#tvdb-test-result");
      out.textContent = "Testing…";
      out.className = "muted";
      try {
        await api("POST", "/api/tvdb/test", { api_key: $("#tvdb_api_key").value, pin: $("#tvdb_pin").value });
        out.textContent = "Connected ✓";
        out.className = "pill ok";
      } catch (e) {
        out.textContent = e.message;
        out.className = "error-text";
      }
    });

    // Show only the schedule fields that apply
    const mode = $("#schedule_mode");
    const syncMode = () => $$("[data-mode]").forEach((el) => { el.hidden = el.dataset.mode !== mode.value; });
    mode.addEventListener("change", syncMode);
    syncMode();
  }
})();
