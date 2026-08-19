"use strict";

(function () {
  let maxLimit = 10000;
  let writeEnabled = false;
  const NATIVE_DEFAULTS = { core: 125, electrumx: 1000 };

  function set(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function installCard() {
    if (document.getElementById("card-connection-control")) return true;
    const anchor = document.getElementById("card-bandwidth-control") ||
      document.getElementById("card-network-traffic") ||
      document.getElementById("card-charts");
    if (!anchor || !anchor.parentNode) return false;

    const style = document.createElement("style");
    style.textContent = `
      .connection-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
      .connection-service { border:1px solid var(--border); border-radius:10px; padding:14px; background:var(--bg); min-width:0; }
      .connection-service-title { font-size:12px; font-weight:800; letter-spacing:.04em; text-transform:uppercase; margin-bottom:6px; }
      .connection-help { color:var(--text-muted); font-size:11.5px; line-height:1.45; min-height:34px; margin-bottom:12px; }
      .connection-stats { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:12px; }
      .connection-stat { border:1px solid var(--border); border-radius:8px; padding:9px 10px; background:var(--bg-elevated); min-width:0; }
      .connection-stat .k { display:block; color:var(--text-muted); font-size:11px; margin-bottom:3px; }
      .connection-stat .value { display:block; font-size:20px; font-weight:750; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
      .connection-stat .value.limit { font-size:16px; line-height:1.25; }
      .connection-control-row { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; align-items:end; }
      .connection-control-row label { display:block; color:var(--text-muted); font-size:11px; }
      .connection-control-row input {
        width:100%; margin-top:4px; border:1px solid var(--border); border-radius:8px; padding:8px 9px;
        background:var(--bg-elevated); color:var(--text); font:inherit; font-size:13px;
      }
      .connection-control-row button {
        border:1px solid var(--border); border-radius:8px; padding:8px 11px; background:var(--bg-elevated); color:var(--text);
        font:inherit; font-size:12px; font-weight:700; cursor:pointer; white-space:nowrap;
      }
      .connection-control-row button:disabled { opacity:.5; cursor:not-allowed; }
      .connection-status { color:var(--text-muted); font-size:11.5px; line-height:1.45; margin-top:9px; }
      .connection-status.error { color:var(--bad); }
      .connection-note { color:var(--text-muted); font-size:11.5px; line-height:1.5; margin-top:12px; }
      @media (max-width:900px) { .connection-grid { grid-template-columns:1fr; } }
      @media (max-width:520px) {
        .connection-stats { grid-template-columns:1fr; }
        .connection-control-row { grid-template-columns:1fr; }
        .connection-control-row button { width:100%; }
      }
    `;
    document.head.appendChild(style);

    const card = document.createElement("div");
    card.className = "card layout-full hidden";
    card.id = "card-connection-control";
    card.innerHTML = `
      <h2>Connection limits <span class="peer-count-tag">RESTART REQUIRED</span></h2>
      <div class="connection-grid">
        <div class="connection-service">
          <div class="connection-service-title">Ravencoin Core · P2P peers</div>
          <div class="connection-help">Other Ravencoin nodes connected directly to this Core instance. This controls simultaneous node-to-node P2P connections; it does not limit Electrum wallet clients.</div>
          <div class="connection-stats">
            <div class="connection-stat"><span class="k">Connected now</span><span class="value" id="conn-core-current">-</span></div>
            <div class="connection-stat"><span class="k">Current limit</span><span class="value limit" id="conn-core-current-limit">-</span></div>
          </div>
          <div class="connection-control-row">
            <label>New limit<input id="conn-core-limit" type="number" min="0" max="10000" step="1" inputmode="numeric" value="0"></label>
            <button type="button" id="conn-core-apply">Apply + restart Core</button>
          </div>
          <div class="connection-status" id="conn-core-status">Loading...</div>
        </div>
        <div class="connection-service">
          <div class="connection-service-title">ElectrumX · client sessions</div>
          <div class="connection-help">Wallets and other Electrum-protocol clients connected to this ElectrumX server. These are client sessions, not Ravencoin P2P peers.</div>
          <div class="connection-stats">
            <div class="connection-stat"><span class="k">Connected now</span><span class="value" id="conn-electrumx-current">-</span></div>
            <div class="connection-stat"><span class="k">Current limit</span><span class="value limit" id="conn-electrumx-current-limit">-</span></div>
          </div>
          <div class="connection-control-row">
            <label>New limit<input id="conn-electrumx-limit" type="number" min="0" max="10000" step="1" inputmode="numeric" value="0"></label>
            <button type="button" id="conn-electrumx-apply">Apply + restart ElectrumX</button>
          </div>
          <div class="connection-status" id="conn-electrumx-status">Loading...</div>
        </div>
      </div>
      <div class="connection-note"><b>Connected now</b> is the live number of connections. <b>Current limit</b> is the limit currently active or, when no explicit deployment setting is present, the application's native default: Ravencoin Core <b>125 peers</b>, ElectrumX <b>1000 clients</b>. <b>New limit</b> is the value to apply. Entering <b>0</b> removes the monitor override and restores the deployment/native default; it never means zero connections. Core uses native <code>-maxconnections</code>, while ElectrumX uses native <code>MAX_SESSIONS</code>. Applying a change restarts only the selected service, so its peers/clients disconnect briefly and reconnect normally.</div>
    `;

    if (anchor.id === "card-charts") anchor.parentNode.insertBefore(card, anchor);
    else anchor.after(card);
    return true;
  }

  function setServiceEnabled(service, enabled) {
    const input = document.getElementById(`conn-${service}-limit`);
    const button = document.getElementById(`conn-${service}-apply`);
    if (input) input.disabled = !enabled;
    if (button) button.disabled = !enabled;
  }

  function currentLimitLabel(service, data) {
    if (!data) return "-";
    const noun = service === "core" ? "peers" : "clients";
    const configured = Number(data.configured_limit || 0);
    if (data.running_limit !== null && data.running_limit !== undefined && Number.isFinite(Number(data.running_limit))) {
      const running = Number(data.running_limit);
      const suffix = configured === 0 ? " · deployment" : "";
      return `${running.toLocaleString()} ${noun}${suffix}`;
    }
    if (configured > 0 && data.applied === true) return `${configured.toLocaleString()} ${noun}`;
    if (configured > 0 && data.applied === false) return "Pending apply";
    const nativeDefault = NATIVE_DEFAULTS[service];
    return `${nativeDefault.toLocaleString()} ${noun} · native default`;
  }

  function renderService(service, data) {
    const status = document.getElementById(`conn-${service}-status`);
    const input = document.getElementById(`conn-${service}-limit`);
    set(`conn-${service}-current`, data && data.current_connections !== null && data.current_connections !== undefined
      ? Number(data.current_connections).toLocaleString()
      : "-");
    set(`conn-${service}-current-limit`, currentLimitLabel(service, data));

    if (!data) {
      if (status) { status.textContent = "Host controller unavailable"; status.classList.add("error"); }
      setServiceEnabled(service, false);
      return;
    }

    const configured = Number(data.configured_limit || 0);
    if (input && document.activeElement !== input) input.value = String(configured);

    let text;
    let isError = false;
    if (data.error) {
      text = data.error;
      isError = true;
    } else if (!data.compose_managed) {
      text = "Docker Compose metadata unavailable; cannot safely recreate this service.";
      isError = true;
    } else if (configured > 0 && data.applied === false) {
      text = `Monitor override ${configured.toLocaleString()} is configured but not active yet.`;
      isError = true;
    } else if (configured > 0) {
      text = `Monitor override active: ${configured.toLocaleString()}.`;
    } else if (data.running_limit !== null && data.running_limit !== undefined) {
      text = `Using deployment setting ${Number(data.running_limit).toLocaleString()}; monitor is not overriding it.`;
    } else {
      const noun = service === "core" ? "peers" : "clients";
      const nativeDefault = NATIVE_DEFAULTS[service];
      text = `Using native default ${nativeDefault.toLocaleString()} ${noun}; monitor is not overriding it.`;
    }

    if (status) {
      status.textContent = text;
      status.classList.toggle("error", isError);
    }
    setServiceEnabled(service, writeEnabled && data.compose_managed === true && data.status === "active");
  }

  function render(payload) {
    const card = document.getElementById("card-connection-control");
    if (!card) return;
    if (!payload || payload.enabled === false) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    maxLimit = Number(payload.max_limit || 10000);
    writeEnabled = payload.write_enabled === true;
    for (const service of ["core", "electrumx"]) renderService(service, (payload.services || {})[service]);

    if (!writeEnabled) {
      const reason = payload.write_disabled_reason || "Read-only: configure MONITOR_PASSWORD to change connection limits.";
      for (const service of ["core", "electrumx"]) {
        const data = (payload.services || {})[service];
        const status = document.getElementById(`conn-${service}-status`);
        if (status && data && !data.error) status.textContent = reason;
        setServiceEnabled(service, false);
      }
    }
    if (payload.error) {
      for (const service of ["core", "electrumx"]) {
        const status = document.getElementById(`conn-${service}-status`);
        if (status) { status.textContent = payload.error; status.classList.add("error"); }
        setServiceEnabled(service, false);
      }
    }
  }

  function readLimit(service) {
    const input = document.getElementById(`conn-${service}-limit`);
    const raw = input ? input.value.trim() : "";
    if (!/^\d+$/.test(raw)) throw new Error("Enter a whole number from 0 to 10,000.");
    const value = Number(raw);
    if (!Number.isSafeInteger(value) || value < 0 || value > maxLimit) {
      throw new Error(`Limit must be between 0 and ${maxLimit.toLocaleString()}.`);
    }
    return value;
  }

  async function apply(service) {
    if (!writeEnabled) return;
    const status = document.getElementById(`conn-${service}-status`);
    let limit;
    try {
      limit = readLimit(service);
    } catch (error) {
      if (status) { status.textContent = error.message; status.classList.add("error"); }
      return;
    }

    const name = service === "core" ? "Ravencoin Core" : "ElectrumX";
    const noun = service === "core" ? "peers" : "clients";
    const defaultValue = NATIVE_DEFAULTS[service];
    const target = limit === 0
      ? `the deployment/native default (normally ${defaultValue.toLocaleString()} ${noun})`
      : `${limit.toLocaleString()} maximum ${noun}`;
    const warning = `${name} must be restarted to apply ${target}. Current ${noun} will disconnect briefly.\n\nApply this change now?`;
    if (!window.confirm(warning)) return;

    setServiceEnabled(service, false);
    if (status) { status.textContent = `Restarting ${name}...`; status.classList.remove("error"); }
    try {
      const response = await fetch("/api/connections", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Content-Type": "application/json", "X-Ravencoin-Monitor-Control": "1" },
        body: JSON.stringify({ service, limit, confirm_restart: true }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      render(payload);
    } catch (error) {
      if (status) { status.textContent = error.message || "Apply failed"; status.classList.add("error"); }
      setServiceEnabled(service, true);
    }
  }

  async function refresh() {
    try {
      const response = await fetch("/api/connections", { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) return;
      render(await response.json());
    } catch (_) {
      // Optional host controller; retain the last state on transient failures.
    }
  }

  function wire() {
    for (const service of ["core", "electrumx"]) {
      const button = document.getElementById(`conn-${service}-apply`);
      const input = document.getElementById(`conn-${service}-limit`);
      if (button) button.addEventListener("click", () => apply(service));
      if (input) input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") apply(service);
      });
    }
  }

  function start() {
    if (!installCard()) return;
    wire();
    refresh();
    setInterval(refresh, 10000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
