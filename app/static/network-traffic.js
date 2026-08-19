"use strict";

(function () {
  const UNIT_FACTORS = { "B/s": 1, "KB/s": 1024, "MB/s": 1024 ** 2, "GB/s": 1024 ** 3 };
  let bandwidthMax = 0;
  let bandwidthWriteEnabled = false;

  function fmtBytes(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "-";
    const units = ["B", "KB", "MB", "GB", "TB", "PB"];
    let v = Number(value);
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return `${v.toFixed(v < 10 && i > 0 ? 2 : v < 100 ? 1 : 0)} ${units[i]}`;
  }

  function fmtRate(value) {
    return value === null || value === undefined || !Number.isFinite(Number(value))
      ? "collecting..."
      : `${fmtBytes(Number(value))}/s`;
  }

  function fmtDuration(seconds) {
    if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return "-";
    let s = Math.max(0, Math.floor(Number(seconds)));
    const d = Math.floor(s / 86400); s %= 86400;
    const h = Math.floor(s / 3600); s %= 3600;
    const m = Math.floor(s / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m ${s % 60}s`;
    return `${s}s`;
  }

  function set(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function installCard() {
    if (document.getElementById("card-network-traffic")) return true;
    const compatibility = document.getElementById("card-electrumx-checks");
    if (!compatibility || !compatibility.parentNode) return false;

    const style = document.createElement("style");
    style.textContent = `
      .rvn-traffic-speeds { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:8px; }
      .rvn-traffic-speed { border:1px solid var(--border); border-radius:10px; padding:12px 14px; background:var(--bg); }
      .rvn-traffic-speed .label { color:var(--text-muted); font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
      .rvn-traffic-speed .value { margin-top:3px; font-size:22px; font-weight:750; font-variant-numeric:tabular-nums; }
      .rvn-traffic-note { color:var(--text-muted); font-size:11.5px; line-height:1.4; margin-top:10px; }

      .bandwidth-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
      .bandwidth-service { border:1px solid var(--border); border-radius:10px; padding:14px; background:var(--bg); min-width:0; }
      .bandwidth-service-title { font-size:12px; font-weight:800; letter-spacing:.04em; text-transform:uppercase; margin-bottom:10px; }
      .bandwidth-current { display:flex; justify-content:space-between; gap:12px; align-items:baseline; margin-bottom:12px; }
      .bandwidth-current .value { font-size:22px; font-weight:750; font-variant-numeric:tabular-nums; }
      .bandwidth-control-row { display:grid; grid-template-columns:minmax(0,1fr) 92px auto; gap:8px; align-items:end; }
      .bandwidth-control-row label { display:block; color:var(--text-muted); font-size:11px; }
      .bandwidth-control-row input, .bandwidth-control-row select {
        width:100%; margin-top:4px; border:1px solid var(--border); border-radius:8px; padding:8px 9px;
        background:var(--card); color:var(--text); font:inherit; font-size:13px;
      }
      .bandwidth-control-row button, .bandwidth-unlimited {
        border:1px solid var(--border); border-radius:8px; padding:8px 11px; background:var(--card); color:var(--text);
        font:inherit; font-size:12px; font-weight:700; cursor:pointer;
      }
      .bandwidth-control-row button:disabled, .bandwidth-unlimited:disabled { opacity:.5; cursor:not-allowed; }
      .bandwidth-actions { display:flex; gap:8px; align-items:center; margin-top:9px; }
      .bandwidth-status { color:var(--text-muted); font-size:11.5px; line-height:1.4; flex:1; }
      .bandwidth-status.error { color:var(--bad); }
      .bandwidth-note { color:var(--text-muted); font-size:11.5px; line-height:1.45; margin-top:12px; }

      /* Demo-style equal-height grid: spare room remains inside cards instead of grey holes. */
      .grid.dashboard-demo-layout { grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; align-items:stretch; }
      .grid.dashboard-demo-layout > .card { min-width:0; height:100%; grid-column:span 1; }
      .grid.dashboard-demo-layout > .card.layout-half { grid-column:span 2; }
      .grid.dashboard-demo-layout > .card.layout-full { grid-column:1 / -1; }
      @media (max-width:900px) {
        .grid.dashboard-demo-layout { grid-template-columns:repeat(2,minmax(0,1fr)); }
        .grid.dashboard-demo-layout > .card.layout-half, .grid.dashboard-demo-layout > .card.layout-full { grid-column:1 / -1; }
        .bandwidth-grid { grid-template-columns:1fr; }
      }
      @media (max-width:640px) {
        .grid.dashboard-demo-layout { grid-template-columns:1fr; }
        .grid.dashboard-demo-layout > .card { grid-column:1 / -1; height:auto; }
        .bandwidth-control-row { grid-template-columns:minmax(0,1fr) 88px; }
        .bandwidth-control-row button { grid-column:1 / -1; }
      }
      @media (max-width:480px) { .rvn-traffic-speeds { grid-template-columns:1fr; } }
    `;
    document.head.appendChild(style);

    const card = document.createElement("div");
    card.className = "card";
    card.id = "card-network-traffic";
    card.innerHTML = `
      <h2>Ravencoin network traffic <span class="peer-count-tag">P2P ONLY</span></h2>
      <div class="rvn-traffic-speeds">
        <div class="rvn-traffic-speed"><div class="label">Download</div><div class="value" id="rvn-download-rate">collecting...</div></div>
        <div class="rvn-traffic-speed"><div class="label">Upload</div><div class="value" id="rvn-upload-rate">collecting...</div></div>
      </div>
      <div class="metric-row"><span class="k">Received since Core start</span><span class="v mono" id="rvn-total-received">-</span></div>
      <div class="metric-row"><span class="k">Sent since Core start</span><span class="v mono" id="rvn-total-sent">-</span></div>
      <div class="metric-row"><span class="k">Total exchanged</span><span class="v mono" id="rvn-total-traffic">-</span></div>
      <div class="metric-row"><span class="k">Rate sample window</span><span class="v mono" id="rvn-rate-window">-</span></div>
      <div class="metric-row"><span class="k">Upload target</span><span class="v mono" id="rvn-upload-target">unlimited</span></div>
      <div class="metric-row hidden" id="rvn-upload-cycle-row"><span class="k">Upload cycle remaining</span><span class="v mono" id="rvn-upload-cycle">-</span></div>
      <div class="bar-mini hidden" id="rvn-upload-target-bar"><div id="rvn-upload-target-fill" style="width:0%"></div></div>
      <div class="rvn-traffic-note">Source: Ravencoin Core <code>getnettotals</code>. These counters and rates are for this node's Ravencoin P2P traffic only; host traffic such as SSH, the dashboard, Docker, updates and ElectrumX is not included.</div>
    `;
    compatibility.parentNode.insertBefore(card, compatibility);

    const bandwidthCard = document.createElement("div");
    bandwidthCard.className = "card hidden";
    bandwidthCard.id = "card-bandwidth-control";
    bandwidthCard.innerHTML = `
      <h2>Bandwidth control <span class="peer-count-tag" id="bandwidth-control-tag">HOST TC</span></h2>
      <div class="bandwidth-grid">
        <div class="bandwidth-service" data-bandwidth-service="core">
          <div class="bandwidth-service-title">Ravencoin Core · public upload</div>
          <div class="bandwidth-current"><span class="k">Current</span><span class="value" id="bw-core-current">collecting...</span></div>
          <div class="bandwidth-control-row">
            <label>Limit<input id="bw-core-value" type="number" min="0" step="0.01" inputmode="decimal" value="0"></label>
            <label>Unit<select id="bw-core-unit"><option>B/s</option><option selected>KB/s</option><option>MB/s</option><option>GB/s</option></select></label>
            <button type="button" id="bw-core-apply">Apply</button>
          </div>
          <div class="bandwidth-actions"><button type="button" class="bandwidth-unlimited" id="bw-core-unlimited">Unlimited</button><span class="bandwidth-status" id="bw-core-status">Loading...</span></div>
        </div>
        <div class="bandwidth-service" data-bandwidth-service="electrumx">
          <div class="bandwidth-service-title">ElectrumX · public upload</div>
          <div class="bandwidth-current"><span class="k">Current</span><span class="value" id="bw-electrumx-current">collecting...</span></div>
          <div class="bandwidth-control-row">
            <label>Limit<input id="bw-electrumx-value" type="number" min="0" step="0.01" inputmode="decimal" value="0"></label>
            <label>Unit<select id="bw-electrumx-unit"><option>B/s</option><option selected>KB/s</option><option>MB/s</option><option>GB/s</option></select></label>
            <button type="button" id="bw-electrumx-apply">Apply</button>
          </div>
          <div class="bandwidth-actions"><button type="button" class="bandwidth-unlimited" id="bw-electrumx-unlimited">Unlimited</button><span class="bandwidth-status" id="bw-electrumx-status">Loading...</span></div>
        </div>
      </div>
      <div class="bandwidth-note">Limits are applied live by the optional host-side Linux <code>tc</code> controller. B/s, KB/s, MB/s and GB/s are supported; KB/MB/GB use 1024-based units. A value of 0 means unlimited. Private Docker/LAN destinations are exempt so Core ↔ ElectrumX traffic is not throttled.</div>
    `;
    compatibility.parentNode.insertBefore(bandwidthCard, compatibility);
    return true;
  }

  function cardFor(descendantId, cardId) {
    const descendant = document.getElementById(descendantId);
    const card = descendant && descendant.closest(".card");
    if (card && cardId && !card.id) card.id = cardId;
    return card;
  }

  function configureCard(card, width) {
    if (!card) return null;
    card.style.gridRowEnd = "";
    card.classList.remove("span-2", "span-full", "layout-half", "layout-full");
    if (width === "half") card.classList.add("layout-half");
    if (width === "full") card.classList.add("layout-full");
    return card;
  }

  function installDemoLikeLayout() {
    const grid = document.querySelector(".grid");
    if (!grid || grid.classList.contains("dashboard-demo-layout")) return;

    const cards = [
      configureCard(document.getElementById("card-price"), "single"),
      configureCard(cardFor("sync-blocks", "card-sync"), "single"),
      configureCard(cardFor("p2p-connections", "card-p2p"), "single"),
      configureCard(cardFor("mempool-count", "card-mempool"), "single"),
      configureCard(cardFor("host-load", "card-host-resources"), "half"),
      configureCard(cardFor("host-disk", "card-storage"), "half"),
      configureCard(document.getElementById("card-network-traffic"), "full"),
      configureCard(document.getElementById("card-bandwidth-control"), "full"),
      configureCard(document.getElementById("card-charts"), "full"),
      configureCard(cardFor("block-rows", "card-recent-blocks"), "half"),
      configureCard(cardFor("mempool-tx-table", "card-mempool-transactions"), "half"),
      configureCard(cardFor("peer-table", "card-network-peers"), "half"),
      configureCard(document.getElementById("card-electrumx-clients"), "half"),
      configureCard(cardFor("node-chain", "card-node"), "half"),
      configureCard(document.getElementById("card-electrumx-server"), "half"),
      configureCard(document.getElementById("card-events"), "half"),
      configureCard(cardFor("banned-rows", "card-banned-peers"), "half"),
      configureCard(document.getElementById("card-electrumx-checks"), "full"),
    ].filter(Boolean);

    for (const item of cards) grid.appendChild(item);
    grid.querySelectorAll(".two-col-section").forEach((section) => {
      if (!section.querySelector(".card")) section.remove();
    });
    grid.classList.add("dashboard-demo-layout");
  }

  function render(traffic) {
    if (!traffic || traffic.scope !== "ravencoin_p2p") return;
    set("rvn-download-rate", fmtRate(traffic.download_bytes_per_second));
    set("rvn-upload-rate", fmtRate(traffic.upload_bytes_per_second));
    set("rvn-total-received", fmtBytes(traffic.total_bytes_received));
    set("rvn-total-sent", fmtBytes(traffic.total_bytes_sent));
    set("rvn-total-traffic", fmtBytes(traffic.total_bytes_transferred));
    set("rvn-rate-window", traffic.sample_seconds ? `${Number(traffic.sample_seconds).toFixed(1)} s` : "collecting...");

    const target = traffic.upload_target || {};
    const cycleRow = document.getElementById("rvn-upload-cycle-row");
    const bar = document.getElementById("rvn-upload-target-bar");
    const fill = document.getElementById("rvn-upload-target-fill");
    if (target.enabled) {
      const used = fmtBytes(target.used_bytes);
      const total = fmtBytes(target.target_bytes);
      set("rvn-upload-target", `${used} / ${total}${target.target_reached ? " · reached" : ""}`);
      set("rvn-upload-cycle", fmtDuration(target.time_left_seconds));
      if (cycleRow) cycleRow.classList.remove("hidden");
      if (bar) bar.classList.remove("hidden");
      if (fill) fill.style.width = `${Math.max(0, Math.min(100, Number(target.progress || 0) * 100))}%`;
    } else {
      set("rvn-upload-target", "unlimited / disabled");
      if (cycleRow) cycleRow.classList.add("hidden");
      if (bar) bar.classList.add("hidden");
    }
  }

  function preferredUnit(bytesPerSecond) {
    if (bytesPerSecond >= UNIT_FACTORS["GB/s"]) return "GB/s";
    if (bytesPerSecond >= UNIT_FACTORS["MB/s"]) return "MB/s";
    if (bytesPerSecond >= UNIT_FACTORS["KB/s"]) return "KB/s";
    return "B/s";
  }

  function setControlFromBytes(service, bytesPerSecond) {
    const input = document.getElementById(`bw-${service}-value`);
    const select = document.getElementById(`bw-${service}-unit`);
    if (!input || !select || document.activeElement === input || document.activeElement === select) return;
    if (!Number.isFinite(Number(bytesPerSecond)) || Number(bytesPerSecond) <= 0) {
      input.value = "0";
      select.value = "KB/s";
      return;
    }
    const unit = preferredUnit(Number(bytesPerSecond));
    const value = Number(bytesPerSecond) / UNIT_FACTORS[unit];
    input.value = String(Number(value.toFixed(3)));
    select.value = unit;
  }

  function setControlEnabled(service, enabled) {
    for (const suffix of ["value", "unit", "apply", "unlimited"]) {
      const el = document.getElementById(`bw-${service}-${suffix}`);
      if (el) el.disabled = !enabled;
    }
  }

  function renderBandwidthService(service, data) {
    const status = document.getElementById(`bw-${service}-status`);
    set(`bw-${service}-current`, fmtRate(data && data.upload_bytes_per_second));
    if (!data) {
      if (status) { status.textContent = "Unavailable"; status.classList.add("error"); }
      setControlEnabled(service, false);
      return;
    }
    const limit = Number(data.limit_bytes_per_second || 0);
    setControlFromBytes(service, limit);
    if (status) {
      status.classList.toggle("error", data.status !== "active");
      if (data.status !== "active") status.textContent = data.error || "Container unavailable";
      else if (limit > 0) status.textContent = `Limited to ${fmtRate(limit)}`;
      else status.textContent = "Unlimited";
    }
    setControlEnabled(service, bandwidthWriteEnabled && data.status === "active");
  }

  function renderBandwidth(payload) {
    const card = document.getElementById("card-bandwidth-control");
    if (!card) return;
    if (!payload || payload.enabled === false) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    bandwidthMax = Number(payload.max_bytes_per_second || 0);
    bandwidthWriteEnabled = payload.write_enabled === true;
    const services = payload.services || {};
    renderBandwidthService("core", services.core);
    renderBandwidthService("electrumx", services.electrumx);
    if (!bandwidthWriteEnabled) {
      const reason = payload.write_disabled_reason || "Read-only: configure MONITOR_PASSWORD to change limits.";
      for (const service of ["core", "electrumx"]) {
        const status = document.getElementById(`bw-${service}-status`);
        if (status && services[service] && services[service].status === "active") status.textContent = reason;
        setControlEnabled(service, false);
      }
    }
    if (payload.error) {
      for (const service of ["core", "electrumx"]) {
        const status = document.getElementById(`bw-${service}-status`);
        if (status) { status.textContent = payload.error; status.classList.add("error"); }
      }
    }
  }

  function bytesFromControl(service) {
    const input = document.getElementById(`bw-${service}-value`);
    const select = document.getElementById(`bw-${service}-unit`);
    const value = Number(input && input.value);
    const factor = UNIT_FACTORS[select && select.value];
    if (!Number.isFinite(value) || value < 0 || !factor) throw new Error("Enter a valid non-negative bandwidth value.");
    const bytes = Math.round(value * factor);
    if (!Number.isSafeInteger(bytes) || bytes < 0) throw new Error("Bandwidth value is too large.");
    if (bandwidthMax > 0 && bytes > bandwidthMax) throw new Error(`Limit exceeds controller maximum (${fmtRate(bandwidthMax)}).`);
    return bytes;
  }

  async function applyBandwidth(service, unlimited) {
    const status = document.getElementById(`bw-${service}-status`);
    if (!bandwidthWriteEnabled) return;
    let bytes;
    try {
      bytes = unlimited ? 0 : bytesFromControl(service);
    } catch (error) {
      if (status) { status.textContent = error.message; status.classList.add("error"); }
      return;
    }
    setControlEnabled(service, false);
    if (status) { status.textContent = "Applying..."; status.classList.remove("error"); }
    const key = service === "core" ? "core_bytes_per_second" : "electrumx_bytes_per_second";
    try {
      const response = await fetch("/api/bandwidth", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Content-Type": "application/json", "X-Ravencoin-Monitor-Control": "1" },
        body: JSON.stringify({ [key]: bytes }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      renderBandwidth(payload);
    } catch (error) {
      if (status) { status.textContent = error.message || "Apply failed"; status.classList.add("error"); }
      setControlEnabled(service, true);
    }
  }

  async function refreshTraffic() {
    try {
      const response = await fetch("/api/status", { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      render(data.network_traffic);
    } catch (_) {
      // The main dashboard already surfaces backend reachability errors.
    }
  }

  async function refreshBandwidth() {
    try {
      const response = await fetch("/api/bandwidth", { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) return;
      renderBandwidth(await response.json());
    } catch (_) {
      // Optional controller: leave its last visible status intact on a transient failure.
    }
  }

  function wireBandwidthControls() {
    for (const service of ["core", "electrumx"]) {
      const apply = document.getElementById(`bw-${service}-apply`);
      const unlimited = document.getElementById(`bw-${service}-unlimited`);
      const input = document.getElementById(`bw-${service}-value`);
      if (apply) apply.addEventListener("click", () => applyBandwidth(service, false));
      if (unlimited) unlimited.addEventListener("click", () => applyBandwidth(service, true));
      if (input) input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") applyBandwidth(service, false);
      });
    }
  }

  function start() {
    if (!installCard()) return;
    installDemoLikeLayout();
    wireBandwidthControls();
    refreshTraffic();
    refreshBandwidth();
    setInterval(refreshTraffic, 8000);
    setInterval(refreshBandwidth, 5000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
