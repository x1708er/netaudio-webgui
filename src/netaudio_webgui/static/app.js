const TOKEN = new URLSearchParams(location.search).get("token");
const POLL_MS = 3000;
let pollTimer = null;
let lastState = null;            // last rendered state (for instant re-render)
let lastGoodTime = null;         // timestamp of the last poll that returned devices
const pending = new Map();       // cellKey -> "add" | "remove" (optimistic, awaiting confirm)
let mutationChain = Promise.resolve();  // serialize mutations (each triggers a daemon restart)

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
    devRow.appendChild(cell);
    i += span;
  }
  table.appendChild(devRow);

  // Row 2: TX channel labels.
  const chRow = document.createElement("tr");
  chRow.appendChild(th("corner", ""));
  chRow.appendChild(th("corner", ""));
  for (const c of txCols) chRow.appendChild(th("tx-ch", c.label));
  table.appendChild(chRow);

  // Data rows.
  for (const r of rxRows) {
    const tr = document.createElement("tr");
    const rxDev = th("rx-dev", r.device);
    rxDev.title = r.device;  // full name when the header clips it
    tr.appendChild(rxDev);
    tr.appendChild(th("rx-ch", r.label));
    for (const c of txCols) {
      const td = document.createElement("td");
      td.className = "cell";
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
    }
    table.appendChild(tr);
  }
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
    const roleClass = d.clock_role.toLowerCase() === "leader" ? "role-leader" : "";
    div.innerHTML =
      `<h3>${escapeHtml(d.name)} <span class="meta ${roleClass}">${escapeHtml(d.clock_role)}</span></h3>` +
      `<div class="meta">${escapeHtml(d.ipv4)} · ${escapeHtml(d.model)} · ${escapeHtml(d.sample_rate || "?")} Hz` +
      ` · ${d.tx_channels.length} TX / ${d.rx_channels.length} RX</div>`;
    const actions = document.createElement("div");
    actions.className = "actions";
    actions.appendChild(button("Umbenennen", () => renameDevice(d)));
    actions.appendChild(button("Identify", () => doAction(`/api/device/${encodeURIComponent(d.ipv4)}/identify`, `Identify: ${d.name}`)));
    const reboot = button("Reboot", () => {
      if (confirm(`${d.name} wirklich neu starten?`))
        doAction(`/api/device/${encodeURIComponent(d.ipv4)}/reboot`, `Reboot: ${d.name}`);
    });
    reboot.className = "danger";
    actions.appendChild(reboot);
    div.appendChild(actions);
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
        chip.title = `Kanal ${ch.number} umbenennen`;
        row.appendChild(chip);
      }
      channels.appendChild(row);
    }
    div.appendChild(channels);
    aside.appendChild(div);
  }
}

function button(label, onclick) {
  const b = document.createElement("button");
  b.textContent = label;
  b.onclick = onclick;
  return b;
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
refresh();
pollTimer = setInterval(refresh, POLL_MS);
setInterval(updateAge, 1000);  // tick the "last updated" age even between polls
