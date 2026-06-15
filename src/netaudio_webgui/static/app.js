const POLL_MS = 3000;
let pollTimer = null;
let lastState = null;            // last rendered state (for instant re-render)
let lastGoodTime = null;         // timestamp of the last poll that returned devices
const pending = new Map();       // cellKey -> "add" | "remove" (optimistic, awaiting confirm)
let mutationChain = Promise.resolve();  // serialize mutations (each triggers a daemon restart)
let filterQuery = "";            // live search filter (lower-cased), re-applied after every render
const openSections = new Set();  // keys of expanded <details> (device + section), kept across re-renders
let lastDevicesJson = null;      // signature of last-rendered device data — skip needless panel rebuilds
let viewMode = "matrix";          // "matrix" | "dashboard"
let zonesConfig = { master: { buttons: [], off: false }, zones: [] };

function headers(extra) {
  return Object.assign({}, extra || {});
}

async function api(method, path, body) {
  const opts = { method, headers: headers(body ? {"Content-Type": "application/json"} : {}) };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(path, opts);
  if (resp.status === 401) {
    showLogin();
    throw new Error("nicht angemeldet");
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return resp.status === 200 ? resp.json() : null;
}

function toast(message, kind) {
  const el = document.createElement("div");
  el.className = "toast " + (kind || "");
  el.textContent = message;
  document.getElementById("toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 6000 : 3000);
}

function subKey(rxDev, rxLabel, txDev, txLabel) {
  // NUL separator: cannot appear in Dante device names / channel labels, so the
  // four fields can't run together and collide (e.g. "AB"+"CD" vs "A"+"BCD").
  return [rxDev, rxLabel, txDev, txLabel].join("\u0000");
}

// ---- clock panel (derived from state) ------------------------------------
// Leader chip + a compact follower count, both derived from state.devices by
// clock_role. No leader -> a subtle warning chip. Re-rendered each refresh.
function buildClock(state) {
  const wrap = document.getElementById("clock");
  wrap.innerHTML = "";
  const followers = state.devices
    .filter(d => (d.clock_role || "").toLowerCase() === "follower")
    .map(d => d.name);

  const leader = document.createElement("span");
  if (state.leader) {
    leader.className = "leader";
    leader.textContent = `Clock-Leader: ${state.leader}`;
  } else {
    leader.className = "leader none";
    leader.textContent = "kein Leader";
    leader.title = "Kein Gerät meldet die Clock-Leader-Rolle";
  }
  wrap.appendChild(leader);

  if (followers.length) {
    const chip = document.createElement("span");
    chip.className = "follower-chip";
    chip.textContent = `Follower: ${followers.length}`;
    chip.title = followers.join("\n");  // full list on hover
    wrap.appendChild(chip);
  }
}

function buildMatrix(state) {
  // Flatten TX (columns) and RX (rows) across all devices.
  const txCols = [];
  for (const d of state.devices)
    for (const ch of d.tx_channels)
      txCols.push({ device: d.name, number: ch.number, label: ch.label });
  const rxRows = [];
  for (const d of state.devices)
    for (const ch of d.rx_channels)
      rxRows.push({ device: d.name, ipv4: d.ipv4, number: ch.number, label: ch.label });

  const subs = new Map();
  for (const s of state.subscriptions)
    subs.set(subKey(s.rx_device, s.rx_channel, s.tx_device, s.tx_channel), s.state);

  // device name -> online flag, so offline devices can be dimmed in the matrix.
  const online = new Map();
  for (const d of state.devices) online.set(d.name, d.online !== false);

  const table = document.getElementById("matrix");
  table.innerHTML = "";

  // Row 1: TX device names spanning their channels.
  const devRow = document.createElement("tr");
  devRow.appendChild(th("corner", ""));
  devRow.appendChild(th("corner", ""));
  let i = 0;
  while (i < txCols.length) {
    let span = 1;
    while (i + span < txCols.length && txCols[i + span].device === txCols[i].device) span++;
    const cell = th("tx-dev", txCols[i].device);
    cell.colSpan = span;
    cell.title = txCols[i].device;  // full name when the header clips it
    cell.classList.toggle("offline", !online.get(txCols[i].device));
    cell.dataset.name = txCols[i].device;
    cell.dataset.colStart = i;      // span covers columns [colStart, colStart+span)
    cell.dataset.colEnd = i + span;
    devRow.appendChild(cell);
    i += span;
  }
  table.appendChild(devRow);

  // Row 2: TX channel labels.
  const chRow = document.createElement("tr");
  chRow.appendChild(th("corner", ""));
  chRow.appendChild(th("corner", ""));
  txCols.forEach((c, idx) => {
    const cell = th("tx-ch", c.label);
    cell.dataset.col = idx;
    cell.dataset.name = c.device;
    cell.dataset.label = c.label;
    cell.classList.toggle("offline", !online.get(c.device));
    chRow.appendChild(cell);
  });
  table.appendChild(chRow);

  // Data rows.
  for (const r of rxRows) {
    const tr = document.createElement("tr");
    const rxOffline = !online.get(r.device);
    const rxDev = th("rx-dev", r.device);
    rxDev.title = r.device;  // full name when the header clips it
    rxDev.classList.toggle("offline", rxOffline);
    rxDev.dataset.name = r.device;
    // Per-device clear-all affordance (disconnect every subscribed RX channel).
    const clear = document.createElement("span");
    clear.className = "rx-clear";
    clear.textContent = "✕";
    clear.title = `Alle Abos von ${r.device} trennen`;
    clear.onclick = (e) => { e.stopPropagation(); disconnectDevice(r.device); };
    rxDev.appendChild(clear);
    tr.appendChild(rxDev);
    const rxCh = th("rx-ch", r.label);
    rxCh.dataset.name = r.device;
    rxCh.dataset.label = r.label;
    rxCh.classList.toggle("offline", rxOffline);
    tr.appendChild(rxCh);
    txCols.forEach((c, idx) => {
      const td = document.createElement("td");
      td.className = "cell";
      // Dim cells whose RX row or TX column device is offline.
      if (rxOffline || !online.get(c.device)) td.classList.add("offline");
      td.dataset.col = idx;
      td.dataset.txName = c.device;
      td.dataset.txLabel = c.label;
      td.dataset.rxName = r.device;
      td.dataset.rxLabel = r.label;
      const key = subKey(r.device, r.label, c.device, c.label);
      const state = subs.get(key);
      const opt = pending.get(key);
      if (opt) {
        // Optimistic: show the intended result immediately, marked as settling.
        if (opt === "add") td.classList.add("on");
        td.classList.add("pending");
      } else if (state === "connected") td.classList.add("on");
      else if (state === "error" || state === "unresolved") td.classList.add("err");
      else if (state && state !== "none") td.classList.add("warn");
      td.title = `${c.label}@${c.device} → ${r.label}@${r.device}`;
      td.onclick = () => onCellClick(r, c, !!state);
      tr.appendChild(td);
    });
    table.appendChild(tr);
  }
  applyFilter();
}

function th(cls, text) {
  const el = document.createElement("th");
  el.className = cls;
  el.textContent = text;
  return el;
}

function onCellClick(rx, tx, isSubscribed) {
  const key = subKey(rx.device, rx.label, tx.device, tx.label);
  // Optimistic: reflect the intended change instantly, then reconcile in the
  // background (each mutation restarts the daemon, which takes a few seconds).
  pending.set(key, isSubscribed ? "remove" : "add");
  if (lastState) buildMatrix(lastState);
  mutationChain = mutationChain.then(async () => {
    try {
      if (isSubscribed) {
        await api("DELETE", "/api/subscription", { rx_device: rx.device, rx_number: rx.number });
        toast(`getrennt: ${rx.label}@${rx.device}`, "ok");
      } else {
        await api("POST", "/api/subscription", {
          tx_device: tx.device, tx_number: tx.number, rx_device: rx.device, rx_number: rx.number });
        toast(`verbunden: ${tx.label}@${tx.device} → ${rx.label}@${rx.device}`, "ok");
      }
    } catch (e) {
      toast(e.message, "error");
    } finally {
      pending.delete(key);
      await refresh();
    }
  });
}

function buildDevices(state) {
  const aside = document.getElementById("devices");
  // Skip the full rebuild when the device data is unchanged (polls fire every
  // few seconds): rebuilding would collapse open <details> and drop focus from
  // a control the user is editing. Only re-render when the data actually changes.
  const sig = JSON.stringify(state.devices);
  if (sig === lastDevicesJson && aside.childElementCount) { applyFilter(); return; }
  lastDevicesJson = sig;
  aside.innerHTML = "";
  for (const d of state.devices) {
    const div = document.createElement("div");
    div.className = "device";
    if (d.online === false) div.classList.add("offline");
    div.dataset.name = d.name;
    // All channel labels, joined, so the filter can match a device by any of them.
    div.dataset.labels = [...d.tx_channels, ...d.rx_channels].map(c => c.label).join(" ");
    const roleClass = d.clock_role.toLowerCase() === "leader" ? "role-leader" : "";
    const offlineTag = d.online === false ? ` <span class="meta offline-tag">offline</span>` : "";
    div.innerHTML =
      `<h3>${escapeHtml(d.name)}${offlineTag} <span class="meta ${roleClass}">${escapeHtml(d.clock_role)}</span></h3>` +
      `<div class="meta">${escapeHtml(d.ipv4)} · ${escapeHtml(d.model)} · ${escapeHtml(d.sample_rate || "?")} Hz` +
      ` · ${d.tx_channels.length} TX / ${d.rx_channels.length} RX</div>`;
    const actions = document.createElement("div");
    actions.className = "actions";
    actions.appendChild(button("Umbenennen", () => renameDevice(d)));
    actions.appendChild(button("⇄ Von Gerät…", () => bulkRouteInto(d)));
    actions.appendChild(button("Identify", () => doAction(`/api/device/${encodeURIComponent(d.ipv4)}/identify`, `Identify: ${d.name}`)));
    const reboot = button("Reboot", () => {
      if (confirm(`${d.name} wirklich neu starten?`))
        doAction(`/api/device/${encodeURIComponent(d.ipv4)}/reboot`, `Reboot: ${d.name}`);
    });
    reboot.className = "danger";
    actions.appendChild(reboot);
    div.appendChild(actions);
    div.appendChild(buildDetails(d));
    div.appendChild(buildConfig(d));
    const channels = document.createElement("div");
    channels.className = "channels";
    for (const [kind, list, type] of [["TX", d.tx_channels, "tx"], ["RX", d.rx_channels, "rx"]]) {
      if (!list.length) continue;
      const row = document.createElement("div");
      row.className = "chrow";
      const lbl = document.createElement("span");
      lbl.className = "meta";
      lbl.textContent = kind + ":";
      row.appendChild(lbl);
      for (const ch of list) {
        const chip = button(ch.label, () => renameChannel(d, ch, type));
        chip.className = "chip";
        // Left-click renames; right-click sets the channel gain (1–5).
        chip.title = `Kanal ${ch.number} umbenennen (Rechtsklick: Gain)`;
        chip.oncontextmenu = (e) => { e.preventDefault(); setChannelGain(d, ch, type); };
        row.appendChild(chip);
      }
      channels.appendChild(row);
    }
    div.appendChild(channels);
    aside.appendChild(div);
  }
  applyFilter();
}

// Keep a <details> section's open/closed state across full re-renders of the
// device panel (it rebuilds via innerHTML), keyed by device name + section.
function persistDetails(details, key) {
  details.open = openSections.has(key);
  details.addEventListener("toggle", () => {
    if (details.open) openSections.add(key); else openSections.delete(key);
  });
}

// ---- per-device detail view (collapsible) --------------------------------
// Read-only key/value list of every field carried in the state. Null/undefined
// values render as "—". Built with createElement for safe escaping.
function buildDetails(d) {
  const details = document.createElement("details");
  details.className = "config details";
  persistDetails(details, d.name + " details");
  const summary = document.createElement("summary");
  summary.textContent = "ℹ Details";
  details.appendChild(summary);
  const grid = document.createElement("div");
  grid.className = "detail-grid";

  const onOff = (v) => v == null ? null : (v ? "an" : "aus");
  const rows = [
    ["Server-Name", d.server_name],
    ["IP", d.ipv4],
    ["Modell", d.model],
    ["Sample-Rate", d.sample_rate != null ? d.sample_rate + " Hz" : null],
    ["Encoding", d.encoding != null ? d.encoding + " bit" : null],
    ["Latenz", d.latency != null ? d.latency + " ms" : null],
    ["AES67", onOff(d.aes67)],
    ["Preferred-Leader", onOff(d.preferred_leader)],
    ["Clock-Rolle", d.clock_role],
    ["Status", d.online === false ? "offline" : "online"],
    ["TX-Kanäle", d.tx_channels.length],
    ["RX-Kanäle", d.rx_channels.length],
  ];
  for (const [label, value] of rows) grid.appendChild(detailRow(label, value));

  details.appendChild(grid);
  return details;
}

function detailRow(label, value) {
  const row = document.createElement("div");
  row.className = "detail-row";
  const key = document.createElement("span");
  key.className = "meta";
  key.textContent = label;
  const val = document.createElement("span");
  val.className = "detail-val";
  val.textContent = (value === null || value === undefined || value === "") ? "—" : String(value);
  row.appendChild(key);
  row.appendChild(val);
  return row;
}

// ---- per-device configuration (collapsible) ------------------------------
function buildConfig(d) {
  const details = document.createElement("details");
  details.className = "config";
  persistDetails(details, d.name + " config");
  const summary = document.createElement("summary");
  summary.textContent = "⚙ Konfiguration";
  details.appendChild(summary);
  const grid = document.createElement("div");
  grid.className = "config-grid";

  grid.appendChild(configSelect(d, "Sample-Rate", "sample-rate",
    [44100, 48000, 88200, 96000, 176400, 192000].map(r => [r, r + " Hz"]), d.sample_rate));
  grid.appendChild(configSelect(d, "Encoding", "encoding",
    [16, 24, 32].map(b => [b, b + " bit"]), d.encoding));
  grid.appendChild(configLatency(d));
  grid.appendChild(configToggle(d, "AES67", "aes67", d.aes67));
  grid.appendChild(configToggle(d, "Preferred-Leader", "preferred-leader", d.preferred_leader));

  details.appendChild(grid);
  return details;
}

// One labelled <select> row. options: [[value, text], …]. selected preselects.
function configSelect(d, label, key, options, selected) {
  const wrap = configRow(label);
  const sel = document.createElement("select");
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "—";
  sel.appendChild(blank);
  for (const [value, text] of options) {
    const opt = document.createElement("option");
    opt.value = String(value);
    opt.textContent = text;
    if (selected != null && String(selected) === String(value)) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.onchange = () => { if (sel.value !== "") sendConfig(d, key, Number(sel.value)); };
  wrap.appendChild(sel);
  return wrap;
}

function configLatency(d) {
  const wrap = configRow("Latenz (ms)");
  const input = document.createElement("input");
  input.type = "number";
  input.min = "0";
  input.step = "0.1";
  if (d.latency != null) input.value = d.latency;
  input.onchange = () => {
    const v = parseFloat(input.value);
    if (!isNaN(v) && v > 0) sendConfig(d, "latency", v);
  };
  wrap.appendChild(input);
  return wrap;
}

function configToggle(d, label, key, checked) {
  const wrap = configRow(label);
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = !!checked;
  box.onchange = () => sendConfig(d, key, box.checked);
  wrap.appendChild(box);
  return wrap;
}

function configRow(labelText) {
  const wrap = document.createElement("label");
  wrap.className = "config-row";
  const lbl = document.createElement("span");
  lbl.className = "meta";
  lbl.textContent = labelText;
  wrap.appendChild(lbl);
  return wrap;
}

async function sendConfig(d, key, value) {
  try {
    await api("PUT", `/api/device/${encodeURIComponent(d.ipv4)}/config/${key}`, { value });
    toast(`${d.name}: ${key} = ${value}`, "ok");
  } catch (e) { toast(e.message, "error"); }
  refresh();
}

async function setChannelGain(d, ch, type) {
  const current = ch.gain != null ? ch.gain : "";
  const answer = prompt(
    `Gain für Kanal ${ch.number} (${type.toUpperCase()}, ${ch.label}) auf ${d.name} — Stufe 1–5:`,
    current);
  if (answer === null) return;
  const level = parseInt(answer, 10);
  if (isNaN(level) || level < 1 || level > 5) { toast("Gain muss 1–5 sein", "error"); return; }
  try {
    await api("PUT", `/api/device/${encodeURIComponent(d.ipv4)}/channel/${ch.number}/gain`,
      { level, type });
    toast(`Gain gesetzt: ${ch.label} = ${level}`, "ok");
  } catch (e) { toast(e.message, "error"); }
  refresh();
}

function button(label, onclick) {
  const b = document.createElement("button");
  b.textContent = label;
  b.onclick = onclick;
  return b;
}

// ---- search / filter (frontend only) -------------------------------------
// A device matches the query if its name OR any of its channel labels match.
// Matrix: hide RX rows and TX columns whose device AND channel both fail to
// match (matching channels of a matching device stay visible). Device panel:
// hide cards whose name and all channel labels fail. Empty query = all visible.
function matchesText(q, ...parts) {
  return parts.some(p => (p || "").toLowerCase().includes(q));
}

function applyFilter() {
  const q = filterQuery;
  // --- device panel ---
  for (const card of document.querySelectorAll("#devices .device")) {
    const show = !q || matchesText(q, card.dataset.name, card.dataset.labels);
    card.classList.toggle("filtered", !show);
  }
  // --- matrix ---
  const table = document.getElementById("matrix");
  if (!q) {
    for (const el of table.querySelectorAll(".filtered")) el.classList.remove("filtered");
    return;
  }
  // Which RX rows / TX columns survive. A row/column survives if its device name
  // matches OR its own channel label matches.
  const colVisible = new Map();  // col index -> bool
  for (const ch of table.querySelectorAll("th.tx-ch")) {
    colVisible.set(ch.dataset.col, matchesText(q, ch.dataset.name, ch.dataset.label));
  }
  for (const ch of table.querySelectorAll("th.tx-ch")) {
    ch.classList.toggle("filtered", !colVisible.get(ch.dataset.col));
  }
  // TX device header spans columns; hide it only if all its columns are hidden.
  for (const dev of table.querySelectorAll("th.tx-dev")) {
    let anyVisible = false;
    for (let c = +dev.dataset.colStart; c < +dev.dataset.colEnd; c++)
      if (colVisible.get(String(c))) { anyVisible = true; break; }
    dev.classList.toggle("filtered", !anyVisible);
  }
  // Data rows + their cells.
  for (const tr of table.querySelectorAll("tr")) {
    const rxDev = tr.querySelector("th.rx-dev");
    if (!rxDev) continue;  // header rows handled above
    const rxCh = tr.querySelector("th.rx-ch");
    const rowVisible = matchesText(q, rxDev.dataset.name, rxCh && rxCh.dataset.label);
    tr.classList.toggle("filtered", !rowVisible);
    for (const td of tr.querySelectorAll("td.cell"))
      td.classList.toggle("filtered", !colVisible.get(td.dataset.col));
  }
}

// ---- matrix clean-up -----------------------------------------------------
// Disconnect every subscribed RX channel of one device (header ✕).
function disconnectDevice(rxDevice) {
  if (!lastState) return;
  const dev = lastState.devices.find(d => d.name === rxDevice);
  if (!dev) return;
  const subbed = lastState.subscriptions.filter(s => s.rx_device === rxDevice);
  if (!subbed.length) { toast(`keine Abos auf ${rxDevice}`, "ok"); return; }
  if (!confirm(`Alle ${subbed.length} Abos von ${rxDevice} trennen?`)) return;
  for (const s of subbed) {
    const ch = dev.rx_channels.find(c => c.label === s.rx_channel);
    if (ch) removeSubscription(rxDevice, ch.label, ch.number);
  }
}

// Disconnect every subscription in the current state ("Alle trennen").
function disconnectAll() {
  if (!lastState) return;
  const subs = lastState.subscriptions;
  if (!subs.length) { toast("keine aktiven Abos", "ok"); return; }
  if (!confirm(`Wirklich ALLE ${subs.length} Abos trennen?`)) return;
  for (const s of subs) {
    const dev = lastState.devices.find(d => d.name === s.rx_device);
    if (!dev) continue;
    const ch = dev.rx_channels.find(c => c.label === s.rx_channel);
    if (ch) removeSubscription(s.rx_device, ch.label, ch.number);
  }
}

// Shared optimistic DELETE of a single subscription by rx device + channel.
function removeSubscription(rxDevice, rxLabel, rxNumber) {
  const keys = lastState.subscriptions
    .filter(s => s.rx_device === rxDevice && s.rx_channel === rxLabel)
    .map(s => subKey(s.rx_device, s.rx_channel, s.tx_device, s.tx_channel));
  for (const k of keys) pending.set(k, "remove");
  if (lastState) buildMatrix(lastState);
  mutationChain = mutationChain.then(async () => {
    try {
      await api("DELETE", "/api/subscription", { rx_device: rxDevice, rx_number: rxNumber });
      toast(`getrennt: ${rxLabel}@${rxDevice}`, "ok");
    } catch (e) {
      toast(e.message, "error");
    } finally {
      for (const k of keys) pending.delete(k);
      await refresh();
    }
  });
}

// ---- bulk routing (device -> device) -------------------------------------
// Route ALL channels of a chosen TX device into THIS device as RX (count=0).
async function bulkRouteInto(rxDev) {
  if (!lastState) return;
  const others = lastState.devices.filter(d => d.name !== rxDev.name && d.tx_channels.length);
  if (!others.length) { toast("kein anderes Gerät mit TX-Kanälen", "error"); return; }
  const names = others.map(d => d.name);
  const choice = prompt(
    `Alle Kanäle welches Geräts nach ${rxDev.name} routen?\n\n` + names.join("\n"),
    names[0]);
  if (!choice) return;
  const tx = others.find(d => d.name === choice.trim());
  if (!tx) { toast(`unbekanntes Gerät: ${choice}`, "error"); return; }
  try {
    await api("POST", "/api/subscription/bulk", { tx_device: tx.name, rx_device: rxDev.name });
    toast(`geroutet: ${tx.name} → ${rxDev.name}`, "ok");
  } catch (e) { toast(e.message, "error"); }
  refresh();
}

async function renameDevice(d) {
  const name = prompt(`Neuer Name für ${d.name}:`, d.name);
  if (!name || name === d.name) return;
  try {
    await api("PUT", `/api/device/${encodeURIComponent(d.ipv4)}/name`, { name });
    toast(`umbenannt: ${name}`, "ok");
  } catch (e) { toast(e.message, "error"); }
  refresh();
}

async function renameChannel(d, ch, type) {
  const name = prompt(`Neuer Name für Kanal ${ch.number} (${type.toUpperCase()}) auf ${d.name}:`, ch.label);
  if (!name || name === ch.label) return;
  try {
    await api("PUT", `/api/device/${encodeURIComponent(d.ipv4)}/channel/${ch.number}/name`, { name, type });
    toast(`Kanal umbenannt: ${name}`, "ok");
  } catch (e) { toast(e.message, "error"); }
  refresh();
}

async function doAction(path, okMsg) {
  try { await api("POST", path); toast(okMsg, "ok"); }
  catch (e) { toast(e.message, "error"); }
  refresh();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}

function updateAge() {
  const el = document.getElementById("updated");
  if (!lastGoodTime) { el.textContent = ""; return; }
  const sec = Math.round((Date.now() - lastGoodTime) / 1000);
  el.textContent = sec < 2 ? "aktualisiert: gerade eben"
    : sec < 60 ? `aktualisiert: vor ${sec}s`
    : `aktualisiert: vor ${Math.floor(sec / 60)}m ${sec % 60}s`;
  el.classList.toggle("stale", sec > 6);  // amber once data is older than a poll cycle
}

async function refresh() {
  let state;
  try {
    state = await api("GET", "/api/state");
  } catch (e) {
    updateAge();  // request failed (e.g. daemon restarting): keep the last good view
    return;
  }
  if (state.devices.length) {
    lastState = state;
    lastGoodTime = Date.now();
    document.getElementById("banner").classList.add("hidden");
    buildClock(state);
    buildMatrix(state);
    buildDevices(state);
  } else if (!lastState || !lastState.devices.length) {
    // Genuinely nothing yet (first load with no devices) — show the hint.
    const banner = document.getElementById("banner");
    banner.textContent = "Keine Geräte gefunden — läuft der netaudio-Daemon? Sind Dante-Geräte/Inferno aktiv?";
    banner.classList.remove("hidden");
    buildMatrix(state);
    buildDevices(state);
  }
  // else: empty poll but we have a good state (daemon restarting) — keep showing it.
  updateAge();
  refreshZonesState();
}

async function rescan() {
  const btn = document.getElementById("rescan");
  btn.disabled = true;
  try {
    await api("POST", "/api/rescan");
    toast("Geräte neu eingelesen", "ok");
  } catch (e) {
    toast(e.message, "error");
  } finally {
    btn.disabled = false;
  }
  refresh();
}

// ---- presets / scenes ----------------------------------------------------
// Save the whole routing matrix as a named scene and recall it. Recall makes
// the live routing EXACTLY match the saved scene (add missing, remove extra).
async function loadPresets() {
  const sel = document.getElementById("preset-select");
  const previous = sel.value;
  let names = [];
  try {
    names = (await api("GET", "/api/presets")).presets || [];
  } catch (e) { return; }  // keep the current list on a transient failure
  sel.innerHTML = '<option value="">— Szene —</option>';
  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  }
  if (names.includes(previous)) sel.value = previous;  // keep selection if still present
}

async function savePreset() {
  const sel = document.getElementById("preset-select");
  const name = prompt("Name der Szene:", sel.value || "");
  if (!name || !name.trim()) return;
  try {
    const res = await api("POST", "/api/presets", { name: name.trim() });
    toast(`gespeichert: ${name.trim()} (${res.count} Routen)`, "ok");
    await loadPresets();
    sel.value = name.trim();
  } catch (e) { toast(e.message, "error"); }
}

async function applyPreset() {
  const sel = document.getElementById("preset-select");
  const name = sel.value;
  if (!name) { toast("keine Szene gewählt", "error"); return; }
  if (!confirm(`Szene „${name}" anwenden? Routing wird exakt angeglichen.`)) return;
  try {
    const res = await api("POST", `/api/presets/${encodeURIComponent(name)}/apply`);
    toast(`${name}: +${res.added} / -${res.removed} / übersprungen ${res.skipped}`, "ok");
  } catch (e) { toast(e.message, "error"); }
  refresh();
}

async function deletePreset() {
  const sel = document.getElementById("preset-select");
  const name = sel.value;
  if (!name) { toast("keine Szene gewählt", "error"); return; }
  if (!confirm(`Szene „${name}" löschen?`)) return;
  try {
    await api("DELETE", `/api/presets/${encodeURIComponent(name)}`);
    toast(`gelöscht: ${name}`, "ok");
    await loadPresets();
  } catch (e) { toast(e.message, "error"); }
}

// ---- config export / import ----------------------------------------------
// Export downloads the live matrix as JSON; import POSTs it and applies it like
// a preset (server-side diff). Reuses the Phase-2 apply logic via apply_desired.
function exportMatrix() {
  if (!lastState) { toast("kein Zustand zum Exportieren", "error"); return; }
  const subscriptions = lastState.subscriptions.map(s => ({
    rx_device: s.rx_device, rx_channel: s.rx_channel,
    tx_device: s.tx_device, tx_channel: s.tx_channel,
  }));
  const blob = new Blob([JSON.stringify({ subscriptions }, null, 2)],
    { type: "application/json" });
  const now = new Date();
  const p = (n) => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}-${p(now.getHours())}${p(now.getMinutes())}`;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `netaudio-routing-${stamp}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
  toast(`exportiert: ${subscriptions.length} Routen`, "ok");
}

async function importMatrix(file) {
  let data;
  try {
    data = JSON.parse(await file.text());
  } catch (e) { toast("ungültige JSON-Datei", "error"); return; }
  if (!data || !Array.isArray(data.subscriptions)) {
    toast("ungültiges Format: subscriptions-Liste fehlt", "error");
    return;
  }
  if (!confirm(`Import anwenden? Routing wird exakt angeglichen (${data.subscriptions.length} Routen).`)) return;
  try {
    const res = await api("POST", "/api/subscriptions/import", { subscriptions: data.subscriptions });
    toast(`Import: +${res.added} / -${res.removed} / übersprungen ${res.skipped}`, "ok");
  } catch (e) { toast(e.message, "error"); }
  refresh();
}

// ---- audit log (session, in-memory on the server) ------------------------
// Collapsible view; fetches /api/log on open and renders newest-first.
async function loadLog() {
  const wrap = document.getElementById("log-entries");
  let log = [];
  try {
    log = (await api("GET", "/api/log")).log || [];
  } catch (e) { return; }
  wrap.innerHTML = "";
  if (!log.length) { wrap.textContent = "— noch keine Aktionen —"; return; }
  for (const e of log) {
    const row = document.createElement("div");
    row.className = "log-row" + (e.status >= 400 ? " err" : "");
    const t = new Date(e.ts * 1000).toLocaleTimeString("de-DE");
    row.innerHTML =
      `<span class="log-time">${escapeHtml(t)}</span>` +
      `<span class="log-method">${escapeHtml(e.method)}</span>` +
      `<span class="log-path">${escapeHtml(e.path)}</span>` +
      `<span class="log-status">${escapeHtml(e.status)}</span>`;
    wrap.appendChild(row);
  }
}

// ---- theme & help overlay ------------------------------------------------
function applyTheme(theme) {
  const btn = document.getElementById("theme-toggle");
  if (theme === "light") {
    document.documentElement.dataset.theme = "light";
    btn.textContent = "☀";
  } else {
    delete document.documentElement.dataset.theme;
    btn.textContent = "🌙";
  }
}

function toggleTheme() {
  const light = document.documentElement.dataset.theme !== "light";
  try { localStorage.setItem("netaudio-theme", light ? "light" : "dark"); } catch (_) {}
  applyTheme(light ? "light" : "dark");
}

function toggleHelp(force) {
  const ov = document.getElementById("help-overlay");
  const show = force === undefined ? ov.classList.contains("hidden") : force;
  ov.classList.toggle("hidden", !show);
}

// Crosshair hover: light up the hovered cell's whole row and column (patchbay
// feel). One delegated listener on the table survives every re-render.
function clearCrosshair() {
  for (const el of document.querySelectorAll("#matrix .row-hi, #matrix .col-hi"))
    el.classList.remove("row-hi", "col-hi");
}
(() => {
  const m = document.getElementById("matrix");
  m.addEventListener("mouseover", (e) => {
    const td = e.target.closest("td.cell");
    if (!td) return;
    const idx = [...td.parentElement.children].indexOf(td);
    clearCrosshair();
    for (const c of td.parentElement.children) c.classList.add("row-hi");
    // rows[0] is the spanning device-name row (colspans don't align); start at 1.
    for (let i = 1; i < m.rows.length; i++) {
      const cell = m.rows[i].children[idx];
      if (cell) cell.classList.add("col-hi");
    }
  });
  m.addEventListener("mouseleave", clearCrosshair);
})();

document.getElementById("rescan").onclick = rescan;
document.getElementById("refresh").onclick = refresh;
document.getElementById("disconnect-all").onclick = disconnectAll;
document.getElementById("preset-save").onclick = savePreset;
document.getElementById("preset-apply").onclick = applyPreset;
document.getElementById("preset-delete").onclick = deletePreset;
document.getElementById("export").onclick = exportMatrix;
document.getElementById("view-toggle").onclick = () =>
  setView(viewMode === "dashboard" ? "matrix" : "dashboard");
document.getElementById("theme-toggle").onclick = toggleTheme;
document.getElementById("help-toggle").onclick = () => toggleHelp();

// Import: the button opens a hidden file picker; picking a file applies it.
const importFile = document.getElementById("import-file");
document.getElementById("import").onclick = () => importFile.click();
importFile.addEventListener("change", () => {
  if (importFile.files.length) importMatrix(importFile.files[0]);
  importFile.value = "";  // allow re-picking the same file
});

// Audit log: (re)fetch its entries whenever the panel is opened.
const logEl = document.getElementById("log");
logEl.addEventListener("toggle", () => { if (logEl.open) loadLog(); });

// Sync the theme button glyph with the (pre-paint) applied theme.
applyTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");

// Live search: store the query and re-apply the filter (no re-render needed).
const searchInput = document.getElementById("search");
searchInput.addEventListener("input", () => {
  filterQuery = searchInput.value.trim().toLowerCase();
  applyFilter();
});
// Keyboard shortcuts. All ignored while typing in a field, except Esc.
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  if (e.key === "Escape") {
    toggleHelp(false);
    closeZoneEditor();
    if (document.activeElement === searchInput) searchInput.blur();
    return;
  }
  if (typing || e.altKey || e.ctrlKey || e.metaKey) return;
  if (e.key === "/") { e.preventDefault(); searchInput.focus(); }
  else if (e.key === "r") { e.preventDefault(); refresh(); }
  else if (e.key === "R") { e.preventDefault(); rescan(); }
  else if (e.key === "?") { e.preventDefault(); toggleHelp(); }
});

