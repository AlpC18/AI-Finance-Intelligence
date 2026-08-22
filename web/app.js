"use strict";
const $ = (s, r = document) => r.querySelector(s);
const store = {
  get t() { return localStorage.getItem("afi_token"); },
  get r() { return localStorage.getItem("afi_refresh"); },
  get email() { return localStorage.getItem("afi_email"); },
  set(t, r, email) {
    if (t) localStorage.setItem("afi_token", t);
    if (r) localStorage.setItem("afi_refresh", r);
    if (email) localStorage.setItem("afi_email", email);
  },
  clear() { ["afi_token", "afi_refresh", "afi_email"].forEach(k => localStorage.removeItem(k)); },
};

const fmt = (n, d = 2) =>
  Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (n, d = 2) => (n >= 0 ? "+" : "") + fmt(n, d);
const money = (n) => "$" + fmt(n);

function toast(msg, isErr = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", isErr);
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.hidden = true), 3200);
}

async function api(path, { method = "GET", body, auth = true, retry = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && store.t) headers.Authorization = `Bearer ${store.t}`;
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401 && auth && retry && store.r) {
    if (await tryRefresh()) return api(path, { method, body, auth, retry: false });
  }
  if (res.status === 401 && auth) { store.clear(); showAuth(); throw new Error("Session ended — sign in again."); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || detail(data) || res.statusText);
  return res.status === 204 ? null : data;
}
const detail = (d) => Array.isArray(d.detail) ? d.detail[0]?.msg : d.detail;

async function tryRefresh() {
  try {
    const r = await fetch("/api/auth/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: store.r }),
    });
    if (!r.ok) return false;
    const d = await r.json();
    store.set(d.access_token, d.refresh_token, null);
    return true;
  } catch { return false; }
}

/* ---------- AUTH ---------- */
let authMode = "login";
function showAuth() { $("#deck").hidden = true; $("#auth").hidden = false; }
function showDeck() {
  $("#auth").hidden = true; $("#deck").hidden = false;
  $("#who").textContent = store.email || "";
  loadHoldings(); loadAlerts(); loadDeck(); loadContracts();
}
document.querySelectorAll(".tab").forEach(t =>
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("is-on"));
    t.classList.add("is-on");
    authMode = t.dataset.mode;
    $("#auth-submit").textContent = authMode === "login" ? "Sign in" : "Create account";
    $("#auth-note").textContent = "";
  }));

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = $("#auth-email").value.trim(), password = $("#auth-pass").value;
  const note = $("#auth-note"); note.classList.remove("ok"); note.textContent = "";
  try {
    if (authMode === "register") {
      await api("/api/auth/register", { method: "POST", auth: false, body: { email, password } });
      note.classList.add("ok"); note.textContent = "Account created — signing you in…";
    }
    const tok = await api("/api/auth/login", { method: "POST", auth: false, body: { email, password } });
    store.set(tok.access_token, tok.refresh_token, email);
    showDeck();
  } catch (err) { note.textContent = err.message; }
});

$("#logout").addEventListener("click", async () => {
  wsDisconnect();
  try { await api("/api/auth/logout", { method: "POST", body: { refresh_token: store.r } }); } catch {}
  store.clear(); showAuth();
});

