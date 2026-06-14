const TOKEN = new URLSearchParams(location.search).get("token");
const POLL_MS = 3000;
let pollTimer = null;
let lastState = null;            // last rendered state (for instant re-render)
let lastGoodTime = null;         // timestamp of the last poll that returned devices
const pending = new Map();       // cellKey -> "add" | "remove" (optimistic, awaiting confirm)
let mutationChain = Promise.resolve();  // serialize mutations (each triggers a daemon restart)
let filterQuery = "";            // live search filter (lower-cased), re-applied after every render

function headers(extra) {
  const h = Object.assign({}, extra || {});
  if (TOKEN) h["Authorization"] = "Bearer " + TOKEN;
  return h;
}

async function api(method, path, body) {
  const opts = { method, headers: headers(body ? {"Content-Type": "application/json"} : {}) };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(path, opts);
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
    chRow.appendChild(cell);
  });
  table.appendChild(chRow);

  // Data rows.
  for (const r of rxRows) {
    const tr = document.createElement("tr");
    const rxDev = th("rx-dev", r.device);
    rxDev.title = r.device;  // full name when the header clips it
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
    tr.appendChild(rxCh);
    txCols.forEach((c, idx) => {
      const td = document.createElement("td");
      td.className = "cell";
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
  aside.innerHTML = "";
  for (const d of state.devices) {
    const div = document.createElement("div");
    div.className = "device";
    div.dataset.name = d.name;
    // All channel labels, joined, so the filter can match a device by any of them.
    div.dataset.labels = [...d.tx_channels, ...d.rx_channels].map(c => c.label).join(" ");
    const roleClass = d.clock_role.toLowerCase() === "leader" ? "role-leader" : "";
    div.innerHTML =
      `<h3>${escapeHtml(d.name)} <span class="meta ${roleClass}">${escapeHtml(d.clock_role)}</span></h3>` +
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

// ---- per-device configuration (collapsible) ------------------------------
function buildConfig(d) {
  const details = document.createElement("details");
  details.className = "config";
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
    document.getElementById("leader").textContent = state.leader ? `Clock-Leader: ${state.leader}` : "";
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

// Live search: store the query and re-apply the filter (no re-render needed).
const searchInput = document.getElementById("search");
searchInput.addEventListener("input", () => {
  filterQuery = searchInput.value.trim().toLowerCase();
  applyFilter();
});
// "/" focuses the search box (unless already typing in a field).
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== searchInput
      && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
    e.preventDefault();
    searchInput.focus();
  }
});

refresh();
loadPresets();
pollTimer = setInterval(refresh, POLL_MS);
setInterval(updateAge, 1000);  // tick the "last updated" age even between polls