// ---- authentication ------------------------------------------------------
function showLogin() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  document.getElementById("logout").hidden = true;
  const overlay = document.getElementById("login-overlay");
  const wasHidden = overlay.classList.contains("hidden");
  overlay.classList.remove("hidden");
  // Only steal focus / reset the error when the overlay was previously hidden
  // (i.e. a real "please log in"). A failed-login 401 re-enters here with the
  // overlay already visible — don't yank focus off the password field, and let
  // the submit handler's catch show the error.
  if (wasHidden) {
    document.getElementById("login-error").classList.add("hidden");
    document.getElementById("login-user").focus();
  }
}

function hideLogin() {
  document.getElementById("login-overlay").classList.add("hidden");
}

function setUser(username) {
  const btn = document.getElementById("logout");
  btn.textContent = "⎋ " + username;
  btn.hidden = false;
}

function startApp() {
  hideLogin();
  if (!pollTimer) {
    refresh();
    loadPresets();
    pollTimer = setInterval(refresh, POLL_MS);
  }
}

async function boot() {
  let me;
  try {
    me = await api("GET", "/api/me");  // 401 -> api() shows the login overlay
  } catch (_) {
    return;
  }
  setUser(me.username);
  startApp();
}

document.getElementById("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const err = document.getElementById("login-error");
  const username = document.getElementById("login-user").value;
  const password = document.getElementById("login-pass").value;
  try {
    const me = await api("POST", "/api/login", { username, password });
    err.classList.add("hidden");
    document.getElementById("login-pass").value = "";
    setUser(me.username);
    startApp();
  } catch (_) {
    err.classList.remove("hidden");
  }
});