/* ---------- MARKET ---------- */
function animateValue(el, to) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { el.textContent = money(to); return; }
  const from = 0, dur = 480, t0 = performance.now();
  (function step(t) {
    const k = Math.min((t - t0) / dur, 1), e = 1 - Math.pow(1 - k, 3);
    el.textContent = money(from + (to - from) * e);
    if (k < 1) requestAnimationFrame(step);
  })(t0);
}
function gauge(label, val, unit = "") {
  if (val === null || val === undefined) return "";
  return `<div class="gauge readout"><span class="readout-label">${label}</span>
    <span class="readout-value">${fmt(val)}${unit ? `<small>${unit}</small>` : ""}</span></div>`;
}
async function readMarket() {
  const sym = $("#mkt-symbol").value.trim().toUpperCase();
  if (!sym) return;
  const out = $("#mkt-readout"); $("#mkt-signal").innerHTML = "";
  out.className = ""; out.innerHTML = `<span class="readout-empty">Taking a reading on ${sym}…</span>`;
  try {
    const d = await api(`/api/market/${encodeURIComponent(sym)}`);
    const ch = d.quote.change_pct;
    const cls = ch == null ? "flat" : ch >= 0 ? "up" : "down";
    const i = d.indicators;
    out.innerHTML = `
      <div class="price-row">
        <div class="readout live"><span class="readout-label">${d.quote.symbol} · last</span>
          <span class="readout-value" id="px"></span></div>
        <span class="chip ${cls}">${ch == null ? "—" : signed(ch) + "%"}</span>
      </div>
      <div class="gauges">
        ${gauge("RSI 14", i.rsi_14)}${gauge("SMA 20", i.sma_20)}${gauge("SMA 50", i.sma_50)}
        ${gauge("MACD", i.macd)}${gauge("Volatility", i.volatility_annual)}
      </div>`;
    animateValue($("#px"), d.quote.price);
  } catch (e) { out.innerHTML = `<span class="readout-empty">${e.message}</span>`; }
}
async function readSignal() {
  const sym = $("#mkt-symbol").value.trim().toUpperCase();
  if (!sym) { toast("Enter a symbol first."); return; }
  const slot = $("#mkt-signal");
  slot.innerHTML = `<div class="verdict"><span class="conf">Consulting the model…</span></div>`;
  try {
    const s = await api(`/api/market/${encodeURIComponent(sym)}/ai-insights`);
    const badge = s.action.toLowerCase();
    slot.innerHTML = `<div class="verdict">
      <span class="verdict-badge ${badge}">${s.action}</span>
      <div class="verdict-body">
        <span class="conf">confidence ${Math.round((s.confidence || 0) * 100)}%</span>
        <p>${s.rationale || "—"}</p>
        ${s.degraded ? `<span class="degraded">Model offline — quant readings only.</span>` : `<span class="tag">not advice</span>`}
      </div></div>`;
  } catch (e) { slot.innerHTML = `<p class="degraded">${e.message}</p>`; }
}
$("#mkt-read").addEventListener("click", readMarket);
$("#mkt-symbol").addEventListener("keydown", (e) => e.key === "Enter" && readMarket());
$("#mkt-signal-btn").addEventListener("click", readSignal);

/* ---------- TRADE DESK: /api/trade/deck + /api/meta/contracts ---------- */
let contracts = null;
async function loadContracts() {
  try { contracts = await api("/api/meta/contracts", { auth: false }); }
  catch { contracts = null; }
}
async function loadDeck() {
  try {
    const d = await api("/api/trade/deck");
    const ks = d.kill_switch;
    $("#deck-stats").innerHTML = `
      ${sumReadout("Equity", money(ks.current_equity))}
      ${sumReadout("Daily P&L", signed(ks.drawdown_pct) + "%", ks.drawdown_pct)}
      ${sumReadout("Open orders", String(d.open_orders))}
      ${sumReadout("Broker", d.has_credentials ? "linked" : "not linked")}`;
    const toggle = $("#kill-toggle");
    toggle.checked = ks.halted;
    const state = $("#kill-state");
    state.textContent = ks.halted ? `HALTED · ${ks.reason || "manual"}` : "ARMED · trading live";
    state.className = "tag " + (ks.halted ? "neg" : "pos");
  } catch (e) { if (!/sign in/i.test(e.message)) toast(e.message, true); }
}
$("#kill-toggle").addEventListener("change", async (e) => {
  const enabled = e.target.checked;
  try {
    const st = await api("/api/trade/kill-switch", { method: "POST", body: { enabled } });
    toast(st.halted ? "Automated trading halted." : "Automated trading resumed.");
    loadDeck();
  } catch (err) { toast(err.message, true); e.target.checked = !enabled; }
});

