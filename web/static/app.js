(() => {
  const shortName = (n) => (n || "").replace(/^ActiveSG Gym @\s*/i, "");
  const levelColor = (pct, closed) => {
    if (closed || pct == null) return "var(--closed)";
    if (pct >= 80) return "var(--hot)";
    if (pct >= 55) return "var(--mid)";
    return "var(--cool)";
  };

  // Static Pages (no :8787) use api/*.json; local FastAPI uses /api/* query routes.
  const STATIC = location.port !== "8787";
  const apiUrl = {
    latest: () => (STATIC ? "api/latest.json" : "/api/latest"),
    dates: (gymId) =>
      STATIC
        ? `api/dates/${encodeURIComponent(gymId)}.json`
        : `/api/dates?gym_id=${encodeURIComponent(gymId)}`,
    history: (gymId, date) =>
      STATIC
        ? `api/history/${encodeURIComponent(gymId)}/${encodeURIComponent(date)}.json`
        : `/api/history?gym_id=${encodeURIComponent(gymId)}&date=${encodeURIComponent(date)}`,
    heatmap: (gymId) =>
      STATIC
        ? `api/heatmap/${encodeURIComponent(gymId)}.json`
        : `/api/heatmap?gym_id=${encodeURIComponent(gymId)}`,
    meta: () => (STATIC ? "api/meta.json" : null),
  };

  let latest = null;
  let selectedId = null;
  let view = "day";
  let dates = [];

  const el = {
    grid: document.getElementById("gym-grid"),
    filter: document.getElementById("filter"),
    sort: document.getElementById("sort"),
    bucket: document.getElementById("bucket-pill"),
    count: document.getElementById("count-pill"),
    updated: document.getElementById("updated-pill"),
    title: document.getElementById("detail-title"),
    date: document.getElementById("date-pick"),
    wrap: document.getElementById("chart-wrap"),
    canvas: document.getElementById("line-canvas"),
    heatmap: document.getElementById("heatmap"),
    hint: document.getElementById("hint"),
  };

  document.querySelectorAll(".seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      view = btn.dataset.view;
      refreshDetail();
    });
  });

  el.filter.addEventListener("input", renderList);
  el.sort.addEventListener("change", renderList);
  el.date.addEventListener("change", () => {
    if (view === "day") refreshDetail();
  });

  function formatBucket(iso) {
    if (!iso) return "No data yet";
    try {
      const d = new Date(iso);
      return (
        d.toLocaleString("en-SG", {
          timeZone: "Asia/Singapore",
          weekday: "short",
          day: "numeric",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }) + " SGT"
      );
    } catch {
      return iso;
    }
  }

  async function loadMeta() {
    if (!el.updated) return;
    const url = apiUrl.meta();
    if (!url) {
      el.updated.textContent = "Live API";
      return;
    }
    try {
      const res = await fetch(url);
      if (!res.ok) return;
      const meta = await res.json();
      el.updated.textContent = "Updated " + formatBucket(meta.generated_at || meta.latest_bucket);
    } catch {
      /* ignore */
    }
  }

  async function loadLatest() {
    const res = await fetch(apiUrl.latest());
    latest = await res.json();
    el.bucket.textContent = formatBucket(latest.bucket_ts);
    const open = (latest.gyms || []).filter((g) => !g.is_closed).length;
    el.count.textContent = `${latest.gyms?.length || 0} gyms · ${open} open`;
    if (!selectedId && latest.gyms?.length) selectedId = latest.gyms[0].id;
    renderList();
    if (selectedId) await refreshDetail();
    await loadMeta();
  }

  function sortedGyms() {
    let list = [...(latest?.gyms || [])];
    const q = el.filter.value.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (g) =>
          shortName(g.name).toLowerCase().includes(q) ||
          g.name.toLowerCase().includes(q)
      );
    }
    const mode = el.sort.value;
    list.sort((a, b) => {
      if (mode === "name") return shortName(a.name).localeCompare(shortName(b.name));
      const ac = a.is_closed ? 1 : 0;
      const bc = b.is_closed ? 1 : 0;
      if (ac !== bc) return ac - bc;
      const ap = a.capacity_pct ?? -1;
      const bp = b.capacity_pct ?? -1;
      return mode === "quiet" ? ap - bp : bp - ap;
    });
    return list;
  }

  function renderList() {
    const list = sortedGyms();
    el.grid.innerHTML = "";
    if (!list.length) {
      el.grid.innerHTML = `<p class="empty">No gyms match.</p>`;
      return;
    }
    for (const g of list) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "gym-card" + (g.id === selectedId ? " active" : "");
      const pct = g.is_closed ? "Closed" : `${g.capacity_pct ?? "—"}%`;
      const color = levelColor(g.capacity_pct, g.is_closed);
      const fill = g.is_closed ? 0 : Math.max(0, Math.min(100, g.capacity_pct ?? 0));
      btn.innerHTML = `
        <div>
          <div class="name">${escapeHtml(shortName(g.name))}</div>
          <div class="sub">${g.is_closed ? "Venue closed" : "Capacity in use"}</div>
          <div class="bar"><i style="width:${fill}%;background:${color}"></i></div>
        </div>
        <div class="pct" style="color:${color}">${pct}</div>`;
      btn.addEventListener("click", () => {
        selectedId = g.id;
        renderList();
        refreshDetail();
      });
      el.grid.appendChild(btn);
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  async function loadDates() {
    if (!selectedId) return;
    const res = await fetch(apiUrl.dates(selectedId));
    const payload = await res.json();
    dates = payload.dates || [];
    if (dates.length) {
      el.date.max = dates[0];
      el.date.min = dates[dates.length - 1];
      if (!el.date.value || !dates.includes(el.date.value)) el.date.value = dates[0];
    } else {
      el.date.value = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Singapore" });
    }
  }

  async function refreshDetail() {
    if (!selectedId) return;
    const gym = (latest?.gyms || []).find((g) => g.id === selectedId);
    el.title.textContent = gym ? shortName(gym.name) : "Gym";
    await loadDates();
    if (view === "heatmap") {
      el.canvas.hidden = true;
      el.wrap.hidden = true;
      el.heatmap.hidden = false;
      await drawHeatmap();
    } else {
      el.heatmap.hidden = true;
      el.wrap.hidden = true;
      el.canvas.hidden = false;
      await drawDay();
    }
  }

  async function drawDay() {
    const date = el.date.value;
    const res = await fetch(apiUrl.history(selectedId, date));
    if (!res.ok) {
      el.hint.textContent = `No snapshots for ${date} yet.`;
      const canvas = el.canvas;
      const ctx = canvas.getContext("2d");
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      return;
    }
    const data = await res.json();
    const points = data.points || [];
    el.hint.textContent = points.length
      ? `${points.length} snapshots on ${date} (15-min buckets, 07:00–22:00 SGT)`
      : `No snapshots for ${date} yet. Collector runs every 15 min during open hours.`;

    const canvas = el.canvas;
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 800;
    const cssH = 280;
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const pad = { t: 24, r: 16, b: 36, l: 40 };
    const w = cssW - pad.l - pad.r;
    const h = cssH - pad.t - pad.b;

    ctx.clearRect(0, 0, cssW, cssH);
    ctx.fillStyle = "rgba(22,32,51,0.5)";
    ctx.fillRect(0, 0, cssW, cssH);

    ctx.strokeStyle = "rgba(148,163,184,0.12)";
    ctx.fillStyle = "#8b9bb4";
    ctx.font = "11px JetBrains Mono, monospace";
    ctx.textAlign = "right";
    for (let y = 0; y <= 100; y += 25) {
      const yy = pad.t + h * (1 - y / 100);
      ctx.beginPath();
      ctx.moveTo(pad.l, yy);
      ctx.lineTo(pad.l + w, yy);
      ctx.stroke();
      ctx.fillText(String(y), pad.l - 8, yy + 3);
    }

    let endMin = 22 * 60;
    for (const p of points) {
      const slot = (p.bucket_ts || "").slice(11, 16);
      if (/^\d{2}:\d{2}$/.test(slot)) {
        const [hh, mm] = slot.split(":").map(Number);
        endMin = Math.max(endMin, hh * 60 + mm);
      }
    }
    const slots = [];
    for (let m = 7 * 60; m <= endMin; m += 15) {
      const hh = String(Math.floor(m / 60)).padStart(2, "0");
      const mm = String(m % 60).padStart(2, "0");
      slots.push(`${hh}:${mm}`);
    }

    const bySlot = new Map();
    for (const p of points) bySlot.set((p.bucket_ts || "").slice(11, 16), p);

    const xs = (i) => pad.l + (slots.length <= 1 ? w / 2 : (i / (slots.length - 1)) * w);
    const ys = (pct) => pad.t + h * (1 - pct / 100);

    ctx.textAlign = "center";
    ctx.fillStyle = "#8b9bb4";
    slots.forEach((s, i) => {
      if (s.endsWith(":00") && parseInt(s, 10) % 2 === 1) {
        ctx.fillText(s, xs(i), pad.t + h + 18);
      }
    });

    ctx.beginPath();
    let started = false;
    slots.forEach((s, i) => {
      const p = bySlot.get(s);
      if (!p || p.is_closed || p.capacity_pct == null) {
        started = false;
        return;
      }
      const x = xs(i);
      const y = ys(p.capacity_pct);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "#2dd4bf";
    ctx.lineWidth = 2.2;
    ctx.lineJoin = "round";
    ctx.stroke();

    slots.forEach((s, i) => {
      const p = bySlot.get(s);
      if (!p) return;
      const x = xs(i);
      if (p.capacity_pct == null) {
        if (p.is_closed) {
          ctx.fillStyle = "#64748b";
          ctx.fillRect(x - 2, pad.t + h / 2 - 2, 4, 4);
        }
        return;
      }
      ctx.beginPath();
      ctx.arc(x, ys(p.capacity_pct), 3.2, 0, Math.PI * 2);
      ctx.fillStyle = levelColor(p.capacity_pct, p.is_closed);
      ctx.fill();
    });

    if (!points.length) {
      ctx.fillStyle = "#8b9bb4";
      ctx.font = "14px DM Sans, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No data for this day yet", cssW / 2, cssH / 2);
    }
  }

  async function drawHeatmap() {
    const res = await fetch(apiUrl.heatmap(selectedId));
    const data = await res.json();
    const cells = data.cells || [];
    const weekdays = data.weekdays || ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const map = new Map(cells.map((c) => [`${c.weekday}|${c.slot}`, c]));

    const displaySlots = [];
    for (let m = 7 * 60; m <= 22 * 60; m += 30) {
      const hh = String(Math.floor(m / 60)).padStart(2, "0");
      const mm = String(m % 60).padStart(2, "0");
      displaySlots.push(`${hh}:${mm}`);
    }

    const hm = el.heatmap;
    hm.innerHTML = "";
    hm.style.gridTemplateColumns = "52px repeat(7, 1fr)";

    hm.appendChild(Object.assign(document.createElement("div"), { className: "hm-corner" }));
    weekdays.forEach((d) => {
      const e = document.createElement("div");
      e.className = "hm-day";
      e.textContent = d;
      hm.appendChild(e);
    });

    displaySlots.forEach((slot) => {
      const lab = document.createElement("div");
      lab.className = "hm-slot";
      lab.textContent = slot;
      hm.appendChild(lab);

      const [h, mi] = slot.split(":").map(Number);
      const keys = [`${String(h).padStart(2, "0")}:${String(mi).padStart(2, "0")}`];
      if (mi === 0) keys.push(`${String(h).padStart(2, "0")}:15`);
      else keys.push(`${String(h).padStart(2, "0")}:45`);

      for (let wd = 0; wd < 7; wd++) {
        let sum = 0;
        let n = 0;
        let samples = 0;
        for (const k of keys) {
          const c = map.get(`${wd}|${k}`);
          if (c) {
            sum += c.avg_pct * c.samples;
            n += c.samples;
            samples += c.samples;
          }
        }
        const cell = document.createElement("div");
        cell.className = "hm-cell";
        if (n > 0) {
          const avg = sum / n;
          const alpha = 0.2 + 0.8 * (avg / 100);
          cell.style.background = `rgba(45, 212, 191, ${alpha.toFixed(3)})`;
          cell.dataset.tip = `${weekdays[wd]} ${slot}: ${avg.toFixed(0)}% (${samples} samples)`;
        }
        hm.appendChild(cell);
      }
    });

    const totalSamples = cells.reduce((a, c) => a + c.samples, 0);
    el.hint.textContent = cells.length
      ? `Average occupancy by weekday & half-hour (open venues only). ${totalSamples} total samples.`
      : "Not enough history yet for a weekly average. Keep collecting.";
  }

  window.addEventListener("resize", () => {
    if (view === "day" && selectedId) drawDay();
  });

  loadLatest().catch((e) => {
    el.grid.innerHTML = `<p class="empty">Failed to load: ${escapeHtml(e.message)}</p>`;
  });
  setInterval(() => loadLatest().catch(() => {}), 60_000);
})();