document.getElementById("logout").onclick = async () => {
  try { await api("POST", "/api/logout"); } catch (_) {}
  showLogin();
};

// ---- touch dashboard -----------------------------------------------------
function setView(mode) {
  viewMode = mode;
  document.querySelector("main").classList.toggle("hidden", mode === "dashboard");
  document.getElementById("dashboard").classList.toggle("hidden", mode !== "dashboard");
  document.getElementById("view-toggle").textContent =
    mode === "dashboard" ? "▦ Matrix" : "🎛 Dashboard";
  if (mode === "dashboard") loadDashboard();
}

async function loadDashboard() {
  try {
    zonesConfig = await api("GET", "/api/zones");
  } catch (_) { return; }
  renderDashboard();
  refreshZonesState();
}

function renderDashboard() {
  const root = document.getElementById("dashboard");
  root.innerHTML = "";
  const cfg = zonesConfig || { master: { buttons: [], off: false }, zones: [] };
  const master = cfg.master || { buttons: [], off: false };
  if ((master.buttons && master.buttons.length) || master.off) {
    root.appendChild(zoneSection("Alle Zonen", master.buttons || [], master.off,
      (s) => `/api/zones/apply/${encodeURIComponent(s)}`, `/api/zones/off`, "__master__"));
  }
  for (const z of cfg.zones || []) {
    root.appendChild(zoneSection(z.name, z.buttons || [], z.off,
      (s) => `/api/zones/${encodeURIComponent(z.name)}/apply/${encodeURIComponent(s)}`,
      `/api/zones/${encodeURIComponent(z.name)}/off`, z.name));
  }
  if (!root.children.length) {
    root.innerHTML = '<p class="dashboard-empty">Keine Zonen konfiguriert — über ⚙ den Editor öffnen.</p>';
  }
  const gear = document.createElement("button");
  gear.className = "zone-editor-open";
  gear.textContent = "⚙ Zonen bearbeiten";
  gear.onclick = openZoneEditor;
  root.appendChild(gear);
}