/* ---------- WEBSOCKET: live streaming insight (WsStart/Token/End/Error/Alert) ---------- */
let ws = null;
function wsUrl(symbol) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const token = encodeURIComponent(store.t || "");
  return `${proto}//${location.host}/ws/insights/${encodeURIComponent(symbol)}?token=${token}`;
}
function streamInsight() {
  const sym = $("#desk-symbol").value.trim().toUpperCase();
  if (!sym) { toast("Enter a symbol to stream."); return; }
  const out = $("#desk-stream"), cite = $("#desk-cite");
  out.textContent = "Connecting…"; cite.innerHTML = "";
  wsDisconnect();
  ws = new WebSocket(wsUrl(sym));
  ws.onmessage = (ev) => {
    let f; try { f = JSON.parse(ev.data); } catch { return; }
    switch (f.type) {
      case "start": out.textContent = `▌ ${f.symbol}\n`; break;
      case "token": out.textContent += f.text; out.scrollTop = out.scrollHeight; break;
      case "end":
        cite.innerHTML =
          (f.citations || []).map(c => `<span class="chip">${c}</span>`).join("") +
          `<span class="tag">${f.disclaimer || "not advice"}</span>`;
        break;
      case "error": out.textContent += `\n[${f.detail || "stream error"}]`; break;
      case "alert":
        toast(`ALERT · ${f.symbol} ${f.condition}${f.threshold != null ? " " + fmt(f.threshold) : ""}`);
        loadAlerts();
        break;
    }
  };
  ws.onerror = () => { out.textContent += "\n[connection error]"; };
  ws.onclose = () => { ws = null; };
}
function wsDisconnect() { if (ws) { try { ws.close(); } catch {} ws = null; } }
$("#desk-stream-form").addEventListener("submit", (e) => { e.preventDefault(); streamInsight(); });

/* ---------- LEDGER ---------- */
$("#tx-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    symbol: $("#tx-symbol").value.trim().toUpperCase(),
    action: $("#tx-action").value,
    quantity: parseFloat($("#tx-qty").value),
    price: parseFloat($("#tx-price").value),
  };
  try {
    await api("/api/portfolio/transactions", { method: "POST", body });
    e.target.reset();
    toast(`${body.action} ${body.quantity} ${body.symbol} recorded.`);
    loadHoldings(); loadDeck();
  } catch (err) { toast(err.message, true); }
});
async function loadHoldings() {
  try {
    const [holds, risk] = await Promise.all([
      api("/api/portfolio"), api("/api/portfolio/risk"),
    ]);
    $("#pf-summary").innerHTML = `
      ${sumReadout("Portfolio value", money(risk.total_value))}
      ${sumReadout("Realized P&L", signed(risk.total_realized_pnl), risk.total_realized_pnl)}
      ${sumReadout("Unrealized P&L", signed(risk.total_unrealized_pnl), risk.total_unrealized_pnl)}`;
    renderHoldings(holds, risk.positions);
  } catch (e) { if (!/sign in/i.test(e.message)) toast(e.message, true); }
}
function sumReadout(label, val, signVal) {
  const cls = signVal === undefined ? "" : signVal >= 0 ? "pos" : "neg";
  return `<div class="readout"><span class="readout-label">${label}</span>
    <span class="readout-value ${cls}">${val}</span></div>`;
}
function renderHoldings(holds, positions) {
  const slot = $("#pf-holdings");
  if (!holds.length) { slot.innerHTML = ""; return; }
  const byS = Object.fromEntries((positions || []).map(p => [p.symbol, p]));
  slot.innerHTML = `<table><thead><tr>
      <th>Symbol</th><th>Qty</th><th>Avg cost</th><th>Last</th><th>Unreal.</th><th>Realized</th><th>Weight</th>
    </tr></thead><tbody>${holds.map(h => {
      const p = byS[h.symbol] || {};
      const u = p.unrealized_pnl ?? 0, r = h.realized_pnl ?? 0;
      return `<tr>
        <td>${h.symbol}</td><td>${fmt(h.quantity, h.quantity % 1 ? 4 : 0)}</td>
        <td>${fmt(h.avg_cost)}</td><td>${p.current_price != null ? fmt(p.current_price) : "—"}</td>
        <td class="${u >= 0 ? "pos" : "neg"}">${signed(u)}</td>
        <td class="${r >= 0 ? "pos" : "neg"}">${signed(r)}</td>
        <td>${p.weight_pct != null ? fmt(p.weight_pct) + "%" : "—"}</td></tr>`;
    }).join("")}</tbody></table>`;
}
$("#advice-btn").addEventListener("click", async () => {
  const slot = $("#pf-advice");
  slot.innerHTML = `<div class="verdict"><span class="conf">Reviewing your book…</span></div>`;
  try {
    const a = await api("/api/portfolio/risk/ai-insights");
    slot.innerHTML = `<div class="verdict"><div class="verdict-body">
      <p>${a.narrative || "—"}</p>
      ${(a.suggestions || []).map(s => `<p>· ${s}</p>`).join("")}
      ${a.degraded ? `<span class="degraded">Model offline — numbers only.</span>` : `<span class="tag">not advice</span>`}
    </div></div>`;
  } catch (e) { slot.innerHTML = `<p class="degraded">${e.message}</p>`; }
});

