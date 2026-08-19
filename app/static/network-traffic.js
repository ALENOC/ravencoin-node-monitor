"use strict";

(function () {
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
    return true;
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

  function start() {
    if (!installCard()) return;
    refreshTraffic();
    setInterval(refreshTraffic, 8000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