function zoneSection(title, buttons, hasOff, applyUrlFn, offUrl, zoneKey) {
  const sec = document.createElement("section");
  sec.className = "zone";
  sec.dataset.zone = zoneKey;
  const h = document.createElement("h2");
  h.textContent = title;
  sec.appendChild(h);
  const grid = document.createElement("div");
  grid.className = "zone-buttons";
  for (const scene of buttons) {
    const b = document.createElement("button");
    b.className = "zone-btn";
    b.dataset.scene = scene;
    b.textContent = scene;
    b.onclick = () => applyZone(applyUrlFn(scene), sec, b);
    grid.appendChild(b);
  }
  if (hasOff) {
    const off = document.createElement("button");
    off.className = "zone-btn zone-off";
    off.dataset.scene = "off";
    off.textContent = "Aus";
    off.onclick = () => applyZone(offUrl, sec, off);
    grid.appendChild(off);
  }
  sec.appendChild(grid);
  return sec;
}

function highlightActive(sec, activeScene) {
  for (const b of sec.querySelectorAll(".zone-btn")) {
    b.classList.toggle("active", b.dataset.scene === activeScene);
  }
}

async function applyZone(url, sec, btn) {
  highlightActive(sec, btn.dataset.scene);  // optimistic
  try {
    await api("POST", url);
  } catch (e) { toast(e.message, "error"); }
  refreshZonesState();
  refresh();
}