/* ---------- WIRE ---------- */
$("#news-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#news-query").value.trim();
  const list = $("#news-list"); list.innerHTML = `<p class="degraded">Scanning the wire…</p>`;
  try {
    const d = await api(`/api/news?query=${encodeURIComponent(q)}`, { auth: false });
    list.innerHTML = d.articles.length
      ? d.articles.map(a => `<div class="wire-item"><a href="${a.link}" target="_blank" rel="noopener">${a.title}</a>
          <span class="src">${a.source || "wire"}</span></div>`).join("")
      : `<p class="degraded">Nothing on the wire for “${q}”.</p>`;
  } catch (err) { list.innerHTML = `<p class="degraded">${err.message}</p>`; }
});
$("#news-ai").addEventListener("click", async () => {
  const q = $("#news-query").value.trim();
  if (!q) { toast("Enter a query first."); return; }
  const slot = $("#news-insight"); slot.innerHTML = `<div class="verdict"><span class="conf">Reading the wire…</span></div>`;
  try {
    const i = await api(`/api/news/ai-insights?query=${encodeURIComponent(q)}`, { auth: false });
    slot.innerHTML = `<div class="verdict"><div class="verdict-body">
      <span class="sentiment ${i.sentiment}">${i.sentiment}</span>
      <p>${i.summary || "—"}</p>
      ${(i.key_points || []).map(k => `<p>· ${k}</p>`).join("")}
      ${i.degraded ? `<span class="degraded">Model offline.</span>` : ""}
    </div></div>`;
  } catch (e) { slot.innerHTML = `<p class="degraded">${e.message}</p>`; }
});

/* ---------- WATCH ---------- */
$("#alert-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const thr = $("#al-threshold").value;
  const body = {
    symbol: $("#al-symbol").value.trim().toUpperCase(),
    condition_type: $("#al-cond").value,
    threshold_value: thr === "" ? null : parseFloat(thr),
    is_active: true,
  };
  try { await api("/api/alerts", { method: "POST", body }); e.target.reset(); toast("Alert armed."); loadAlerts(); }
  catch (err) { toast(err.message, true); }
});
const CONDS = { PRICE_ABOVE: "price above", PRICE_BELOW: "price below", RSI_BELOW: "RSI below", AI_SIGNAL_STRONG_BUY: "model: strong buy" };
async function loadAlerts() {
  try {
    const alerts = await api("/api/alerts");
    const list = $("#alerts-list");
    list.innerHTML = alerts.length
      ? alerts.map(a => `<div class="watch-item"><span class="w-live"></span>
          <span class="w-sym">${a.symbol}</span>
          <span class="w-cond">${CONDS[a.condition_type] || a.condition_type}${a.threshold_value != null ? " " + fmt(a.threshold_value) : ""}</span>
          <button class="link-btn" data-id="${a.id}">Disarm</button></div>`).join("")
      : `<p class="degraded">No alerts armed. The deck only watches what you tell it to.</p>`;
    list.querySelectorAll(".link-btn").forEach(b =>
      b.addEventListener("click", async () => {
        try { await api(`/api/alerts/${b.dataset.id}`, { method: "DELETE" }); loadAlerts(); }
        catch (e) { toast(e.message, true); }
      }));
  } catch (e) { if (!/sign in/i.test(e.message)) toast(e.message, true); }
}

/* ---------- BOOT ---------- */
store.t ? showDeck() : showAuth();
