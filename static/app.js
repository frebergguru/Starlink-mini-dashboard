// Starlink Mini · Web UI
// Vanilla JS — no inline handlers, no frameworks.

(() => {
  "use strict";

  const REFRESH_MS = 1000;
  const SLOW_REFRESH_TICKS = 5;
  const HISTORY_REFRESH_TICKS = 10;
  const THROUGHPUT_MAX_BPS = 300_000_000;

  let utcOffsetS = 0;
  let lastDiagnostics = null;
  let wifiSecretsCache = null;
  let wifiSecretsPromise = null;
  let vaultSavedSet = null;      // Set<ssid> of vault entries, null = not loaded
  let vaultSavedPromise = null;

  // ───── Utilities ─────

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const isNaNString = (v) =>
    typeof v === "string" && v.trim().toLowerCase() === "nan";

  const isMissing = (v) =>
    v === null || v === undefined || v === "" || v === "N/A" || isNaNString(v);

  const pick = (obj, ...keys) => {
    if (!obj || typeof obj !== "object") return undefined;
    for (const k of keys) {
      if (obj[k] !== undefined && obj[k] !== null) return obj[k];
    }
    return undefined;
  };

  const unwrap = (resp, ...keys) => {
    if (!resp || typeof resp !== "object") return resp;
    for (const k of keys) {
      if (resp[k] !== undefined) return resp[k];
    }
    return resp;
  };

  const titleCase = (s) =>
    String(s)
      .replace(/_/g, " ")
      .replace(/([A-Z])/g, " $1")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/\b\w/g, (c) => c.toUpperCase());

  const nfix = (n, digits) =>
    i18n.fmtNumber(n, { minimumFractionDigits: digits, maximumFractionDigits: digits });

  const fmtBps = (b) => {
    if (isMissing(b)) return "—";
    let n = Number(b);
    if (!isFinite(n)) return "—";
    const keys = ["units.bps", "units.kbps", "units.mbps", "units.gbps", "units.tbps"];
    let i = 0;
    while (Math.abs(n) >= 1000 && i < keys.length - 1) {
      n /= 1000;
      i++;
    }
    return `${nfix(n, 1)} ${i18n.t(keys[i])}`;
  };

  const fmtPct = (v) => {
    if (isMissing(v)) return "—";
    const n = Number(v);
    if (!isFinite(n)) return "—";
    const scaled = n <= 1 ? n * 100 : n;
    return `${nfix(scaled, 1)}%`;
  };

  const fmtUptime = (s) => {
    if (isMissing(s)) return "—";
    let n = Math.floor(Number(s));
    if (!isFinite(n)) return "—";
    const d = Math.floor(n / 86400);
    n -= d * 86400;
    const h = Math.floor(n / 3600);
    n -= h * 3600;
    const m = Math.floor(n / 60);
    const sec = n - m * 60;
    const parts = [];
    if (d) parts.push(`${d}d`);
    if (h) parts.push(`${h}h`);
    if (m) parts.push(`${m}m`);
    parts.push(`${sec}s`);
    return parts.join(" ");
  };

  const fmtMs = (v) => {
    if (isMissing(v)) return "—";
    const n = Number(v);
    if (!isFinite(n)) return "—";
    return `${nfix(n, 1)} ${i18n.t("units.ms")}`;
  };

  const fmtDeg = (v) => {
    if (isMissing(v)) return "—";
    const n = Number(v);
    if (!isFinite(n)) return "—";
    return `${nfix(n, 2)}°`;
  };

  const fmtSnr = (v) => {
    if (isMissing(v)) return "—";
    const n = Number(v);
    if (!isFinite(n)) return "—";
    return `${nfix(n, 1)} ${i18n.t("units.db")}`;
  };

  const fmtValue = (v) => {
    if (isMissing(v)) return "—";
    if (typeof v === "boolean") return v ? i18n.t("bool.yes") : i18n.t("bool.no");
    if (typeof v === "number") return Number.isInteger(v) ? i18n.fmtNumber(v) : nfix(v, 2);
    return String(v);
  };

  // ───── DOM builders ─────

  const el = (tag, attrs = {}, children = []) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("data-")) node.setAttribute(k, v);
      else node[k] = v;
    }
    for (const c of [].concat(children)) {
      if (c == null) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
  };

  const renderKV = (container, entries) => {
    container.replaceChildren();
    for (const [label, value, cls] of entries) {
      const dd = el("dd", { text: fmtValue(value) });
      if (isMissing(value)) dd.classList.add("na");
      if (cls) dd.classList.add(cls);
      container.appendChild(
        el("div", {}, [el("dt", { text: label }), dd])
      );
    }
  };

  // ───── API layer ─────

  const api = {
    async get(path) {
      const r = await fetch(path, { headers: { Accept: "application/json" } });
      const body = await r.json().catch(() => ({}));
      return { ok: r.ok, status: r.status, body };
    },
    async post(path, payload) {
      const r = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      const body = await r.json().catch(() => ({}));
      return { ok: r.ok, status: r.status, body };
    },
  };

  // ───── Toast notifications ─────

  const toastContainer = $("#toasts");
  const toast = (kind, title, body = "", ttl = 3600) => {
    const node = el("div", { class: `toast toast-${kind}` }, [
      el("div", { class: "toast-title", text: title }),
      body ? el("div", { class: "toast-body", text: body }) : null,
    ]);
    toastContainer.appendChild(node);
    setTimeout(() => {
      node.classList.add("toast-out");
      setTimeout(() => node.remove(), 240);
    }, ttl);
  };

  // ───── Confirm modal ─────

  const confirmModal = $("#confirm-modal");
  const confirmTitle = $("#confirm-title");
  const confirmMsg = $("#confirm-msg");
  const confirmOk = $("#confirm-ok");

  const confirmAction = (title, message, okLabel = "Confirm", danger = true) =>
    new Promise((resolve) => {
      confirmTitle.textContent = title;
      confirmMsg.textContent = message;
      confirmOk.textContent = okLabel;
      confirmOk.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
      confirmModal.showModal();
      const handler = () => {
        confirmModal.removeEventListener("close", handler);
        resolve(confirmModal.returnValue === "ok");
      };
      confirmModal.addEventListener("close", handler);
    });

  // ───── Tab switching ─────

  const initTabs = () => {
    const tabs = $$(".tab");
    const panels = $$(".tab-panel");
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        tabs.forEach((t) => t.setAttribute("aria-selected", "false"));
        tab.setAttribute("aria-selected", "true");
        const id = tab.dataset.tab;
        panels.forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${id}`));
        if (id === "history") refreshHistory();
        if (id === "router") refreshRouter();
      });
    });
  };

  // ───── Connection state ─────

  const setConn = (dotId, addrId, info) => {
    const dot = document.getElementById(dotId);
    const addr = document.getElementById(addrId);
    if (!dot || !addr) return;
    if (info && info.connected) {
      dot.dataset.state = "connected";
    } else {
      dot.dataset.state = "disconnected";
    }
    addr.textContent = (info && info.address) || "";
  };

  const refreshState = async () => {
    const r = await api.get("/api/state");
    const body = r.body || {};
    setConn("conn-dot", "conn-addr", body.dish);
    setConn("router-conn-dot", "router-conn-addr", body.router);
    currentState = body;
    return body;
  };

  // ───── Telemetry rendering ─────

  const renderStatus = (root) => {
    const s = unwrap(root, "getStatus", "dish_get_status", "dishGetStatus");
    if (!s || typeof s !== "object") return;

    // Hero throughput
    const dl = pick(s, "downlink_throughput_bps", "downlinkThroughputBps") || 0;
    const ul = pick(s, "uplink_throughput_bps", "uplinkThroughputBps") || 0;
    const lat = pick(s, "pop_ping_latency_ms", "popPingLatencyMs");
    const drop = pick(s, "pop_ping_drop_rate", "popPingDropRate");
    const snrRaw = pick(s, "is_snr_above_noise_floor", "snr_above_noise_floor", "snrAboveNoiseFloor");
    const snrNumField = pick(s, "snr", "snr_db", "snrDb");
    let snrBool = null;
    let snrNum = null;
    if (typeof snrRaw === "boolean") snrBool = snrRaw;
    else if (snrRaw != null && isFinite(Number(snrRaw))) snrNum = Number(snrRaw);
    if (snrNum == null && snrNumField != null && isFinite(Number(snrNumField))) {
      snrNum = Number(snrNumField);
    }
    const snrTag = snrBool == null ? null : (snrBool ? i18n.t("snr.above_floor") : i18n.t("snr.below_floor"));
    const snrDisplay =
      snrNum != null && snrTag ? `${fmtSnr(snrNum)} · ${snrTag}`
      : snrNum != null ? fmtSnr(snrNum)
      : snrTag ? snrTag
      : "—";

    const devState = pick(s, "device_state", "deviceState") || {};
    const up = pick(devState, "uptime_s", "uptimeS");

    $("#tp-dl").textContent = fmtBps(dl);
    $("#tp-ul").textContent = fmtBps(ul);

    const dlPct = Math.min(100, (Number(dl) / THROUGHPUT_MAX_BPS) * 100);
    const ulPct = Math.min(100, (Number(ul) / THROUGHPUT_MAX_BPS) * 100);
    $("#bar-dl").style.width = `${dlPct}%`;
    $("#bar-ul").style.width = `${ulPct}%`;

    $("#m-lat").textContent = fmtMs(lat);
    $("#m-drop").textContent = fmtPct(drop);
    $("#m-snr").textContent = snrDisplay;
    $("#m-uptime").textContent = fmtUptime(up);

    // Status pill
    const disablement = pick(s, "disablement_code") || "UNKNOWN";
    const pill = $("#status-pill");
    pill.textContent = disablement;
    pill.dataset.state = disablement === "OKAY" ? "OKAY" : "warn";

    // Track UTC offset so the Power Save timeline renders in local time
    const devInfo = pick(s, "device_info", "deviceInfo") || {};
    const reportedOffset = Number(pick(devInfo, "utc_offset_s", "utcOffsetS"));
    if (isFinite(reportedOffset)) utcOffsetS = reportedOffset;

    // Status KV
    const mobility = pick(s, "mobility_class") || "—";
    const classOfService = pick(s, "class_of_service", "classOfService");
    const updateState = pick(s, "software_update_state", "softwareUpdateState");
    const eth = pick(s, "eth_speed_mbps", "ethSpeedMbps");
    const selfTest = lastDiagnostics ? pick(lastDiagnostics, "hardware_self_test", "hardwareSelfTest") : null;
    const selfTestText =
      selfTest === "PASSED" ? i18n.t("selftest.passed")
      : selfTest === "FAILED" ? i18n.t("selftest.failed")
      : selfTest || null;
    const selfTestCls = selfTest === "PASSED" ? "good" : selfTest === "FAILED" ? "bad" : null;
    renderKV($("#kv-status"), [
      [i18n.t("kv.disablement"), disablement, disablement === "OKAY" ? "good" : "warn"],
      [i18n.t("kv.mobility_class"), mobility],
      [i18n.t("kv.uptime"), fmtUptime(up)],
      [i18n.t("kv.class_of_service"), classOfService],
      [i18n.t("kv.software_update"), updateState],
      [i18n.t("kv.ethernet_speed"), eth ? i18n.t("units.eth_speed", { n: eth }) : null],
      [i18n.t("kv.self_test"), selfTestText, selfTestCls],
    ]);

    // Alerts
    const alerts = pick(s, "alerts") || {};

    // Live snow-melt heating indicator
    const isHeating = Boolean(alerts.is_heating || alerts.dish_is_heating);
    const snowChips = $("#cfg-snow-chips");
    if (snowChips) {
      snowChips.replaceChildren();
      if (isHeating) {
        snowChips.appendChild(
          el("span", { class: "chip chip-heating", "data-on": "true", text: i18n.t("snow.heating_now") })
        );
      }
    }
    const active = Object.entries(alerts)
      .filter(([, v]) => v)
      .map(([k]) => k);
    const alertsEl = $("#alerts");
    alertsEl.replaceChildren();
    if (active.length) {
      for (const a of active) {
        alertsEl.appendChild(el("span", { class: "alert-chip", text: titleCase(a) }));
      }
    } else {
      alertsEl.appendChild(el("span", { class: "alert-none", text: i18n.t("alerts.none") }));
    }

    // Signal & ready states
    const rs = pick(s, "ready_states", "readyStates") || {};
    const signalClass = snrBool == null ? null : (snrBool ? "good" : "warn");
    const signalEntries = [
      [i18n.t("kv.snr"), snrDisplay === "—" ? null : snrDisplay, signalClass],
      [i18n.t("kv.class_of_service"), classOfService],
      [i18n.t("kv.software_update"), updateState],
    ];
    for (const [k, v] of Object.entries(rs)) {
      signalEntries.push([titleCase(k), v, v === true ? "good" : v === false ? "bad" : null]);
    }
    renderKV($("#kv-signal"), signalEntries);

    // GPS card from status (fallback — location endpoint fills it properly)
    const gps = pick(s, "gps_stats", "gpsStats") || {};
    const tilt = pick(s, "tilt_angle_deg", "tiltAngleDeg");
    const az = pick(s, "boresight_azimuth_deg", "boresightAzimuthDeg");
    const elev = pick(s, "boresight_elevation_deg", "boresightElevationDeg");
    const fallbackEntries = [
      [i18n.t("kv.gps_valid"), gps.gps_valid, gps.gps_valid === true ? "good" : gps.gps_valid === false ? "bad" : null],
      [i18n.t("kv.satellites"), gps.gps_sats],
      [i18n.t("kv.tilt"), fmtDeg(tilt)],
      [i18n.t("kv.azimuth"), fmtDeg(az)],
      [i18n.t("kv.elevation"), fmtDeg(elev)],
    ];
    if (!lastLocation) renderKV($("#kv-location"), fallbackEntries);

    // Obstruction stats
    const obs = pick(s, "obstruction_stats", "obstructionStats") || {};
    const obsEntries = [];
    for (const [k, v] of Object.entries(obs)) {
      if (Array.isArray(v)) continue;
      if (k.toLowerCase().includes("fraction")) obsEntries.push([titleCase(k), fmtPct(v)]);
      else obsEntries.push([titleCase(k), v]);
    }
    renderKV($("#kv-obstruction"), obsEntries);

    // Alignment sky view
    renderAlignment(s);

    // Connected routers (REMOVABLE)
    renderConnectedRouters(s);

    // Device info
    const di = pick(s, "device_info", "deviceInfo") || {};
    const deviceEntries = [
      [i18n.t("kv.id"), di.id],
      [i18n.t("kv.hardware"), pick(di, "hardware_version", "hardwareVersion")],
      [i18n.t("kv.software"), pick(di, "software_version", "softwareVersion")],
      [i18n.t("kv.country"), pick(di, "country_code", "countryCode")],
      [i18n.t("kv.boot_count"), pick(di, "bootcount")],
      [i18n.t("kv.generation"), pick(di, "generation_number", "generationNumber")],
    ];
    renderKV($("#kv-device"), deviceEntries);
  };

  // ═══════════ REMOVABLE: Connected Routers card rendering ═══════════
  // To remove: delete this block (down to the matching END marker), plus the
  // `renderConnectedRouters(s)` call in renderStatus(), and the matching
  // markers in index.html + style.css.

  const diffSecsFromNs = (ns) => {
    const secs = Number(ns) / 1e9;
    if (!isFinite(secs) || secs < 1577836800) return null;
    return (Date.now() - secs * 1000) / 1000;
  };

  const timeAgoFromNs = (ns) => {
    const diff = diffSecsFromNs(ns);
    if (diff == null) return "";
    if (diff < 2) return i18n.t("time.now");
    if (diff < 60) return i18n.t("time.s_ago", { n: Math.floor(diff) });
    if (diff < 3600) return i18n.t("time.m_ago", { n: Math.floor(diff / 60) });
    if (diff < 86400) return i18n.t("time.h_ago", { n: Math.floor(diff / 3600) });
    return i18n.t("time.d_ago", { n: Math.floor(diff / 86400) });
  };

  const initialsFor = (name) => {
    const s = String(name || "").trim();
    if (!s) return "?";
    const parts = s.split(/[\s\-_]+/).filter(Boolean);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return s.slice(0, 2).toUpperCase();
  };

  const renderEmpty = (container, title, hint) => {
    container.replaceChildren(
      el("div", { class: "device-empty" }, [
        el("div", { class: "device-empty-title", text: title }),
        hint ? el("div", { class: "device-empty-hint", text: hint }) : null,
      ])
    );
  };

  const renderConnectedRouters = (statusDict) => {
    const card = document.getElementById("card-routers");
    if (!card) return;
    const list = $("#routers-list");
    const count = $("#routers-count");

    const connectedIds = pick(statusDict, "connected_routers") || [];
    const downstream = pick(statusDict, "downstream_routers") || {};
    const ids = Array.isArray(connectedIds) ? connectedIds : [];
    count.textContent = ids.length ? i18n.tp("router.connected", ids.length) : "";

    if (!ids.length) {
      renderEmpty(list, i18n.t("empty.routers.title"), i18n.t("empty.routers.hint"));
      return;
    }

    list.replaceChildren();
    for (const id of ids) {
      const info = (downstream && downstream[id]) || {};
      const role = info.role || "UNKNOWN";
      const lastSeen = info.last_seen || info.lastSeen;
      const ago = lastSeen ? timeAgoFromNs(lastSeen) : "";
      const diffSecs = lastSeen ? diffSecsFromNs(lastSeen) : null;
      const stale = diffSecs != null && diffSecs >= 300;
      const shortId = id.replace(/^Router-/, "").slice(-12);
      const row = el("div", { class: "device-row" }, [
        el("div", { class: "device-row-avatar", text: "RT" }),
        el("div", { class: "device-row-body" }, [
          el("div", { class: "device-row-name", text: role === "CONTROLLER" ? i18n.t("router.controller") : role.replace(/_/g, " ") }),
          el("div", { class: "device-row-sub", text: shortId }),
        ]),
        el("div", { class: "device-row-meta" }, [
          el("span", {
            class: "device-row-status",
            "data-state": stale ? "stale" : "live",
            text: stale ? i18n.t("router.stale") : i18n.t("router.live"),
          }),
          el("span", { text: ago || "—" }),
        ]),
      ]);
      list.appendChild(row);
    }
  };

  // ═══════════ END REMOVABLE ═══════════

  // ───── Router tab ─────

  const BYTES_UNITS = ["B", "KB", "MB", "GB", "TB"];
  const fmtBytes = (n) => {
    let v = Number(n);
    if (!isFinite(v) || v < 0) return "—";
    let i = 0;
    while (v >= 1024 && i < BYTES_UNITS.length - 1) {
      v /= 1024;
      i++;
    }
    return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${BYTES_UNITS[i]}`;
  };

  const fmtMegabytes = (mb) => {
    if (mb == null) return "—";
    const n = Number(mb);
    if (!isFinite(n)) return "—";
    if (n >= 1024) return `${(n / 1024).toFixed(1)} GB`;
    return `${n.toFixed(0)} MB`;
  };

  const shortBand = (b) => {
    if (!b) return "";
    return String(b).replace(/^RF_/, "").replace("2GHZ", "2.4 GHz").replace("5GHZ_HIGH", "5 GHz (high)").replace("5GHZ", "5 GHz");
  };

  const renderRouterStatus = (data) => {
    const s = unwrap(data, "wifi_get_status", "get_status") || {};
    const di = s.device_info || {};
    const up = pick(s.device_state || {}, "uptime_s", "uptimeS");

    const pill = $("#router-status-pill");
    const alerts = s.alerts || {};
    const active = Object.entries(alerts).filter(([, v]) => v).map(([k]) => k);
    if (active.length) {
      pill.textContent = i18n.tp("pill.alert", active.length);
      pill.dataset.state = "warn";
    } else {
      pill.textContent = i18n.t("pill.healthy");
      pill.dataset.state = "OKAY";
    }

    renderKV($("#kv-router-hero"), [
      [i18n.t("kv.uptime"), fmtUptime(up)],
      [i18n.t("kv.ping_to_dish"), s.dish_ping_latency_ms != null ? fmtMs(s.dish_ping_latency_ms) : null],
      [i18n.t("kv.ping_to_pop"), s.pop_ping_latency_ms != null ? fmtMs(s.pop_ping_latency_ms) : null],
      [i18n.t("kv.ping_wan"), s.ping_latency_ms != null ? fmtMs(s.ping_latency_ms) : null],
    ]);

    const leaseCount = (s.dhcp_servers || []).reduce(
      (acc, srv) => acc + (srv.leases ? srv.leases.length : 0),
      0
    );
    renderKV($("#kv-router-status"), [
      [i18n.t("kv.router_id"), di.id],
      [i18n.t("kv.hardware"), di.hardware_version],
      [i18n.t("kv.software"), di.software_version],
      [i18n.t("kv.country"), di.country_code],
      [i18n.t("kv.boot_count"), di.bootcount],
      [i18n.t("kv.dish_cohoused"), di.dish_cohoused],
      [i18n.t("kv.ipv4_wan"), s.ipv4_wan_address],
      [i18n.t("kv.ipv6_wan"), Array.isArray(s.ipv6_wan_addresses) ? s.ipv6_wan_addresses[0] : null],
      [i18n.t("kv.dhcp_leases"), leaseCount || null],
      [i18n.t("kv.active_alerts"), active.length ? active.join(", ") : i18n.t("alerts.no_alerts"),
        active.length ? "warn" : "good"],
    ]);
  };

  const renderRouterClients = (data) => {
    const resp = unwrap(data, "wifi_get_clients", "wifiGetClients") || {};
    const clients = Array.isArray(resp.clients) ? resp.clients : [];
    const list = $("#router-clients-list");
    const count = $("#router-clients-count");

    count.textContent = clients.length ? i18n.tp("clients.connected", clients.length) : "";
    list.replaceChildren();

    if (!clients.length) {
      renderEmpty(list, i18n.t("empty.clients.title"), i18n.t("empty.clients.hint"));
      return;
    }

    // Sort: controllers first, then by signal strength (strongest first)
    const sorted = [...clients].sort((a, b) => {
      if (a.role === "CONTROLLER" && b.role !== "CONTROLLER") return -1;
      if (b.role === "CONTROLLER" && a.role !== "CONTROLLER") return 1;
      const sa = Number(a.signal_strength || -999);
      const sb = Number(b.signal_strength || -999);
      return sb - sa;
    });

    for (const c of sorted) {
      const name = c.name || c.hostname || c.given_name || c.mac_address || "unnamed";
      const mac = c.mac_address || "";
      const ip = c.ip_address || "";
      const band = shortBand(c.iface);
      const rssi = c.signal_strength != null ? Number(c.signal_strength) : null;
      const snr = c.snr != null ? Number(c.snr) : null;
      const txRate = c.tx_stats && c.tx_stats.rate_mbps;
      const rxRate = c.rx_stats && c.rx_stats.rate_mbps;
      const up = c.upload_mb;
      const down = c.download_mb;
      const assoc = c.associated_time_s;

      const subparts = [mac, ip].filter(Boolean).join(" · ");
      const metaLines = [];
      if (rssi != null) metaLines.push(`${i18n.fmtNumber(rssi, { maximumFractionDigits: 0 })} dBm`);
      if (band) metaLines.push(band);
      if (txRate || rxRate) metaLines.push(`${rxRate || 0}↓ ${txRate || 0}↑ ${i18n.t("units.mbps")}`);
      if (down != null || up != null) metaLines.push(`${fmtMegabytes(down)} / ${fmtMegabytes(up)}`);
      if (assoc) metaLines.push(i18n.t("time.assoc", { dur: fmtUptime(assoc) }));
      if (snr != null) metaLines.push(i18n.t("snr.short", { n: i18n.fmtNumber(snr, { maximumFractionDigits: 0 }) }));

      const isController = c.role === "CONTROLLER";
      const row = el("div", { class: "device-row" }, [
        el("div", { class: "device-row-avatar", text: isController ? "RT" : initialsFor(name) }),
        el("div", { class: "device-row-body" }, [
          el("div", { class: "device-row-name", text: name }),
          el("div", { class: "device-row-sub", text: subparts }),
        ]),
        el(
          "div",
          { class: "device-row-meta" },
          metaLines.length
            ? metaLines.slice(0, 4).map((t) => el("span", { text: t }))
            : [el("span", { class: "device-row-status", text: i18n.t("router.online") })]
        ),
      ]);
      list.appendChild(row);
    }
  };

  // Per-SSID metadata coming from the router (auth type + masked flag).
  // The actual passphrase is kept in localStorage because current Starlink
  // firmware only returns "•••••" over the LAN gRPC API.
  const fetchWifiSecrets = async () => {
    if (wifiSecretsCache) return wifiSecretsCache;
    if (wifiSecretsPromise) return wifiSecretsPromise;
    wifiSecretsPromise = (async () => {
      const r = await api.get("/api/router/wifi_secrets");
      if (!r.ok) {
        wifiSecretsPromise = null;
        toast("error", i18n.t("wifi.secrets_failed"), r.body.error || "");
        return null;
      }
      const map = {};
      for (const net of r.body.networks || []) {
        if (net && net.ssid) map[net.ssid] = net;
      }
      wifiSecretsCache = map;
      wifiSecretsPromise = null;
      return map;
    })();
    return wifiSecretsPromise;
  };

  const loadVaultSsids = async (force) => {
    if (!force && vaultSavedSet) return vaultSavedSet;
    if (vaultSavedPromise) return vaultSavedPromise;
    vaultSavedPromise = (async () => {
      const r = await api.get("/api/vault/list");
      vaultSavedPromise = null;
      if (!r.ok) return new Set();
      vaultSavedSet = new Set(r.body.ssids || []);
      return vaultSavedSet;
    })();
    return vaultSavedPromise;
  };

  const vaultHasSsid = (ssid) => Boolean(vaultSavedSet && vaultSavedSet.has(ssid));

  const vaultGetPassword = async (ssid) => {
    const r = await api.post("/api/vault/get", { ssid });
    if (!r.ok) return null;
    return r.body.psk || null;
  };

  const vaultSetPassword = async (ssid, psk, auth) => {
    const r = await api.post("/api/vault/set", { ssid, psk, auth: auth || "WPA" });
    if (r.ok) {
      if (!vaultSavedSet) vaultSavedSet = new Set();
      vaultSavedSet.add(ssid);
    }
    return r.ok;
  };

  const vaultDeletePassword = async (ssid) => {
    const r = await api.post("/api/vault/delete", { ssid });
    if (r.ok && vaultSavedSet) vaultSavedSet.delete(ssid);
    return r.ok;
  };

  const openWifiQr = async (ssid) => {
    const modal = $("#qr-modal");
    const canvas = $("#qr-canvas");
    const ssidEl = $("#qr-ssid");
    ssidEl.textContent = ssid;
    canvas.replaceChildren(el("div", { class: "muted small", text: "…" }));
    modal.showModal();
    try {
      const resp = await fetch("/api/router/wifi_qr", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "image/svg+xml",
        },
        body: JSON.stringify({ ssid }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${resp.status}`);
      }
      const svgText = await resp.text();
      canvas.replaceChildren();
      canvas.insertAdjacentHTML("beforeend", svgText);
    } catch (err) {
      canvas.replaceChildren(
        el("div", { class: "muted small", text: err.message || String(err) })
      );
      toast("error", i18n.t("wifi.qr_failed"), err.message || "");
    }
  };

  const buildWifiActions = (card, ssid) => {
    const passValue = el("code", { class: "wifi-net-pass-value", text: "" });
    const copyBtn = el("button", {
      type: "button",
      class: "btn btn-ghost wifi-net-pass-copy",
      text: i18n.t("wifi.copy_password"),
    });
    const passRow = el("div", { class: "wifi-net-pass", "data-visible": "false" }, [
      el("span", { class: "wifi-net-pass-label", text: i18n.t("wifi.password_label") }),
      passValue,
      copyBtn,
    ]);

    const entryInput = el("input", {
      type: "password",
      class: "wifi-net-pass-input",
      placeholder: i18n.t("wifi.password_placeholder"),
      autocomplete: "off",
    });
    const saveBtn = el("button", { type: "button", class: "btn btn-primary", text: i18n.t("wifi.save") });
    const noteEl = el("p", { class: "muted small wifi-net-pass-note", text: i18n.t("wifi.masked_note") });
    const entryForm = el("form", { class: "wifi-net-pass-form", "data-visible": "false" }, [
      noteEl,
      el("div", { class: "wifi-net-pass-input-row" }, [entryInput, saveBtn]),
    ]);

    const showBtn = el("button", { type: "button", class: "btn btn-ghost", text: i18n.t("wifi.show_password") });
    const qrBtn = el("button", { type: "button", class: "btn btn-ghost", text: i18n.t("wifi.show_qr") });
    const forgetBtn = el("button", { type: "button", class: "btn btn-ghost wifi-net-forget", text: i18n.t("wifi.forget") });

    const syncForgetVisibility = () => {
      forgetBtn.style.display = vaultHasSsid(ssid) ? "" : "none";
    };
    syncForgetVisibility();

    const closePanels = () => {
      passRow.dataset.visible = "false";
      entryForm.dataset.visible = "false";
      showBtn.textContent = i18n.t("wifi.show_password");
    };

    const revealExistingPassword = (psk) => {
      passValue.textContent = psk;
      passRow.dataset.visible = "true";
      entryForm.dataset.visible = "false";
      showBtn.textContent = i18n.t("wifi.hide_password");
    };

    const promptForPassword = () => {
      passRow.dataset.visible = "false";
      entryForm.dataset.visible = "true";
      entryInput.value = "";
      entryInput.focus();
      showBtn.textContent = i18n.t("wifi.hide_password");
    };

    showBtn.addEventListener("click", async () => {
      if (passRow.dataset.visible === "true" || entryForm.dataset.visible === "true") {
        closePanels();
        return;
      }
      if (vaultHasSsid(ssid)) {
        const psk = await vaultGetPassword(ssid);
        if (psk) { revealExistingPassword(psk); return; }
      }
      promptForPassword();
    });

    saveBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      const value = (entryInput.value || "").trim();
      if (!value) return;
      const ok = await vaultSetPassword(ssid, value, "WPA");
      if (!ok) {
        toast("error", i18n.t("wifi.secrets_failed"));
        return;
      }
      syncForgetVisibility();
      toast("success", i18n.t("wifi.saved"));
      revealExistingPassword(value);
    });

    entryForm.addEventListener("submit", (e) => {
      e.preventDefault();
      saveBtn.click();
    });

    forgetBtn.addEventListener("click", async () => {
      const ok = await vaultDeletePassword(ssid);
      if (!ok) return;
      syncForgetVisibility();
      toast("success", i18n.t("wifi.forgotten"));
      closePanels();
    });

    qrBtn.addEventListener("click", async () => {
      if (!vaultHasSsid(ssid)) {
        toast("error", i18n.t("wifi.qr_failed"), i18n.t("wifi.need_password"));
        promptForPassword();
        return;
      }
      openWifiQr(ssid);
    });

    copyBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      const value = passValue.textContent || "";
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
        toast("success", i18n.t("wifi.copied"));
      } catch {}
    });

    const actions = el("div", { class: "wifi-net-actions" }, [showBtn, qrBtn, forgetBtn]);
    card.appendChild(actions);
    card.appendChild(passRow);
    card.appendChild(entryForm);
  };

  const renderRouterConfig = (data) => {
    const wifi = pick(
      unwrap(data, "wifi_get_config", "wifiGetConfig") || {},
      "wifi_config", "wifiConfig"
    ) || {};
    const networks = wifi.networks || [];
    const container = $("#router-networks");
    const count = $("#router-wifi-count");

    count.textContent = networks.length ? i18n.tp("wifi.networks", networks.length) : "";
    container.replaceChildren();

    if (!networks.length) {
      renderEmpty(container, i18n.t("empty.wifi"), "");
      return;
    }

    for (const net of networks) {
      const bss = net.basic_service_sets || [];
      const ssid = bss[0] && bss[0].ssid ? bss[0].ssid : i18n.t("wifi.hidden");
      const isGuest = Boolean(net.guest);
      const card = el("div", { class: `wifi-net${isGuest ? " wifi-net-guest" : ""}` });

      const head = el("div", { class: "wifi-net-head" }, [
        el("span", { class: "wifi-net-ssid", text: ssid }),
      ]);
      if (isGuest) head.appendChild(el("span", { class: "wifi-net-tag", text: i18n.t("wifi.guest") }));
      if (net.client_isolation) head.appendChild(el("span", { class: "wifi-net-tag", text: i18n.t("wifi.isolated") }));
      card.appendChild(head);

      const bands = el("div", { class: "wifi-net-bands" });
      for (const b of bss) {
        bands.appendChild(
          el("span", { class: "wifi-band-chip", text: shortBand(b.band) })
        );
      }
      card.appendChild(bands);

      const lines = [];
      if (net.ipv4) lines.push(`${net.ipv4}`);
      if (net.domain) lines.push(i18n.t("wifi.domain", { v: net.domain }));
      if (net.vlan) lines.push(i18n.t("wifi.vlan", { v: net.vlan }));
      if (net.dhcpv4_lease_duration_s) lines.push(i18n.t("wifi.dhcp_lease", { v: net.dhcpv4_lease_duration_s }));
      if (lines.length) {
        card.appendChild(el("div", { class: "wifi-net-detail", text: lines.join(" · ") }));
      }

      if (bss[0] && bss[0].ssid) buildWifiActions(card, bss[0].ssid);

      container.appendChild(card);
    }
  };

  const renderRouterRadios = (data) => {
    const resp = unwrap(data, "get_radio_stats") || {};
    const radios = resp.radio_stats || [];
    const list = $("#router-radios");
    list.replaceChildren();

    if (!radios.length) {
      renderEmpty(list, i18n.t("empty.radio"), "");
      return;
    }

    for (const r of radios) {
      const temp = r.thermal_status && r.thermal_status.temp2;
      const duty = r.thermal_status && r.thermal_status.duty_cycle;
      const row = el("div", { class: "radio-row" });

      const tempEl = el("span", {
        class: "radio-temp-value",
        text: temp != null ? `${i18n.fmtNumber(temp, { maximumFractionDigits: 0 })}°C` : "—",
      });
      if (temp != null) {
        if (Number(temp) > 70) tempEl.dataset.hot = "true";
        else if (Number(temp) > 55) tempEl.dataset.warm = "true";
      }

      row.appendChild(
        el("div", { class: "radio-row-head" }, [
          el("span", { class: "radio-band", text: shortBand(r.band) }),
          el("div", { class: "radio-temp" }, [el("span", { text: i18n.t("radio.temp") }), tempEl]),
        ])
      );

      const rxBytes = r.rx_stats && r.rx_stats.bytes;
      const txBytes = r.tx_stats && r.tx_stats.bytes;
      const rxPkts = r.rx_stats && r.rx_stats.packets;
      const txPkts = r.tx_stats && r.tx_stats.packets;

      const statsGrid = el("div", { class: "radio-stats-grid" });
      for (const [label, value] of [
        [i18n.t("radio.rx"), fmtBytes(rxBytes)],
        [i18n.t("radio.tx"), fmtBytes(txBytes)],
        [i18n.t("radio.rx_pkts"), rxPkts || "—"],
        [i18n.t("radio.tx_pkts"), txPkts || "—"],
        [i18n.t("radio.duty"), duty != null ? `${duty}%` : "—"],
      ]) {
        statsGrid.appendChild(
          el("div", { class: "radio-stat" }, [
            el("div", { class: "radio-stat-label", text: label }),
            el("div", { class: "radio-stat-value", text: String(value) }),
          ])
        );
      }
      row.appendChild(statsGrid);

      const ant = r.antenna_status || {};
      const antRow = el("div", { class: "radio-antennas" });
      for (const k of ["rssi1", "rssi2", "rssi3", "rssi4"]) {
        const v = ant[k];
        const num = typeof v === "number" ? v : parseFloat(v);
        const show = isFinite(num) ? `${num.toFixed(0)}` : "—";
        antRow.appendChild(
          el("div", { class: "radio-antenna" }, [
            el("span", { class: "radio-antenna-label", text: k.toUpperCase() }),
            el("span", { class: "radio-antenna-value", text: show }),
          ])
        );
      }
      row.appendChild(antRow);

      list.appendChild(row);
    }
  };

  const renderRouterSelfTest = (data) => {
    const resp = unwrap(data, "wifi_self_test") || {};
    const st = resp.self_test || {};
    const list = $("#router-selftest");
    list.replaceChildren();

    const rows = [];
    const pushItem = (name, obj) => {
      if (!obj || typeof obj !== "object") return;
      const pass = Boolean(obj.success);
      rows.push({ name, pass, reason: obj.failure_reason || "" });
    };
    for (const k of Object.keys(st)) {
      const v = st[k];
      if (Array.isArray(v)) {
        for (const item of v) pushItem(item.name || k, item);
      } else {
        pushItem(v.name || k, v);
      }
    }

    if (!rows.length) {
      renderEmpty(list, i18n.t("empty.selftest"), "");
      return;
    }

    for (const r of rows) {
      const row = el("div", { class: "selftest-row", "data-pass": String(r.pass) }, [
        el("div", { class: "selftest-icon", text: r.pass ? "✓" : "✗" }),
        el("div", { class: "selftest-name", text: r.name }),
        el("div", { class: "selftest-status", text: r.pass ? i18n.t("selftest.pass") : i18n.t("selftest.fail") }),
      ]);
      if (!r.pass && r.reason) {
        row.appendChild(el("div", { class: "selftest-reason", text: r.reason }));
      }
      list.appendChild(row);
    }
  };

  const renderRouterInterfaces = (data) => {
    const resp = unwrap(data, "get_network_interfaces") || {};
    const ifaces = resp.network_interfaces || [];
    const list = $("#router-interfaces");
    const count = $("#router-iface-count");

    count.textContent = ifaces.length ? i18n.t("iface.up_count", { up: ifaces.filter((i) => i.up).length, total: ifaces.length }) : "";
    list.replaceChildren();

    if (!ifaces.length) {
      renderEmpty(list, i18n.t("empty.interfaces"), "");
      return;
    }

    for (const iface of ifaces) {
      const up = Boolean(iface.up);
      const kind = iface.ethernet ? "ETH" : iface.wifi ? "WIFI" : "";
      const addrs = [
        ...(iface.ipv4_addresses || []),
        ...(iface.ipv6_addresses || []).slice(0, 1),
      ].join(" · ");

      const meta = [];
      if (iface.mac_address) meta.push(el("span", {}, [el("strong", { text: i18n.t("iface.mac") }), document.createTextNode(iface.mac_address)]));
      if (iface.ethernet) {
        const eth = iface.ethernet;
        meta.push(
          el("span", {}, [
            el("strong", { text: i18n.t("iface.link") }),
            document.createTextNode(`${eth.speed_mbps || "?"} ${i18n.t("units.mbps")} ${eth.duplex || ""}`),
          ])
        );
      }
      if (iface.wifi && iface.wifi.channel) {
        meta.push(
          el("span", {}, [
            el("strong", { text: i18n.t("iface.ch") }),
            document.createTextNode(String(iface.wifi.channel)),
          ])
        );
      }
      const rxB = iface.rx_stats && iface.rx_stats.bytes;
      const txB = iface.tx_stats && iface.tx_stats.bytes;
      if (rxB || txB) {
        meta.push(
          el("span", {}, [
            el("strong", { text: i18n.t("iface.rx_tx") }),
            document.createTextNode(`${fmtBytes(rxB)} / ${fmtBytes(txB)}`),
          ])
        );
      }

      const row = el("div", { class: "iface-row", "data-up": String(up) }, [
        el("div", { class: "iface-head" }, [
          el("span", { class: "iface-name", text: iface.name }),
          kind ? el("span", { class: "iface-kind", text: kind }) : null,
          el("span", { class: "iface-state", text: up ? i18n.t("iface.up") : i18n.t("iface.down") }),
        ]),
        addrs ? el("div", { class: "iface-addr", text: addrs }) : null,
        meta.length ? el("div", { class: "iface-meta" }, meta) : null,
      ]);
      list.appendChild(row);
    }
  };

  const refreshRouter = async () => {
    const [statusR, clientsR, configR, radioR, stR, ifaceR] = await Promise.all([
      api.get("/api/router/status"),
      api.get("/api/router/clients"),
      api.get("/api/router/config"),
      api.get("/api/router/radio_stats"),
      api.get("/api/router/self_test"),
      api.get("/api/router/network_interfaces"),
    ]);
    if (statusR.ok && statusR.body.data) renderRouterStatus(statusR.body.data);
    if (clientsR.ok && clientsR.body.data) renderRouterClients(clientsR.body.data);
    if (configR.ok && configR.body.data) renderRouterConfig(configR.body.data);
    if (radioR.ok && radioR.body.data) renderRouterRadios(radioR.body.data);
    if (stR.ok && stR.body.data) renderRouterSelfTest(stR.body.data);
    if (ifaceR.ok && ifaceR.body.data) renderRouterInterfaces(ifaceR.body.data);
    $("#router-last-update").textContent = i18n.t("updated", { time: i18n.fmtTime(new Date()) });
  };

  // ───── Alignment sky view ─────

  const ALIGN_CENTER = 110;
  const ALIGN_RADIUS = 92;
  let alignmentBackgroundBuilt = false;

  const buildAlignmentBackground = (svg) => {
    const ns = "http://www.w3.org/2000/svg";
    const cx = ALIGN_CENTER;
    const cy = ALIGN_CENTER;
    const R = ALIGN_RADIUS;

    const bg = document.createElementNS(ns, "circle");
    bg.setAttribute("cx", cx);
    bg.setAttribute("cy", cy);
    bg.setAttribute("r", R);
    bg.setAttribute("fill", "rgba(5,10,18,0.65)");
    bg.setAttribute("stroke", "rgba(86,204,242,0.25)");
    bg.setAttribute("stroke-width", "1");
    svg.appendChild(bg);

    for (const elev of [30, 60]) {
      const r = R * (1 - elev / 90);
      const ring = document.createElementNS(ns, "circle");
      ring.setAttribute("cx", cx);
      ring.setAttribute("cy", cy);
      ring.setAttribute("r", r);
      ring.setAttribute("fill", "none");
      ring.setAttribute("stroke", "rgba(138,160,191,0.22)");
      ring.setAttribute("stroke-width", "0.8");
      ring.setAttribute("stroke-dasharray", "2 3");
      svg.appendChild(ring);

      const label = document.createElementNS(ns, "text");
      label.setAttribute("x", cx + 4);
      label.setAttribute("y", cy - r + 10);
      label.setAttribute("fill", "rgba(138,160,191,0.55)");
      label.setAttribute("font-size", "8");
      label.setAttribute("font-family", "JetBrains Mono, monospace");
      label.textContent = `${elev}°`;
      svg.appendChild(label);
    }

    // Crosshair through center
    for (const [x1, y1, x2, y2] of [
      [cx - R, cy, cx + R, cy],
      [cx, cy - R, cx, cy + R],
    ]) {
      const l = document.createElementNS(ns, "line");
      l.setAttribute("x1", x1);
      l.setAttribute("y1", y1);
      l.setAttribute("x2", x2);
      l.setAttribute("y2", y2);
      l.setAttribute("stroke", "rgba(138,160,191,0.12)");
      l.setAttribute("stroke-width", "0.6");
      svg.appendChild(l);
    }

    // Minor compass ticks at 30° intervals
    for (let deg = 0; deg < 360; deg += 30) {
      if (deg % 90 === 0) continue;
      const rad = (deg * Math.PI) / 180;
      const x1 = cx + (R - 4) * Math.sin(rad);
      const y1 = cy - (R - 4) * Math.cos(rad);
      const x2 = cx + R * Math.sin(rad);
      const y2 = cy - R * Math.cos(rad);
      const t = document.createElementNS(ns, "line");
      t.setAttribute("x1", x1);
      t.setAttribute("y1", y1);
      t.setAttribute("x2", x2);
      t.setAttribute("y2", y2);
      t.setAttribute("stroke", "rgba(138,160,191,0.25)");
      t.setAttribute("stroke-width", "0.8");
      svg.appendChild(t);
    }

    // Compass labels (N is accent yellow for orientation cue)
    const labels = [
      ["N", cx, cy - R - 6, "#ffc35e"],
      ["E", cx + R + 9, cy + 4, "#8aa0bf"],
      ["S", cx, cy + R + 13, "#8aa0bf"],
      ["W", cx - R - 9, cy + 4, "#8aa0bf"],
    ];
    for (const [t, x, y, color] of labels) {
      const label = document.createElementNS(ns, "text");
      label.setAttribute("x", x);
      label.setAttribute("y", y);
      label.setAttribute("fill", color);
      label.setAttribute("font-size", "12");
      label.setAttribute("font-weight", "700");
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("font-family", "Inter, sans-serif");
      label.textContent = t;
      svg.appendChild(label);
    }

    // Connector line between desired and current
    const line = document.createElementNS(ns, "line");
    line.setAttribute("id", "align-connector");
    line.setAttribute("stroke", "rgba(255,195,94,0.4)");
    line.setAttribute("stroke-width", "1");
    line.setAttribute("stroke-dasharray", "2 2");
    svg.appendChild(line);

    // Target marker (desired pointing)
    const targetG = document.createElementNS(ns, "g");
    targetG.setAttribute("id", "align-target");
    targetG.classList.add("align-marker");
    targetG.style.opacity = "0";
    const targetRing = document.createElementNS(ns, "circle");
    targetRing.setAttribute("cx", "0");
    targetRing.setAttribute("cy", "0");
    targetRing.setAttribute("r", "7");
    targetRing.setAttribute("fill", "none");
    targetRing.setAttribute("stroke", "#ffc35e");
    targetRing.setAttribute("stroke-width", "1.5");
    targetRing.setAttribute("stroke-dasharray", "3 2");
    targetG.appendChild(targetRing);
    const targetDot = document.createElementNS(ns, "circle");
    targetDot.setAttribute("cx", "0");
    targetDot.setAttribute("cy", "0");
    targetDot.setAttribute("r", "1.5");
    targetDot.setAttribute("fill", "#ffc35e");
    targetG.appendChild(targetDot);
    svg.appendChild(targetG);

    // Current marker (actual boresight) — glow halo + solid dot
    const currentG = document.createElementNS(ns, "g");
    currentG.setAttribute("id", "align-current");
    currentG.classList.add("align-marker");
    currentG.style.opacity = "0";
    const glow = document.createElementNS(ns, "circle");
    glow.setAttribute("cx", "0");
    glow.setAttribute("cy", "0");
    glow.setAttribute("r", "13");
    glow.setAttribute("fill", "rgba(86,204,242,0.15)");
    currentG.appendChild(glow);
    const glow2 = document.createElementNS(ns, "circle");
    glow2.setAttribute("cx", "0");
    glow2.setAttribute("cy", "0");
    glow2.setAttribute("r", "8");
    glow2.setAttribute("fill", "rgba(86,204,242,0.22)");
    currentG.appendChild(glow2);
    const dot = document.createElementNS(ns, "circle");
    dot.setAttribute("cx", "0");
    dot.setAttribute("cy", "0");
    dot.setAttribute("r", "5");
    dot.setAttribute("fill", "#7ddcff");
    dot.setAttribute("stroke", "#56ccf2");
    dot.setAttribute("stroke-width", "1");
    currentG.appendChild(dot);
    svg.appendChild(currentG);
  };

  const azElToXY = (azDeg, elDeg) => {
    const el = Math.max(0, Math.min(90, elDeg));
    const r = ALIGN_RADIUS * (1 - el / 90);
    const rad = (azDeg * Math.PI) / 180;
    return {
      x: ALIGN_CENTER + r * Math.sin(rad),
      y: ALIGN_CENTER - r * Math.cos(rad),
    };
  };

  const renderAlignment = (statusDict) => {
    const svg = $("#align-svg");
    if (!alignmentBackgroundBuilt) {
      buildAlignmentBackground(svg);
      alignmentBackgroundBuilt = true;
    }

    const alignment = pick(statusDict, "alignment_stats", "alignmentStats");
    const cur = $("#align-current");
    const tgt = $("#align-target");
    const line = $("#align-connector");
    const pill = $("#align-pill");

    if (!alignment || typeof alignment !== "object") {
      cur.style.opacity = "0";
      tgt.style.opacity = "0";
      line.setAttribute("stroke-opacity", "0");
      pill.textContent = "—";
      pill.dataset.state = "unknown";
      renderKV($("#kv-alignment"), [[i18n.t("kv.alignment"), null]]);
      return;
    }

    const az = Number(alignment.boresight_azimuth_deg);
    const el = Number(alignment.boresight_elevation_deg);
    const dAz = Number(alignment.desired_boresight_azimuth_deg);
    const dEl = Number(alignment.desired_boresight_elevation_deg);
    const tilt = Number(alignment.tilt_angle_deg);
    const state = alignment.attitude_estimation_state || "";
    const unc = Number(alignment.attitude_uncertainty_deg);

    const hasCurrent = isFinite(az) && isFinite(el);
    const hasTarget = isFinite(dAz) && isFinite(dEl);

    if (hasCurrent) {
      const { x, y } = azElToXY(az, el);
      cur.style.opacity = "1";
      cur.style.transform = `translate(${x}px, ${y}px)`;
      line.setAttribute("x2", x);
      line.setAttribute("y2", y);
    } else {
      cur.style.opacity = "0";
    }

    if (hasTarget) {
      const { x, y } = azElToXY(dAz, dEl);
      tgt.style.opacity = "1";
      tgt.style.transform = `translate(${x}px, ${y}px)`;
      line.setAttribute("x1", x);
      line.setAttribute("y1", y);
    } else {
      tgt.style.opacity = "0";
    }

    line.setAttribute("stroke-opacity", hasCurrent && hasTarget ? "0.6" : "0");

    if (state === "FILTER_CONVERGED") {
      pill.textContent = i18n.t("align.converged");
      pill.dataset.state = "OKAY";
    } else if (state) {
      pill.textContent = state.replace(/^FILTER_/, "").replace(/_/g, " ");
      pill.dataset.state = "warn";
    } else {
      pill.textContent = "—";
      pill.dataset.state = "unknown";
    }

    const deltaAz = hasCurrent && hasTarget ? az - dAz : null;
    const deltaEl = hasCurrent && hasTarget ? el - dEl : null;
    const fmtDelta = (v) => (v >= 0 ? `+${nfix(v, 2)}°` : `${nfix(v, 2)}°`);
    const deltaClass = (v) => {
      if (v == null) return null;
      const a = Math.abs(v);
      if (a < 1) return "good";
      if (a > 5) return "bad";
      return "warn";
    };

    renderKV($("#kv-alignment"), [
      [i18n.t("kv.azimuth"), isFinite(az) ? `${nfix(az, 2)}°` : null],
      [i18n.t("kv.elevation"), isFinite(el) ? `${nfix(el, 2)}°` : null],
      [i18n.t("kv.tilt"), isFinite(tilt) ? `${nfix(tilt, 2)}°` : null],
      [i18n.t("kv.uncertainty"), isFinite(unc) ? `±${nfix(unc, 2)}°` : null],
      [i18n.t("kv.delta_azimuth"), deltaAz != null ? fmtDelta(deltaAz) : null, deltaClass(deltaAz)],
      [i18n.t("kv.delta_elevation"), deltaEl != null ? fmtDelta(deltaEl) : null, deltaClass(deltaEl)],
    ]);
  };

  let lastLocation = null;

  const renderLocation = (root) => {
    const loc = unwrap(root, "get_location", "getLocation");
    if (!loc || typeof loc !== "object" || !loc.lla) {
      lastLocation = null;
      $("#maps-link").classList.add("hidden");
      return;
    }
    lastLocation = loc;
    const { lat, lon, alt } = loc.lla;
    renderKV($("#kv-location"), [
      [i18n.t("kv.latitude"), lat != null ? `${nfix(lat, 6)}°` : null],
      [i18n.t("kv.longitude"), lon != null ? `${nfix(lon, 6)}°` : null],
      [i18n.t("kv.altitude"), alt != null ? `${nfix(alt, 1)} ${i18n.t("units.m")}` : null],
      [i18n.t("kv.source"), loc.source],
      [i18n.t("kv.accuracy_sigma"), loc.sigma_m != null ? `±${nfix(loc.sigma_m, 1)} ${i18n.t("units.m")}` : null],
      [i18n.t("kv.horizontal_speed"), loc.horizontal_speed_mps != null ? `${nfix(loc.horizontal_speed_mps, 2)} ${i18n.t("units.mps")}` : null],
      [i18n.t("kv.vertical_speed"), loc.vertical_speed_mps != null ? `${nfix(loc.vertical_speed_mps, 2)} ${i18n.t("units.mps")}` : null],
    ]);
    const link = $("#maps-link");
    if (lat != null && lon != null) {
      link.href = `https://maps.google.com/?q=${lat},${lon}`;
      link.classList.remove("hidden");
    } else {
      link.classList.add("hidden");
    }
  };

  // ───── Obstruction map SVG ─────

  const renderObstructionMap = (root) => {
    const om = unwrap(root, "dishGetObstructionMap", "dish_get_obstruction_map");
    const snr = (om && om.snr) || [];
    const rows = Number(pick(om, "num_rows", "numRows")) || 0;
    const cols = Number(pick(om, "num_cols", "numCols")) || 0;
    const svg = $("#obs-svg");
    svg.replaceChildren();
    $("#obs-grid-info").textContent = rows && cols ? `${cols}×${rows}` : "";
    if (!rows || !cols || !snr.length) return;

    const ns = "http://www.w3.org/2000/svg";
    svg.setAttribute("viewBox", `0 0 ${cols} ${rows}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");

    const frag = document.createDocumentFragment();
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const idx = r * cols + c;
        if (idx >= snr.length) continue;
        const v = snr[idx];
        const n = v == null ? -1 : Number(v);
        let color;
        if (!isFinite(n) || n < 0) color = "rgba(92,117,150,0.08)";
        else if (n > 2) color = "#5cdf8f";
        else if (n > 0.5) color = "#ffc35e";
        else color = "#ff6b6b";
        const rect = document.createElementNS(ns, "rect");
        rect.setAttribute("x", c);
        rect.setAttribute("y", r);
        rect.setAttribute("width", 1);
        rect.setAttribute("height", 1);
        rect.setAttribute("fill", color);
        frag.appendChild(rect);
      }
    }
    svg.appendChild(frag);
  };

  // ───── History tab (power, outages, events) ─────

  const SVG_NS = "http://www.w3.org/2000/svg";

  const renderSparkline = (samples) => {
    const svg = $("#power-spark");
    svg.replaceChildren();
    const nums = [];
    for (const v of samples || []) {
      const n = Number(v);
      if (isFinite(n)) nums.push(n);
    }
    if (nums.length < 2) return { min: null, max: null, avg: null };

    const min = Math.min(...nums);
    const max = Math.max(...nums);
    const range = max - min || 1;
    const W = samples.length;
    const H = 100;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);

    const pts = [];
    for (let i = 0; i < samples.length; i++) {
      const n = Number(samples[i]);
      if (!isFinite(n)) continue;
      const y = H - ((n - min) / range) * H;
      pts.push(`${i},${y.toFixed(1)}`);
    }
    const pointsStr = pts.join(" ");

    const defs = document.createElementNS(SVG_NS, "defs");
    const grad = document.createElementNS(SVG_NS, "linearGradient");
    grad.setAttribute("id", "power-grad");
    grad.setAttribute("x1", "0");
    grad.setAttribute("y1", "0");
    grad.setAttribute("x2", "0");
    grad.setAttribute("y2", "1");
    const s1 = document.createElementNS(SVG_NS, "stop");
    s1.setAttribute("offset", "0%");
    s1.setAttribute("stop-color", "#56ccf2");
    s1.setAttribute("stop-opacity", "0.55");
    const s2 = document.createElementNS(SVG_NS, "stop");
    s2.setAttribute("offset", "100%");
    s2.setAttribute("stop-color", "#56ccf2");
    s2.setAttribute("stop-opacity", "0");
    grad.appendChild(s1);
    grad.appendChild(s2);
    defs.appendChild(grad);
    svg.appendChild(defs);

    const area = document.createElementNS(SVG_NS, "polygon");
    area.setAttribute("points", `0,${H} ${pointsStr} ${W - 1},${H}`);
    area.setAttribute("fill", "url(#power-grad)");
    svg.appendChild(area);

    const line = document.createElementNS(SVG_NS, "polyline");
    line.setAttribute("points", pointsStr);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "#7ddcff");
    line.setAttribute("stroke-width", "1.4");
    line.setAttribute("vector-effect", "non-scaling-stroke");
    line.setAttribute("stroke-linejoin", "round");
    svg.appendChild(line);

    const avg = nums.reduce((a, b) => a + b, 0) / nums.length;
    return { min, max, avg };
  };

  const fmtDurationNs = (ns) => {
    const s = Number(ns) / 1e9;
    if (!isFinite(s) || s < 0) return "—";
    if (s < 1) return `${i18n.fmtNumber(s * 1000, { maximumFractionDigits: 0 })} ${i18n.t("units.ms")}`;
    if (s < 60) return `${nfix(s, 1)} ${i18n.t("units.s")}`;
    const m = Math.floor(s / 60);
    const sr = Math.round(s - m * 60);
    return sr ? `${m}m ${sr}s` : `${m}m`;
  };

  const fmtEventTime = (ns) => {
    const secs = Number(ns) / 1e9;
    if (!isFinite(secs) || secs < 1577836800) return "";
    const d = new Date(secs * 1000);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 0) return i18n.fmtTime(d);
    if (diff < 60) return i18n.t("time.just_now");
    if (diff < 3600) return i18n.t("time.m_ago", { n: Math.floor(diff / 60) });
    if (diff < 86400) return i18n.t("time.h_ago", { n: Math.floor(diff / 3600) });
    if (diff < 7 * 86400) return i18n.t("time.d_ago", { n: Math.floor(diff / 86400) });
    return i18n.fmtDate(d);
  };

  const prettify = (s, prefix) => {
    if (!s) return "";
    let t = String(s);
    if (prefix && t.startsWith(prefix)) t = t.slice(prefix.length);
    return t.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  };

  const severityClass = (s) => {
    const map = {
      EVENT_SEVERITY_ADVISORY: "advisory",
      EVENT_SEVERITY_CAUTION: "caution",
      EVENT_SEVERITY_WARNING: "warning",
      EVENT_SEVERITY_INFO: "info",
    };
    return map[s] || "advisory";
  };

  const renderOutages = (outages) => {
    const list = $("#outages-list");
    const count = $("#outages-count");
    list.replaceChildren();
    const arr = Array.isArray(outages) ? outages : [];
    count.textContent = arr.length ? i18n.tp("outages.count", arr.length) : "";
    if (!arr.length) {
      list.appendChild(el("div", { class: "empty", text: i18n.t("empty.outages") }));
      return;
    }
    const sorted = [...arr].sort(
      (a, b) => Number(b.start_timestamp_ns || 0) - Number(a.start_timestamp_ns || 0)
    );
    for (const o of sorted) {
      const cause = o.cause || "UNKNOWN";
      const row = el("div", { class: "event-row event-outage" }, [
        el("div", { class: "event-sev-label", text: cause.replace(/_/g, " ") }),
        el("div", { class: "event-reason", text: prettify(cause) }),
        el("div", { class: "event-meta" }, [
          el("span", { class: "event-duration", text: fmtDurationNs(o.duration_ns) }),
          el("span", { text: o.did_switch ? i18n.t("outages.sat_switch") : i18n.t("outages.no_switch") }),
        ]),
      ]);
      list.appendChild(row);
    }
  };

  const renderEvents = (events) => {
    const list = $("#events-list");
    const count = $("#events-count");
    list.replaceChildren();
    const arr = Array.isArray(events) ? events : [];
    count.textContent = arr.length ? i18n.tp("events.count", arr.length) : "";
    if (!arr.length) {
      list.appendChild(el("div", { class: "empty", text: i18n.t("empty.events") }));
      return;
    }
    const sorted = [...arr].sort(
      (a, b) => Number(b.start_timestamp_ns || 0) - Number(a.start_timestamp_ns || 0)
    );
    for (const e of sorted) {
      const sev = severityClass(e.severity);
      const row = el("div", { class: `event-row event-${sev}` }, [
        el("div", { class: "event-sev-label", text: (e.severity || "").replace(/^EVENT_SEVERITY_/, "") }),
        el("div", { class: "event-reason", text: prettify(e.reason, "EVENT_REASON_") }),
        el("div", { class: "event-meta" }, [
          el("span", { class: "event-duration", text: fmtDurationNs(e.duration_ns) }),
          el("span", { text: fmtEventTime(e.start_timestamp_ns) }),
        ]),
      ]);
      list.appendChild(row);
    }
  };

  const renderHistory = (root) => {
    const h = unwrap(root, "dish_get_history", "dishGetHistory", "getHistory");
    if (!h || typeof h !== "object") return;

    const power = h.power_in || h.powerIn || [];
    const { min, max, avg } = renderSparkline(power);
    const last = [...power].reverse().find((v) => isFinite(Number(v)));
    const lastN = last != null ? Number(last) : null;
    $("#power-current").textContent = lastN != null ? nfix(lastN, 1) : "—";
    $("#power-min").textContent = min != null ? `${nfix(min, 1)} ${i18n.t("units.w")}` : "—";
    $("#power-max").textContent = max != null ? `${nfix(max, 1)} ${i18n.t("units.w")}` : "—";
    $("#power-avg").textContent = avg != null ? `${nfix(avg, 1)} ${i18n.t("units.w")}` : "—";
    $("#power-window").textContent = power.length ? `${power.length}${i18n.t("units.s")}` : "—";

    const pill = $("#power-pill");
    if (lastN != null) {
      pill.textContent = i18n.t("pill.live");
      pill.dataset.state = "OKAY";
    } else {
      pill.textContent = i18n.t("pill.no_data");
      pill.dataset.state = "warn";
    }

    renderOutages(h.outages || []);
    const eventLog = h.event_log || h.eventLog || {};
    renderEvents(eventLog.events || []);
  };

  const refreshHistory = async () => {
    const r = await api.get("/api/history");
    if (r.ok && r.body.data) {
      renderHistory(r.body.data);
      $("#history-last-update").textContent = i18n.t("updated", { time: i18n.fmtTime(new Date()) });
    }
  };

  const isTabVisible = (id) => {
    const panel = document.getElementById(`tab-${id}`);
    return panel && !panel.classList.contains("hidden");
  };

  // ───── Fetch + render pipeline ─────

  const refreshStatusOnly = async () => {
    const r = await api.get("/api/status");
    if (r.ok && r.body.data) renderStatus(r.body.data);
    return r.ok;
  };

  const refreshHeavyTelemetry = async () => {
    const [locR, obsR, diagR] = await Promise.all([
      api.get("/api/location"),
      api.get("/api/obstruction"),
      api.get("/api/diagnostics"),
    ]);
    if (locR.ok && locR.body.data) renderLocation(locR.body.data);
    if (obsR.ok && obsR.body.data) renderObstructionMap(obsR.body.data);
    if (diagR.ok && diagR.body.data) {
      lastDiagnostics = unwrap(diagR.body.data, "dish_get_diagnostics", "dishGetDiagnostics");
    }
  };

  const refreshTelemetry = async () => {
    await Promise.all([refreshStatusOnly(), refreshHeavyTelemetry()]);
    $("#last-update").textContent = i18n.t("updated", { time: i18n.fmtTime(new Date()) });
  };

  // ───── Auto-refresh loop ─────

  let refreshTimer = null;
  let tickCounter = 0;
  const startAutoRefresh = () => {
    stopAutoRefresh();
    refreshTimer = setInterval(async () => {
      const s = await refreshState();
      tickCounter++;
      const dishOk = s && s.dish && s.dish.connected;
      const routerOk = s && s.router && s.router.connected;

      // Every tick (1 s): dish status — drives live alignment and throughput.
      if (dishOk && isTabVisible("telemetry")) {
        refreshStatusOnly();
        $("#last-update").textContent = i18n.t("updated", { time: i18n.fmtTime(new Date()) });
      }

      // Every SLOW_REFRESH_TICKS (5 s): location + obstruction map.
      if (dishOk && tickCounter % SLOW_REFRESH_TICKS === 0 && isTabVisible("telemetry")) {
        refreshHeavyTelemetry();
      }

      // Every HISTORY_REFRESH_TICKS (10 s): history data when that tab is open.
      if (dishOk && tickCounter % HISTORY_REFRESH_TICKS === 0 && isTabVisible("history")) {
        refreshHistory();
      }

      // Every SLOW_REFRESH_TICKS (5 s): router data when that tab is open.
      if (routerOk && tickCounter % SLOW_REFRESH_TICKS === 0 && isTabVisible("router")) {
        refreshRouter();
      }
    }, REFRESH_MS);
  };
  const stopAutoRefresh = () => {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = null;
  };

  const initConfig = () => {
    $("#btn-reload-config").addEventListener("click", loadConfig);
  };

  const SNOW_MELT = {
    AUTO: { key: "auto", state: "active" },
    ALWAYS_ON: { key: "on", state: "warm" },
    ALWAYS_OFF: { key: "off", state: "off" },
  };
  const LEVEL_DISH = {
    TILT_LIKE_NORMAL: { key: "tilt" },
    FORCE_LEVEL: { key: "force" },
  };
  const LOC_MODE = {
    LOCAL: { key: "local" },
    REMOTE: { key: "remote" },
  };

  const fmtHHMM = (minutes) => {
    const n = Number(minutes);
    if (!isFinite(n)) return "—";
    const mod = ((n % 1440) + 1440) % 1440;
    const h = Math.floor(mod / 60) % 24;
    const m = Math.floor(mod) % 60;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  };

  const fmtTimeOfDay = (utcMinutes) => {
    const n = Number(utcMinutes);
    if (!isFinite(n)) return "—";
    return fmtHHMM(n + Math.round(utcOffsetS / 60));
  };

  const fmtDurationMin = (m) => {
    const n = Number(m);
    if (!isFinite(n) || n <= 0) return "—";
    const h = Math.floor(n / 60);
    const mm = n % 60;
    if (h && mm) return i18n.t("units.h_min", { h, m: mm });
    if (h) return i18n.t("units.h", { h });
    return i18n.t("units.min", { m: mm });
  };

  const renderPowerSaveTimeline = (startMin, durMin, mode) => {
    const pill = $("#cfg-ps-pill");
    const winA = $("#cfg-ps-window");
    const winB = $("#cfg-ps-window-wrap");
    const nowLine = $("#cfg-ps-now");

    const offMin = Math.round(utcOffsetS / 60);
    const now = new Date();
    const nowUtcMin = now.getUTCHours() * 60 + now.getUTCMinutes();
    const nowLocalMin = ((nowUtcMin + offMin) % 1440 + 1440) % 1440;
    nowLine.style.left = `${(nowLocalMin / 1440) * 100}%`;
    nowLine.title = i18n.t("config.ps.now_title", { time: fmtHHMM(nowLocalMin) });

    const s = Number(startMin);
    const d = Number(durMin);
    const enabled = mode === true && isFinite(s) && isFinite(d) && d > 0;

    if (enabled) {
      pill.textContent = i18n.t("config.ps.active");
      pill.dataset.state = "OKAY";
      const sLocal = ((s + offMin) % 1440 + 1440) % 1440;
      const firstLen = Math.min(d, 1440 - sLocal);
      winA.classList.remove("hidden");
      winA.style.left = `${(sLocal / 1440) * 100}%`;
      winA.style.width = `${(firstLen / 1440) * 100}%`;

      if (sLocal + d > 1440) {
        const wrapLen = sLocal + d - 1440;
        winB.classList.remove("hidden");
        winB.style.left = "0%";
        winB.style.width = `${(wrapLen / 1440) * 100}%`;
      } else {
        winB.classList.add("hidden");
      }

      $("#cfg-ps-start").textContent = fmtHHMM(sLocal);
      $("#cfg-ps-end").textContent = fmtHHMM(sLocal + d);
      $("#cfg-ps-duration").textContent = fmtDurationMin(d);
      $("#cfg-ps-state").textContent = i18n.t("config.ps.enabled_label");
    } else {
      pill.textContent = mode === false ? i18n.t("config.ps.disabled") : i18n.t("config.ps.not_set");
      pill.dataset.state = "warn";
      winA.classList.add("hidden");
      winB.classList.add("hidden");
      $("#cfg-ps-start").textContent = "—";
      $("#cfg-ps-end").textContent = "—";
      $("#cfg-ps-duration").textContent = "—";
      $("#cfg-ps-state").textContent = mode === false ? i18n.t("config.ps.disabled_label") : i18n.t("config.ps.not_configured");
    }
  };

  const renderConfig = (root) => {
    const outer = unwrap(root, "dishGetConfig", "dish_get_config");
    const cfg = pick(outer, "dish_config", "dishConfig") || outer || {};

    // Snow melt
    const snow = pick(cfg, "snow_melt_mode", "snowMeltMode");
    const snowDef = (snow && SNOW_MELT[snow]) || null;
    const snowCard = $("#cfg-snowmelt");
    $("#cfg-snow-value").textContent = snowDef ? i18n.t(`snow.${snowDef.key}.label`) : (snow || i18n.t("config.snow_not_set"));
    $("#cfg-snow-desc").textContent = snowDef ? i18n.t(`snow.${snowDef.key}.desc`) : i18n.t("config.snow_unknown_desc");
    snowCard.dataset.state = snowDef ? snowDef.state : "";
    const snowPill = $("#cfg-snow-pill");
    snowPill.textContent = snowDef ? i18n.t(`snow.${snowDef.key}.pill`) : "—";
    snowPill.dataset.state = snowDef ? (snowDef.state === "off" ? "warn" : "OKAY") : "unknown";

    // Level
    const level = pick(cfg, "level_dish_mode", "levelDishMode");
    const levelDef = (level && LEVEL_DISH[level]) || null;
    const levelCard = $("#cfg-level");
    $("#cfg-level-value").textContent = levelDef ? i18n.t(`level.${levelDef.key}.label`) : i18n.t("config.level_default_label");
    $("#cfg-level-desc").textContent = levelDef ? i18n.t(`level.${levelDef.key}.desc`) : i18n.t("config.level_default_desc");
    levelCard.dataset.state = levelDef ? "active" : "off";

    // Power save
    const psStart = pick(cfg, "power_save_start_minutes", "powerSaveStartMinutes");
    const psDur = pick(cfg, "power_save_duration_minutes", "powerSaveDurationMinutes");
    const psMode = pick(cfg, "power_save_mode", "powerSaveMode");
    renderPowerSaveTimeline(psStart, psDur, psMode);

    // Software updates
    const rebootHour = pick(cfg, "swupdate_reboot_hour", "swupdateRebootHour");
    const swuCard = $("#cfg-swu");
    $("#cfg-swu-hour").textContent = rebootHour != null ? i18n.t("config.swu.hour_utc", { h: String(rebootHour).padStart(2, "0") }) : "—";
    swuCard.dataset.state = rebootHour != null ? "active" : "off";
    const deferral = pick(cfg, "swupdate_three_day_deferral_enabled", "swupdateThreeDayDeferralEnabled");
    const chips = $("#cfg-swu-chips");
    chips.replaceChildren();
    chips.appendChild(
      el("span", { class: "chip", "data-on": String(Boolean(deferral)), text: i18n.t("config.swu.three_day") })
    );

    // Location reporting
    const locReq = pick(cfg, "location_request_mode", "locationRequestMode");
    const locDef = (locReq && LOC_MODE[locReq]) || null;
    const locCard = $("#cfg-loc");
    $("#cfg-loc-value").textContent = locDef ? i18n.t(`loc.${locDef.key}.label`) : (locReq || "—");
    $("#cfg-loc-desc").textContent = locDef ? i18n.t(`loc.${locDef.key}.desc`) : i18n.t("config.loc_unknown_desc");
    locCard.dataset.state = locReq === "LOCAL" ? "active" : locReq ? "warm" : "off";

    // Asset class
    const assetClass = pick(cfg, "asset_class", "assetClass");
    const assetCard = $("#cfg-asset");
    $("#cfg-asset-value").textContent = assetClass != null ? i18n.t("config.asset_class", { n: assetClass }) : "—";
    assetCard.dataset.state = assetClass != null ? "active" : "off";

    // Raw
    $("#raw-config").textContent = JSON.stringify(cfg, null, 2);
  };

  const loadConfig = async () => {
    const r = await api.get("/api/config");
    if (r.ok) {
      renderConfig(r.body.data);
    } else {
      $("#cfg-snow-value").textContent = i18n.t("config.failed_load");
      $("#cfg-snow-desc").textContent = r.body.error || i18n.t("config.unknown_error");
      $("#raw-config").textContent = "";
    }
  };

  // ───── Actions ─────

  const initActions = () => {
    $("#btn-reboot").addEventListener("click", async () => {
      const ok = await confirmAction(
        i18n.t("confirm.reboot.title"),
        i18n.t("confirm.reboot.msg"),
        i18n.t("confirm.reboot.ok")
      );
      if (!ok) return;
      const r = await api.post("/api/reboot", {});
      if (r.ok) toast("success", i18n.t("toast.reboot_sent"));
      else toast("error", i18n.t("toast.reboot_failed"), r.body.error || "");
    });

    $("#btn-ping").addEventListener("click", async () => {
      const out = $("#ping-output");
      out.textContent = i18n.t("toast.ping_running");
      const r = await api.get("/api/ping");
      if (r.ok) out.textContent = r.body.output || r.body.stderr || i18n.t("toast.ping_no_output");
      else out.textContent = r.body.error || i18n.t("toast.ping_failed");
    });

    $("#btn-export").addEventListener("click", async () => {
      const paths = [
        ["device", "/api/device"],
        ["status", "/api/status"],
        ["location", "/api/location"],
        ["config", "/api/config"],
        ["obstruction", "/api/obstruction"],
        ["diagnostics", "/api/diagnostics"],
        ["history", "/api/history"],
      ];
      const dump = {};
      for (const [label, path] of paths) {
        const r = await api.get(path);
        dump[label] = r.ok ? r.body.data : { error: r.body.error || `HTTP ${r.status}` };
      }
      const blob = new Blob([JSON.stringify(dump, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const a = el("a", { href: url, download: `starlink_dump_${ts}.json` });
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast("success", i18n.t("toast.export_downloaded"));
    });
  };

  // ───── Advanced ─────

  const initAdvanced = () => {
    $("#btn-load-services").addEventListener("click", async () => {
      const r = await api.get("/api/services");
      const box = $("#services-list");
      box.replaceChildren();
      if (!r.ok) {
        box.appendChild(el("div", { class: "muted", text: r.body.error || i18n.t("toast.failed") }));
        return;
      }
      for (const [svc, methods] of Object.entries(r.body.data || {})) {
        const group = el("div", { class: "tree-service" }, [
          el("div", { class: "tree-service-name", text: svc }),
          ...(methods || []).map((m) => el("div", { class: "tree-method", text: `→ ${m}` })),
        ]);
        box.appendChild(group);
      }
    });

    $("#btn-load-fields").addEventListener("click", async () => {
      const r = await api.get("/api/fields");
      const box = $("#fields-list");
      box.replaceChildren();
      if (!r.ok) {
        box.appendChild(el("div", { class: "muted", text: r.body.error || i18n.t("toast.failed") }));
        return;
      }
      for (const f of r.body.data || []) {
        const shortType = String(f.type).split(".").pop();
        box.appendChild(
          el("div", { class: "tree-field" }, [
            el("span", { class: "tree-field-name", text: f.name }),
            el("span", { class: "tree-field-type", text: shortType }),
          ])
        );
      }
    });

    $("#btn-load-diag").addEventListener("click", async () => {
      const r = await api.get("/api/diagnostics");
      $("#raw-diag").textContent = r.ok
        ? JSON.stringify(r.body.data, null, 2)
        : r.body.error || i18n.t("toast.failed");
    });

    $("#form-raw").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.currentTarget);
      const key = fd.get("key").trim();
      let payload = {};
      const raw = fd.get("payload").trim();
      if (raw) {
        try {
          payload = JSON.parse(raw);
        } catch (err) {
          toast("error", i18n.t("toast.invalid_json"), err.message);
          return;
        }
      }
      const r = await api.post("/api/raw", { key, payload });
      const out = $("#raw-output");
      if (r.ok) out.textContent = JSON.stringify(r.body.data, null, 2);
      else out.textContent = r.body.error || `HTTP ${r.status}`;
    });
  };

  // ───── Header actions ─────

  const initHeader = () => {
    $("#btn-reconnect").addEventListener("click", async () => {
      const dot = $("#conn-dot");
      if (dot) dot.dataset.state = "unknown";
      const r = await api.post("/api/reconnect", {});
      if (r.ok && r.body.connected) {
        toast("success", i18n.t("toast.dish_reconnected"));
        refreshTelemetry();
      } else {
        toast("error", i18n.t("toast.dish_reconnect_failed"), (r.body && r.body.message) || "");
      }
      refreshState();
    });

    $("#btn-router-reconnect").addEventListener("click", async () => {
      const dot = $("#router-conn-dot");
      if (dot) dot.dataset.state = "unknown";
      wifiSecretsCache = null;
      const r = await api.post("/api/router/reconnect", {});
      if (r.ok && r.body.connected) {
        toast("success", i18n.t("toast.router_reconnected"));
        refreshRouter();
      } else {
        toast("error", i18n.t("toast.router_reconnect_failed"), (r.body && r.body.message) || "");
      }
      refreshState();
    });

    $("#btn-refresh").addEventListener("click", refreshTelemetry);
    $("#btn-history-refresh").addEventListener("click", refreshHistory);
    $("#btn-router-refresh").addEventListener("click", refreshRouter);

    $("#auto-refresh").addEventListener("change", (e) => {
      if (e.target.checked) startAutoRefresh();
      else stopAutoRefresh();
    });
  };

  // ───── Boot ─────

  // ───── Vault gate (master password) ─────

  const vaultModal = () => $("#vault-modal");
  const vaultForm = () => $("#vault-form");

  const setVaultMode = (mode) => {
    // mode: "setup" or "unlock"
    const titleEl = $("#vault-title");
    const descEl = $("#vault-desc");
    const submit = $("#vault-submit");
    const confirmField = $("#vault-confirm-field");
    const resetLink = $("#vault-reset-link");
    const errEl = $("#vault-error");
    errEl.classList.add("hidden");
    errEl.textContent = "";
    $("#vault-password").value = "";
    $("#vault-confirm").value = "";
    if (mode === "setup") {
      titleEl.textContent = i18n.t("vault.setup_title");
      descEl.textContent = i18n.t("vault.setup_desc");
      submit.textContent = i18n.t("vault.setup_btn");
      confirmField.classList.remove("hidden");
      resetLink.classList.add("hidden");
    } else {
      titleEl.textContent = i18n.t("vault.unlock_title");
      descEl.textContent = i18n.t("vault.unlock_desc");
      submit.textContent = i18n.t("vault.unlock_btn");
      confirmField.classList.add("hidden");
      resetLink.classList.remove("hidden");
    }
  };

  const showVaultError = (msg) => {
    const e = $("#vault-error");
    e.textContent = msg;
    e.classList.remove("hidden");
  };

  const vaultGate = async () => {
    const statusR = await api.get("/api/vault/status");
    if (!statusR.ok) {
      // Server unreachable — retry loop?
      return new Promise((resolve) => setTimeout(() => vaultGate().then(resolve), 2000));
    }
    if (statusR.body.unlocked) {
      $("#btn-lock-vault").classList.remove("hidden");
      return true;
    }
    const modal = vaultModal();
    const form = vaultForm();
    document.body.inert = true;
    modal.showModal();

    let mode = statusR.body.initialized ? "unlock" : "setup";
    setVaultMode(mode);
    setTimeout(() => $("#vault-password").focus(), 50);

    return new Promise((resolve) => {
      const onSubmit = async (e) => {
        e.preventDefault();
        const password = $("#vault-password").value || "";
        if (mode === "setup") {
          if (password.length < 8) { showVaultError(i18n.t("vault.min_length")); return; }
          if (password !== ($("#vault-confirm").value || "")) {
            showVaultError(i18n.t("vault.mismatch")); return;
          }
          const r = await api.post("/api/vault/init", { password });
          if (!r.ok) {
            showVaultError(r.body.error || i18n.t("vault.init_failed"));
            return;
          }
          closeGate();
          return;
        }
        // unlock
        const r = await api.post("/api/vault/unlock", { password });
        if (!r.ok) {
          showVaultError(i18n.t("vault.wrong"));
          $("#vault-password").select();
          return;
        }
        closeGate();
      };

      const onReset = async () => {
        const ok = await confirmAction(
          i18n.t("vault.reset_title"),
          i18n.t("vault.reset_msg"),
          i18n.t("vault.reset_ok")
        );
        if (!ok) return;
        const r = await api.post("/api/vault/reset", {});
        if (!r.ok) {
          showVaultError(r.body.error || i18n.t("vault.init_failed"));
          return;
        }
        mode = "setup";
        setVaultMode(mode);
        toast("success", i18n.t("vault.reset_done"));
        setTimeout(() => $("#vault-password").focus(), 50);
      };

      const closeGate = () => {
        form.removeEventListener("submit", onSubmit);
        $("#vault-reset-link").removeEventListener("click", onReset);
        modal.close();
        document.body.inert = false;
        $("#btn-lock-vault").classList.remove("hidden");
        resolve(true);
      };

      form.addEventListener("submit", onSubmit);
      $("#vault-reset-link").addEventListener("click", onReset);
    });
  };

  const migrateLegacyWifi = async () => {
    let keys = [];
    try {
      keys = Object.keys(localStorage).filter((k) => k.startsWith("starlink.wifi."));
    } catch { return; }
    if (!keys.length) return;
    let migrated = 0;
    for (const k of keys) {
      const ssid = k.slice("starlink.wifi.".length);
      const psk = localStorage.getItem(k);
      if (!psk) { localStorage.removeItem(k); continue; }
      const ok = await vaultSetPassword(ssid, psk, "WPA");
      if (ok) {
        localStorage.removeItem(k);
        migrated++;
      }
    }
    if (migrated) {
      toast("success", i18n.tp("vault.migrated", migrated));
    }
  };

  const initLockButton = () => {
    const btn = $("#btn-lock-vault");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      await api.post("/api/vault/lock", {});
      toast("success", i18n.t("vault.locked_toast"));
      setTimeout(() => location.reload(), 300);
    });
  };

  const initLangSwitcher = () => {
    const sel = $("#lang-select");
    if (!sel) return;
    sel.value = i18n.lang;
    sel.addEventListener("change", () => i18n.setLang(sel.value));
    i18n.onChange(() => {
      const state = currentState;
      if (state && state.dish && state.dish.connected) {
        refreshTelemetry();
        loadConfig();
      }
      if (state && state.router && state.router.connected) {
        refreshRouter();
      }
      if (isTabVisible("history")) refreshHistory();
    });
  };

  let currentState = null;

  document.addEventListener("DOMContentLoaded", async () => {
    await vaultGate();
    initTabs();
    initHeader();
    initConfig();
    initActions();
    initAdvanced();
    initLangSwitcher();
    initLockButton();

    // Populate the vault SSID cache once; fire migration in the background.
    loadVaultSsids(true).then(() => migrateLegacyWifi());

    const state = await refreshState();
    currentState = state;
    if (state && state.dish && state.dish.connected) {
      await refreshTelemetry();
      loadConfig();
    }
    if (state && state.router && state.router.connected) {
      refreshRouter();
    }
    startAutoRefresh();
  });
})();