async function refreshZonesState() {
  if (viewMode !== "dashboard") return;
  let state;
  try { state = await api("GET", "/api/zones/state"); } catch (_) { return; }
  const root = document.getElementById("dashboard");
  for (const sec of root.querySelectorAll("section.zone")) {
    const key = sec.dataset.zone;
    const active = key === "__master__" ? state.master : (state.zones || {})[key];
    highlightActive(sec, active);
  }
}

// ---- zone editor ---------------------------------------------------------
let editorModel = null;   // working copy of the zones config while editing

async function openZoneEditor() {
  let state = lastState, presets = [];
  try {
    if (!state) state = await api("GET", "/api/state");
    presets = (await api("GET", "/api/presets")).presets || [];
    editorModel = JSON.parse(JSON.stringify(await api("GET", "/api/zones")));
  } catch (e) { toast(e.message, "error"); return; }
  editorModel._rxChoices = [];
  for (const d of state.devices || []) {
    for (const c of d.rx_channels || []) {
      editorModel._rxChoices.push({ device: d.name, channel: c.label });
    }
  }
  editorModel._sceneChoices = presets;
  renderEditor();
  document.getElementById("zone-editor").classList.remove("hidden");
}

function closeZoneEditor() {
  document.getElementById("zone-editor").classList.add("hidden");
  editorModel = null;
}

