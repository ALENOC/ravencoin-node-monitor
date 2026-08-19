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

      /*
       * Demo-inspired responsive masonry layout.
       * A tiny fixed grid row is used as the masonry unit; JavaScript assigns
       * each card the number of rows required by its actual rendered height.
       * This prevents a tall card from forcing a large grey hole beneath the
       * shorter card beside it.
       */
      .grid.dashboard-demo-layout {
        grid-template-columns:repeat(4,minmax(0,1fr));
        grid-auto-rows:8px;
        grid-auto-flow:dense;
        align-items:start;
      }
      .grid.dashboard-demo-layout > .card {
        min-width:0;
        align-self:start;
        grid-column:span 1;
      }
      .grid.dashboard-demo-layout > .card.layout-half {
        grid-column:span 2;
      }
      .grid.dashboard-demo-layout > .card.layout-full {
        grid-column:1 / -1;
      }
      @media (max-width:900px) {
        .grid.dashboard-demo-layout { grid-template-columns:repeat(2,minmax(0,1fr)); }
        .grid.dashboard-demo-layout > .card.layout-half,
        .grid.dashboard-demo-layout > .card.layout-full { grid-column:1 / -1; }
      }
      @media (max-width:640px) {
        .grid.dashboard-demo-layout { grid-template-columns:1fr; }
        .grid.dashboard-demo-layout > .card { grid-column:1 / -1; }
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
    card.classList.remove("span-2", "span-full", "layout-half", "layout-full");
    if (width === "half") card.classList.add("layout-half");
    if (width === "full") card.classList.add("layout-full");
    return card;
  }

  let masonryRaf = 0;
  let masonryObserver = null;

  function scheduleMasonry() {
    cancelAnimationFrame(masonryRaf);
    masonryRaf = requestAnimationFrame(applyMasonry);
  }

  function applyMasonry() {
    const grid = document.querySelector(".grid.dashboard-demo-layout");
    if (!grid) return;

    const styles = getComputedStyle(grid);
    const rowHeight = parseFloat(styles.gridAutoRows) || 8;
    const rowGap = parseFloat(styles.rowGap) || parseFloat(styles.gap) || 16;
    const unit = rowHeight + rowGap;

    grid.querySelectorAll(":scope > .card").forEach((card) => {
      if (getComputedStyle(card).display === "none") {
        card.style.gridRowEnd = "span 1";
        return;
      }
      // Measure the natural card height; align-self:start prevents the grid
      // area itself from stretching the card and feeding back into this value.
      const height = card.getBoundingClientRect().height;
      const span = Math.max(1, Math.ceil((height + rowGap) / unit));
      card.style.gridRowEnd = `span ${span}`;
    });
  }

  function observeMasonryCards() {
    const grid = document.querySelector(".grid.dashboard-demo-layout");
    if (!grid || typeof ResizeObserver === "undefined") return;
    if (masonryObserver) masonryObserver.disconnect();
    masonryObserver = new ResizeObserver(scheduleMasonry);
    grid.querySelectorAll(":scope > .card").forEach((card) => masonryObserver.observe(card));
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

    for (const card of cards) grid.appendChild(card);

    // The old dashboard grouped the lower cards into nested two-column
    // wrappers. Once every known card has been moved into the flat grid,
    // remove only wrappers that are genuinely empty so future unknown
    // cards are never discarded.
    grid.querySelectorAll(".two-col-section").forEach((section) => {
      if (!section.querySelector(".card")) section.remove();
    });

    grid.classList.add("dashboard-demo-layout");
    scheduleMasonry();
    observeMasonryCards();
    window.addEventListener("resize", scheduleMasonry, { passive: true });
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
    scheduleMasonry();
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
    installDemoLikeLayout();
    refreshTraffic();
    setInterval(refreshTraffic, 8000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