function renderEditor() {
  const m = editorModel;
  const masterEl = document.getElementById("editor-master");
  masterEl.innerHTML = "<h3>Alle Zonen (Master)</h3>";
  masterEl.appendChild(sceneChecklist(m.master.buttons, m._sceneChoices,
    (sel) => { m.master.buttons = sel; }));
  masterEl.appendChild(offToggle(m.master.off, (v) => { m.master.off = v; }));

  const zonesEl = document.getElementById("editor-zones");
  zonesEl.innerHTML = "";
  m.zones.forEach((zone, idx) => {
    const card = document.createElement("div");
    card.className = "editor-zone";
    const name = document.createElement("input");
    name.className = "editor-zone-name";
    name.value = zone.name;
    name.placeholder = "Zonenname";
    name.oninput = () => { zone.name = name.value; };
    const del = document.createElement("button");
    del.className = "editor-zone-del danger";
    del.textContent = "🗑";
    del.onclick = () => { m.zones.splice(idx, 1); renderEditor(); };
    const head = document.createElement("div");
    head.className = "editor-zone-head";
    head.append(name, del);
    card.appendChild(head);

    card.appendChild(label("RX-Ausgänge"));
    card.appendChild(rxChecklist(zone.rx, m._rxChoices, (sel) => { zone.rx = sel; }));
    card.appendChild(label("Szenen-Buttons"));
    card.appendChild(sceneChecklist(zone.buttons, m._sceneChoices, (sel) => { zone.buttons = sel; }));
    card.appendChild(offToggle(zone.off, (v) => { zone.off = v; }));
    zonesEl.appendChild(card);
  });
}

function label(text) {
  const el = document.createElement("div");
  el.className = "editor-label";
  el.textContent = text;
  return el;
}

function offToggle(checked, onChange) {
  const wrap = document.createElement("label");
  wrap.className = "editor-off";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = !!checked;
  cb.onchange = () => onChange(cb.checked);
  wrap.append(cb, document.createTextNode(' „Aus"-Button anzeigen'));
  return wrap;
}

function sceneChecklist(selected, choices, onChange) {
  const set = new Set(selected);
  const box = document.createElement("div");
  box.className = "editor-checklist";
  for (const name of choices) {
    const lab = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = set.has(name);
    cb.onchange = () => {
      cb.checked ? set.add(name) : set.delete(name);
      onChange(choices.filter((c) => set.has(c)));
    };
    lab.append(cb, document.createTextNode(" " + name));
    box.appendChild(lab);
  }
  if (!choices.length) box.textContent = "(keine Szenen gespeichert)";
  return box;
}

function rxChecklist(selected, choices, onChange) {
  const key = (r) => r.device + "\u0000" + r.channel;
  const set = new Set(selected.map(key));
  const box = document.createElement("div");
  box.className = "editor-checklist";
  for (const r of choices) {
    const lab = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = set.has(key(r));
    cb.onchange = () => {
      cb.checked ? set.add(key(r)) : set.delete(key(r));
      onChange(choices.filter((c) => set.has(key(c))).map((c) => ({ device: c.device, channel: c.channel })));
    };
    lab.append(cb, document.createTextNode(` ${r.device} / ${r.channel}`));
    box.appendChild(lab);
  }
  if (!choices.length) box.textContent = "(keine Geräte gefunden)";
  return box;
}

async function saveZoneEditor() {
  const payload = {
    master: { buttons: editorModel.master.buttons || [], off: !!editorModel.master.off },
    zones: (editorModel.zones || []).map((z) => ({
      name: z.name, rx: z.rx || [], buttons: z.buttons || [], off: !!z.off,
    })),
  };
  try {
    await api("PUT", "/api/zones", payload);
  } catch (e) { toast(e.message, "error"); return; }
  closeZoneEditor();
  toast("Zonen gespeichert", "ok");
  loadDashboard();
}

document.getElementById("editor-add-zone").onclick = () => {
  editorModel.zones.push({ name: "Neue Zone", rx: [], buttons: [], off: true });
  renderEditor();
};
document.getElementById("editor-cancel").onclick = closeZoneEditor;
document.getElementById("editor-save").onclick = saveZoneEditor;

boot();
setInterval(updateAge, 1000);  // tick the "last updated" age even between polls
