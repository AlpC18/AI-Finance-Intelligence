"use strict";
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/static/sw.js").catch(() => {}));
}
const $ = (s, r = document) => r.querySelector(s);
const THEME_KEY = "afi_ui_theme";
const THEMES = new Set(["terminal", "modernist", "industry"]);

function setTheme(theme) {
  const active = THEMES.has(theme) ? theme : "terminal";
  document.body.dataset.theme = active;
  localStorage.setItem(THEME_KEY, active);
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === active));
  });
  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor) themeColor.content = active === "terminal" ? "#0a0e1a" : "#f2f2f3";
  // The chart is canvas-rendered, so redraw after the CSS palette switches.
  // The lightweight JS test harness intentionally has no animation scheduler.
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => drawChart());
  }
}

document.querySelectorAll("[data-theme-choice]").forEach((button) => {
  button.addEventListener("click", () => setTheme(button.dataset.themeChoice));
});
setTheme(localStorage.getItem(THEME_KEY) || "terminal");

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

/* ---------- formatting + escaping ---------- */
// Feed titles, model text and broker strings all reach innerHTML. Escape every
// interpolated value: an RSS headline is third-party content, not our markup.
const esc = (v) => String(v ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");

const fmt = (n, d = 2) =>
  Number.isFinite(Number(n))
    ? Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })
    : "—";
const signed = (n, d = 2) => (Number.isFinite(Number(n)) ? (n >= 0 ? "+" : "") + fmt(n, d) : "—");
const money = (n) => (Number.isFinite(Number(n)) ? "$" + fmt(n) : "—");

function toast(msg, isErr = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", isErr);
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.hidden = true), 3200);
}

/* ---------- explicit async states: loading / empty / error ----------
   Every async surface resolves to a visible state. Leaving stale content on
   screen after a failure is how a deck lies to the person trading on it. */
function renderLoading(el, label = "Loading…", lines = 3) {
  el.innerHTML =
    `<div class="state" aria-busy="true"><span class="state-title">${esc(label)}</span>` +
    ["w-80", "w-60", "w-40"].slice(0, lines).map(w => `<span class="skel ${w}"></span>`).join("") +
    `</div>`;
}
function renderEmpty(el, title, hint = "") {
  el.innerHTML = `<div class="state"><span class="state-title">${esc(title)}</span>` +
    (hint ? `<span class="state-hint">${esc(hint)}</span>` : "") + `</div>`;
}
function renderError(el, message, onRetry) {
  el.innerHTML = `<div class="state is-error" role="alert">
      <span class="state-title">Could not load</span>
      <span class="state-hint">${esc(message)}</span>
      ${onRetry ? `<button class="state-retry" type="button">Try again</button>` : ""}
    </div>`;
  if (onRetry) el.querySelector(".state-retry")?.addEventListener("click", onRetry);
}
/** Disable a button and show a spinner for the duration of an async action. */
async function withBusy(btn, fn) {
  if (!btn) return fn();
  btn.setAttribute("aria-busy", "true");
  try { return await fn(); } finally { btn.removeAttribute("aria-busy"); }
}

/* ---------- api ---------- */
async function api(path, { method = "GET", body, auth = true, retry = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && store.t) headers.Authorization = `Bearer ${store.t}`;
  let res;
  try {
    res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch {
    // fetch only rejects on a transport failure — name it rather than surfacing
    // the browser's opaque "Failed to fetch".
    throw new Error("Network unreachable — check your connection.");
  }
  if (res.status === 401 && auth && retry && store.r) {
    if (await tryRefresh()) return api(path, { method, body, auth, retry: false });
  }
  if (res.status === 401 && auth) { store.clear(); showAuth(); throw new Error("Session ended — sign in again."); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || detail(data) || `${res.status} ${res.statusText}`);
  return res.status === 204 ? null : data;
}
const detail = (d) => Array.isArray(d.detail) ? d.detail[0]?.msg : d.detail;
const isAuthError = (e) => /sign in|session ended/i.test(e.message);

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
function showAuth() { wsTeardown(); $("#deck").hidden = true; $("#auth").hidden = false; }
function showDeck() {
  $("#auth").hidden = true; $("#deck").hidden = false;
  $("#who").textContent = store.email || "";
  loadHoldings(); loadAlerts(); loadDeck(); loadContracts(); loadActivity(); connectAccountSocket();
}
document.querySelectorAll(".tab").forEach(t =>
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("is-on"));
    t.classList.add("is-on");
    document.querySelectorAll(".tab").forEach(x =>
      x.setAttribute("aria-selected", String(x === t)));
    authMode = t.dataset.mode;
    $("#auth-submit").textContent = authMode === "login" ? "Sign in" : "Create account";
    $("#auth-note").textContent = "";
  }));

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = $("#auth-email").value.trim(), password = $("#auth-pass").value;
  const note = $("#auth-note"); note.classList.remove("ok"); note.textContent = "";
  await withBusy($("#auth-submit"), async () => {
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
});

$("#logout").addEventListener("click", async () => {
  wsTeardown();
  try { await api("/api/auth/logout", { method: "POST", body: { refresh_token: store.r } }); } catch {}
  store.clear(); showAuth();
});

/* ---------- MARKET ---------- */
function animateValue(el, to) {
  if (!Number.isFinite(Number(to))) { el.textContent = "—"; return; }
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
  return `<div class="gauge readout"><span class="readout-label">${esc(label)}</span>
    <span class="readout-value">${fmt(val)}${unit ? `<small>${esc(unit)}</small>` : ""}</span></div>`;
}
async function readMarket() {
  const sym = $("#mkt-symbol").value.trim().toUpperCase();
  const out = $("#mkt-readout");
  if (!sym) { renderEmpty(out, "No symbol", "Enter a ticker to take a reading."); return; }
  $("#mkt-signal").innerHTML = ""; $("#mkt-halt").innerHTML = "";
  out.className = "";
  renderLoading(out, `Taking a reading on ${sym}…`);
  await withBusy($("#mkt-read"), async () => {
    try {
      const d = await api(`/api/market/${encodeURIComponent(sym)}`);
      const ch = d.quote.change_pct;
      const cls = ch == null ? "flat" : ch >= 0 ? "up" : "down";
      const i = d.indicators || {};
      const gauges = [
        gauge("RSI 14", i.rsi_14), gauge("SMA 20", i.sma_20), gauge("SMA 50", i.sma_50),
        gauge("MACD", i.macd), gauge("Volatility", i.volatility_annual),
      ].filter(Boolean).join("");
      out.innerHTML = `
        <div class="price-row">
          <div class="readout live"><span class="readout-label">${esc(d.quote.symbol)} · last</span>
            <span class="readout-value" id="px"></span></div>
          <span class="chip ${cls}">${ch == null ? "—" : signed(ch) + "%"}</span>
        </div>
        ${gauges
          ? `<div class="gauges">${gauges}</div>`
          : `<p class="state-hint">Not enough history for indicators on this symbol.</p>`}`;
      animateValue($("#px"), d.quote.price);
    } catch (e) {
      renderError(out, e.message, isAuthError(e) ? null : readMarket);
    }
  });
}
async function readSignal() {
  const sym = $("#mkt-symbol").value.trim().toUpperCase();
  if (!sym) { toast("Enter a symbol first."); return; }
  const slot = $("#mkt-signal"), halt = $("#mkt-halt");
  halt.innerHTML = "";
  renderLoading(slot, "Consulting the model…", 2);
  await withBusy($("#mkt-signal-btn"), async () => {
    try {
      const s = await api(`/api/market/${encodeURIComponent(sym)}/ai-insights`);
      // A forced HOLD at zero confidence is the event circuit breaker, not a
      // model opinion. Surface it as a halt so it can't be mistaken for advice.
      if (s.action === "HOLD" && !s.degraded && (s.confidence || 0) === 0) {
        halt.innerHTML = haltBanner("Trading halted — high-impact event", s.rationale, true);
      }
      const badge = esc((s.action || "hold").toLowerCase());
      slot.innerHTML = `<div class="verdict">
        <span class="verdict-badge ${badge}">${esc(s.action)}</span>
        <div class="verdict-body">
          <span class="conf">confidence ${Math.round((s.confidence || 0) * 100)}%</span>
          <p>${esc(s.rationale) || "—"}</p>
          ${(s.citations || []).map(c => `<span class="chip">${esc(c)}</span>`).join("")}
          ${s.degraded
            ? `<span class="degraded">Model offline — quant readings only.</span>`
            : `<span class="tag">not advice</span>`}
        </div></div>`;
    } catch (e) { renderError(slot, e.message, isAuthError(e) ? null : readSignal); }
  });
}
$("#mkt-read").addEventListener("click", readMarket);
$("#mkt-symbol").addEventListener("keydown", (e) => e.key === "Enter" && readMarket());
$("#mkt-signal-btn").addEventListener("click", readSignal);

/* ---------- halt rendering (kill-switch + event circuit breaker) ---------- */
function haltBanner(title, detailText, isEvent = false) {
  return `<div class="halt-banner ${isEvent ? "is-event" : ""}" role="alert">
      <span class="halt-icon">${isEvent ? "◈" : "■"}</span>
      <div><div class="halt-title">${esc(title)}</div>
        ${detailText ? `<div class="halt-detail">${esc(detailText)}</div>` : ""}</div>
    </div>`;
}

/* ---------- TRADE DESK ---------- */
let contracts = null;
async function loadContracts() {
  try { contracts = await api("/api/meta/contracts", { auth: false }); }
  catch { contracts = null; }
}
function metric(label, value, signVal, sub) {
  const cls = signVal === undefined || signVal === null ? "" : signVal >= 0 ? "pos" : "neg";
  return `<div class="metric"><span class="metric-label">${esc(label)}</span>
    <span class="metric-value ${cls}">${esc(value)}</span>
    ${sub ? `<span class="metric-sub">${esc(sub)}</span>` : ""}</div>`;
}
function renderDeckHeader(deck) {
  const ks = deck.kill_switch || {};
  const equity = $("#top-equity");
  const daily = $("#top-daily-pnl");
  const limit = $("#top-loss-limit");
  const drawdown = $("#top-drawdown");
  const broker = $("#top-broker");
  const session = $("#session-state");
  if (equity) equity.textContent = money(ks.current_equity);
  if (daily) {
    daily.textContent = ks.drawdown_pct == null ? "—" : signed(ks.drawdown_pct) + "%";
    daily.className = ks.drawdown_pct >= 0 ? "pos" : "neg";
  }
  if (limit) limit.textContent = "loss limit " + fmt(ks.daily_loss_limit_pct) + "%";
  if (drawdown) {
    drawdown.textContent = ks.drawdown_pct == null ? "—" : fmt(ks.drawdown_pct) + "%";
    drawdown.className = ks.drawdown_pct < 0 ? "neg" : "";
  }
  if (broker) broker.textContent = deck.has_credentials ? "Broker linked · paper" : "Paper broker";
  if (session) session.textContent = "Last desk refresh " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
async function loadDeck() {
  const stats = $("#deck-stats");
  renderLoading(stats, "Reading the desk…", 2);
  try {
    const d = await api("/api/trade/deck");
    const ks = d.kill_switch || {};
    renderDeckHeader(d);
    stats.innerHTML =
      metric("Equity", money(ks.current_equity)) +
      metric("Daily P&L", signed(ks.drawdown_pct) + "%", ks.drawdown_pct,
             `limit ${fmt(ks.daily_loss_limit_pct)}%`) +
      metric("Open orders", String(d.open_orders ?? 0),
             undefined, d.open_orders ? "awaiting reconciliation" : "all settled") +
      metric("Broker", d.has_credentials ? "Linked" : "Not linked",
             undefined, d.has_credentials ? "paper trading" : "add keys to trade");

    const toggle = $("#kill-toggle");
    toggle.checked = !!ks.halted;
    const state = $("#kill-state");
    state.textContent = ks.halted ? `HALTED · ${ks.reason || "manual"}` : "ARMED · trading live";
    state.className = "tag " + (ks.halted ? "neg" : "pos");
    $("#deck-halt").innerHTML = ks.halted
      ? haltBanner("Automated trading halted",
          ks.manual_halt ? "Manual kill-switch engaged. Orders are refused until resumed."
                         : `Daily loss limit reached — ${ks.reason || "drawdown breach"}.`)
      : "";
  } catch (e) {
    if (isAuthError(e)) return;
    renderError(stats, e.message, loadDeck);
  }
}
$("#kill-toggle").addEventListener("change", async (e) => {
  const enabled = e.target.checked;
  try {
    const st = await api("/api/trade/kill-switch", { method: "POST", body: { enabled } });
    toast(st.halted ? "Automated trading halted." : "Automated trading resumed.");
    loadDeck();
  } catch (err) { toast(err.message, true); e.target.checked = !enabled; }
});

/* ---------- WEBSOCKET: connection state machine ----------
   The socket is the deck's live link. It now reports its own state and
   reconnects on its own with capped exponential backoff, because a silently
   dead socket looks identical to a quiet market. */
const WS_MAX_RETRIES = 6;
const accountWs = { sock: null, timer: null, retries: 0, closed: false };
const ws = {
  sock: null,
  symbol: null,
  retries: 0,
  timer: null,
  manualClose: false,
  gotFrame: false,
  // Terminal by intent: the stream finished, or the server rejected us. Distinct
  // from "currently down", which SHOULD auto-recover. A stale timer handle is
  // not a safe proxy for either, so recovery never infers it from one.
  stopped: false,
};

function setConn(state, text, action) {
  const el = $("#ws-conn");
  if (!el) return;
  el.className = `conn is-${state}`;
  const label = $("#ws-conn-text");
  label.textContent = text;
  const existing = el.querySelector(".conn-act");
  if (existing) existing.remove();
  if (action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "conn-act";
    btn.textContent = action.label;
    btn.addEventListener("click", action.onClick);
    el.appendChild(btn);
  }
}

function wsUrl(symbol) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const token = encodeURIComponent(store.t || "");
  return `${proto}//${location.host}/ws/insights/${encodeURIComponent(symbol)}?token=${token}`;
}

/** Close the socket and cancel any pending reconnect (logout / new stream). */
function wsTeardown() {
  ws.manualClose = true;
  clearTimeout(ws.timer);
  ws.timer = null;
  if (ws.sock) { try { ws.sock.close(); } catch {} }
  ws.sock = null;
  ws.retries = 0;
  accountWs.closed = true;
  clearTimeout(accountWs.timer);
  if (accountWs.sock) { try { accountWs.sock.close(); } catch {} }
  accountWs.sock = null;
}

function accountWsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/account?token=${encodeURIComponent(store.t || "")}`;
}
function setAccountConn(state, text) {
  const el = $("#account-conn");
  if (!el) return;
  el.className = `conn is-${state}`;
  el.lastElementChild.textContent = text;
}
function connectAccountSocket() {
  accountWs.closed = false;
  if (accountWs.sock) return;
  try { accountWs.sock = new WebSocket(accountWsUrl()); }
  catch { scheduleAccountReconnect(); return; }
  accountWs.sock.onopen = () => { accountWs.retries = 0; setAccountConn("live", "Live"); };
  accountWs.sock.onmessage = (event) => {
    let frame; try { frame = JSON.parse(event.data); } catch { return; }
    if (frame.type === "activity") loadActivity();
    if (frame.type === "alert") { toast(`ALERT · ${frame.symbol} ${frame.condition}`); loadAlerts(); loadActivity(); }
    if (frame.type === "order" || frame.type === "halt") { loadDeck(); loadHoldings(); loadActivity(); }
  };
  accountWs.sock.onclose = () => { accountWs.sock = null; if (!accountWs.closed) scheduleAccountReconnect(); };
}
function scheduleAccountReconnect() {
  if (accountWs.closed || accountWs.timer) return;
  accountWs.retries += 1;
  const delay = Math.min(1000 * 2 ** Math.min(accountWs.retries, 4), 15000);
  setAccountConn("retrying", "Reconnecting…");
  accountWs.timer = setTimeout(() => { accountWs.timer = null; connectAccountSocket(); }, delay);
}

async function loadActivity() {
  const target = $("#activity-list");
  if (!target) return;
  try {
    const page = await api("/api/activity?limit=12");
    if (!page.items.length) { renderEmpty(target, "No account activity", "Events will appear here as the desk changes."); return; }
    target.innerHTML = page.items.map(item => `<div class="activity-row">
      <span class="activity-kind">${esc(item.kind)}</span><div><strong>${esc(item.summary)}</strong>
      <small>${esc(new Date(item.created_at).toLocaleString())}</small></div></div>`).join("");
  } catch (err) { if (!isAuthError(err)) renderError(target, err.message, loadActivity); }
}

function streamInsight(symbol) {
  const sym = (symbol || $("#desk-symbol").value).trim().toUpperCase();
  if (!sym) { toast("Enter a symbol to stream."); return; }
  wsTeardown();
  ws.symbol = sym;
  ws.retries = 0;
  ws.stopped = false;
  wsConnect();
}

function wsConnect() {
  const out = $("#desk-stream"), cite = $("#desk-cite");
  ws.manualClose = false;
  ws.gotFrame = false;
  out.classList.remove("is-idle");
  cite.innerHTML = "";
  $("#stream-meta").textContent = ws.symbol || "";
  setConn("connecting", ws.retries ? `Reconnecting · try ${ws.retries}` : "Connecting…");
  if (!ws.retries) out.textContent = "";

  let sock;
  try {
    sock = new WebSocket(wsUrl(ws.symbol));
  } catch {
    wsScheduleRetry("Could not open a socket");
    return;
  }
  ws.sock = sock;

  sock.onopen = () => {
    ws.retries = 0;
    setConn("live", "Live");
    out.classList.add("stream-caret");
  };

  sock.onmessage = (ev) => {
    ws.gotFrame = true;
    let f;
    try { f = JSON.parse(ev.data); } catch { return; }
    switch (f.type) {
      case "start":
        out.textContent = `▌ ${f.symbol}\n`;
        out.classList.add("stream-caret");
        break;
      case "token":
        out.textContent += f.text;
        out.scrollTop = out.scrollHeight;
        break;
      case "end":
        out.classList.remove("stream-caret");
        cite.innerHTML =
          (f.citations || []).map(c => `<span class="chip">${esc(c)}</span>`).join("") +
          `<span class="tag">${esc(f.disclaimer) || "not advice"}</span>`;
        if (!out.textContent.trim()) {
          renderEmpty(out, "No model output",
            "The model returned nothing for this symbol — quant readings still apply.");
        }
        break;
      case "error":
        out.classList.remove("stream-caret");
        out.textContent += `\n[${f.detail || "stream error"}]`;
        break;
      case "alert":
        toast(`ALERT · ${f.symbol} ${f.condition}${f.threshold != null ? " " + fmt(f.threshold) : ""}`);
        loadAlerts();
        break;
      case "event":
        // Market-wide event broadcast — a blocking one halts the whole desk.
        if (f.blocking) {
          $("#deck-halt").innerHTML = haltBanner(
            "Event circuit breaker — trading halted",
            f.detail || f.headline || "High-impact event imminent.", true);
        }
        toast(`EVENT · ${f.headline || f.detail || "market event"}`);
        break;
    }
  };

  sock.onerror = () => { /* onclose always follows; retry is handled there. */ };

  sock.onclose = (ev) => {
    out.classList.remove("stream-caret");
    ws.sock = null;
    if (ws.manualClose) { setConn("idle", "Idle"); return; }
    // 1008 = policy violation: the server rejected the token. Retrying with the
    // same credential would just loop, so stop and ask for a re-auth instead.
    if (ev.code === 1008) {
      ws.stopped = true;  // re-dialling with the same token just loops
      setConn("down", "Not authorized");
      renderError(out, "The stream rejected your session. Sign in again to resume.");
      return;
    }
    if (ev.code === 1000 && ws.gotFrame) {
      ws.stopped = true;  // a finished stream must not silently re-run the model
      setConn("idle", "Stream complete");
      return;
    }
    wsScheduleRetry(ev.reason || "Connection lost");
  };
}

/** Reconnect with capped exponential backoff, then hand control to the user. */
function wsScheduleRetry(reason) {
  if (ws.retries >= WS_MAX_RETRIES) {
    setConn("down", "Disconnected", { label: "Reconnect", onClick: () => streamInsight(ws.symbol) });
    toast(`Live stream lost — ${reason}`, true);
    return;
  }
  ws.retries += 1;
  const delay = Math.min(1000 * 2 ** (ws.retries - 1), 15000);
  setConn("retrying", `Reconnecting in ${Math.round(delay / 1000)}s · try ${ws.retries}`,
    { label: "Retry now", onClick: () => { clearTimeout(ws.timer); wsConnect(); } });
  ws.timer = setTimeout(() => { ws.timer = null; wsConnect(); }, delay);
}

$("#desk-stream-form").addEventListener("submit", (e) => { e.preventDefault(); streamInsight(); });

// A backgrounded tab gets its socket reaped; reconnect when the user returns.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  if (!ws.symbol || ws.sock || ws.manualClose || ws.stopped || ws.timer) return;
  ws.retries = 0;
  wsConnect();
});
// The browser knows before we do that the network came back.
window.addEventListener("online", () => {
  if (ws.symbol && !ws.sock && !ws.manualClose && !ws.stopped) {
    ws.retries = 0;
    clearTimeout(ws.timer);
    ws.timer = null;
    wsConnect();
  }
});
window.addEventListener("offline", () => {
  if (ws.symbol) setConn("down", "Offline");
});

/* ---------- LEDGER ---------- */
$("#tx-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    symbol: $("#tx-symbol").value.trim().toUpperCase(),
    action: $("#tx-action").value,
    quantity: parseFloat($("#tx-qty").value),
    price: parseFloat($("#tx-price").value),
  };
  if (!Number.isFinite(body.quantity) || body.quantity <= 0) { toast("Quantity must be positive.", true); return; }
  if (!Number.isFinite(body.price) || body.price <= 0) { toast("Price must be positive.", true); return; }
  await withBusy(e.submitter, async () => {
    try {
      await api("/api/portfolio/transactions", { method: "POST", body });
      e.target.reset();
      toast(`${body.action} ${body.quantity} ${body.symbol} recorded.`);
      loadHoldings(); loadDeck();
    } catch (err) { toast(err.message, true); }
  });
});
async function loadHoldings() {
  const summary = $("#pf-summary"), table = $("#pf-holdings");
  renderLoading(summary, "Reconstructing the book…", 2);
  table.classList.add("stale");
  try {
    const [holds, risk] = await Promise.all([api("/api/portfolio"), api("/api/portfolio/risk")]);
    summary.innerHTML =
      metric("Portfolio value", money(risk.total_value), undefined,
             `${holds.length} position${holds.length === 1 ? "" : "s"}`) +
      metric("Realized P&L", signed(risk.total_realized_pnl), risk.total_realized_pnl) +
      metric("Unrealized P&L", signed(risk.total_unrealized_pnl), risk.total_unrealized_pnl) +
      metric("Volatility", risk.volatility_annual != null ? fmt(risk.volatility_annual) + "%" : "—",
             undefined, "annualized") +
      metric("Max drawdown", risk.max_drawdown_pct != null ? fmt(risk.max_drawdown_pct) + "%" : "—") +
      metric("Value at risk", risk.value_at_risk != null ? money(risk.value_at_risk) : "—",
             undefined, "95% 1-day");
    renderHoldings(holds, risk.positions);
    loadPerformance();
  } catch (e) {
    if (isAuthError(e)) return;
    renderError(summary, e.message, loadHoldings);
    table.innerHTML = "";
  } finally {
    table.classList.remove("stale");
  }
}
/* ---------- EQUITY CURVE ----------
 * Drawn by hand as inline SVG: the page ships self-contained, so there is no
 * chart library to reach for. The one rule that shapes the drawing is that a
 * day we never snapshotted must not look like a day the book sat still — those
 * spans are dashed and the caption says how many days were actually observed.
 */
const CURVE_W = 320, CURVE_H = 90, CURVE_PAD = 8, DAY_MS = 86400000;

function curveGeometry(points) {
  const t = points.map((p) => Date.parse(p.date + "T00:00:00Z"));
  const eq = points.map((p) => p.equity);
  const lo = Math.min(...eq), hi = Math.max(...eq);
  const spanT = (t[t.length - 1] - t[0]) || 1;
  const spanE = (hi - lo) || 0;
  const innerW = CURVE_W - CURVE_PAD * 2, innerH = CURVE_H - CURVE_PAD * 2;
  return points.map((p, i) => ({
    date: p.date,
    equity: p.equity,
    x: points.length === 1 ? CURVE_W / 2 : CURVE_PAD + ((t[i] - t[0]) / spanT) * innerW,
    // A perfectly flat book centres rather than pinning to the floor.
    y: spanE ? CURVE_PAD + innerH - ((eq[i] - lo) / spanE) * innerH : CURVE_H / 2,
    gapDays: i ? Math.round((t[i] - t[i - 1]) / DAY_MS) : 0,
  }));
}

function renderEquityCurve(report) {
  const slot = $("#pf-curve");
  if (!slot) return;
  const points = (report && report.points) || [];
  if (points.length < 2) {
    slot.innerHTML = `<div class="curve-empty">${
      points.length
        ? "One day recorded so far — the curve appears once there are two."
        : "No equity history yet. It builds up as you trade."
    }</div>`;
    return;
  }

  const g = curveGeometry(points);
  const dir = report.return_pct > 0 ? "is-up" : report.return_pct < 0 ? "is-down" : "";
  const segments = g.slice(1).map((pt, i) => {
    const prev = g[i];
    // > 1 day between snapshots means the span between them was never observed.
    const gap = pt.gapDays > 1 ? " is-gap" : "";
    return `<path class="curve-line ${dir}${gap}" d="M${fmt(prev.x, 2)} ${fmt(prev.y, 2)} L${fmt(pt.x, 2)} ${fmt(pt.y, 2)}" />`;
  }).join("");
  const dots = g.map((pt) =>
    `<circle class="curve-dot ${dir}" cx="${fmt(pt.x, 2)}" cy="${fmt(pt.y, 2)}" r="1.9"><title>${esc(pt.date)} · ${money(pt.equity)}</title></circle>`
  ).join("");

  const observed = `${report.covered_days} of ${report.span_days} day${report.span_days === 1 ? "" : "s"} observed`;
  slot.innerHTML = `
    <div class="curve-head">
      <span class="curve-title">Equity curve</span>
      <span class="curve-return ${report.return_pct >= 0 ? "pos" : "neg"}">${signed(report.return_pct)}%</span>
      <span class="curve-sub">${money(report.start_equity)} → ${money(report.current_equity)}</span>
    </div>
    <svg class="curve-svg" viewBox="0 0 ${CURVE_W} ${CURVE_H}" role="img"
         aria-label="Equity curve, ${esc(signed(report.return_pct))} percent, ${esc(observed)}">
      ${segments}${dots}
    </svg>
    <div class="curve-note">${esc(observed)}${
      report.sparse ? " — dashed spans were never sampled, not flat." : ""
    } · max drawdown ${fmt(report.max_drawdown_pct)}%</div>`;
}

async function loadPerformance() {
  try {
    renderEquityCurve(await api("/api/portfolio/performance"));
  } catch (e) {
    // The curve is context, not the book itself — never let it break the panel.
    if (!isAuthError(e)) $("#pf-curve").innerHTML =
      `<div class="curve-empty">Equity history unavailable.</div>`;
  }
}

function renderHoldings(holds, positions) {
  const slot = $("#pf-holdings");
  if (!holds.length) {
    renderEmpty(slot, "No open positions",
      "Record a trade above and the ledger will reconstruct your book from it.");
    return;
  }
  const byS = Object.fromEntries((positions || []).map(p => [p.symbol, p]));
  slot.innerHTML = `<table><thead><tr>
      <th>Symbol</th><th>Qty</th><th>Avg cost</th><th>Last</th><th>Unreal.</th><th>Realized</th><th>Weight</th>
    </tr></thead><tbody>${holds.map(h => {
      const p = byS[h.symbol] || {};
      const u = p.unrealized_pnl ?? 0, r = h.realized_pnl ?? 0;
      return `<tr>
        <td>${esc(h.symbol)}</td><td>${fmt(h.quantity, h.quantity % 1 ? 4 : 0)}</td>
        <td>${fmt(h.avg_cost)}</td><td>${p.current_price != null ? fmt(p.current_price) : "—"}</td>
        <td class="${u >= 0 ? "pos" : "neg"}">${signed(u)}</td>
        <td class="${r >= 0 ? "pos" : "neg"}">${signed(r)}</td>
        <td>${p.weight_pct != null ? fmt(p.weight_pct) + "%" : "—"}</td></tr>`;
    }).join("")}</tbody></table>`;
}
$("#advice-btn").addEventListener("click", async () => {
  const slot = $("#pf-advice");
  renderLoading(slot, "Reviewing your book…", 3);
  await withBusy($("#advice-btn"), async () => {
    try {
      const a = await api("/api/portfolio/risk/ai-insights");
      const points = (a.suggestions || []).map(s => `<p>· ${esc(s)}</p>`).join("");
      slot.innerHTML = `<div class="verdict"><div class="verdict-body">
        <p>${esc(a.narrative) || "—"}</p>${points}
        ${a.degraded ? `<span class="degraded">Model offline — numbers only.</span>`
                     : `<span class="tag">not advice</span>`}
      </div></div>`;
    } catch (e) { renderError(slot, e.message, isAuthError(e) ? null : () => $("#advice-btn").click()); }
  });
});

/* ---------- WIRE ---------- */
async function scanWire() {
  const q = $("#news-query").value.trim();
  const list = $("#news-list");
  if (!q) { renderEmpty(list, "Nothing to scan", "Enter a company or ticker."); return; }
  renderLoading(list, "Scanning the wire…");
  try {
    const d = await api(`/api/news?query=${encodeURIComponent(q)}`, { auth: false });
    const items = d.articles || [];
    if (!items.length) {
      renderEmpty(list, `Nothing on the wire for “${q}”`, "Try a ticker or a company's full name.");
      return;
    }
    list.innerHTML = items.map(a =>
      `<div class="wire-item">
         <a href="${esc(safeUrl(a.link))}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a>
         <span class="src">${esc(a.source) || "wire"}</span>
       </div>`).join("");
  } catch (err) { renderError(list, err.message, scanWire); }
}
$("#news-form").addEventListener("submit", (e) => { e.preventDefault(); scanWire(); });
$("#news-ai").addEventListener("click", async () => {
  const q = $("#news-query").value.trim();
  if (!q) { toast("Enter a query first."); return; }
  const slot = $("#news-insight");
  renderLoading(slot, "Reading the wire…", 2);
  await withBusy($("#news-ai"), async () => {
    try {
      const i = await api(`/api/news/ai-insights?query=${encodeURIComponent(q)}`, { auth: false });
      const points = (i.key_points || []).map(k => `<p>· ${esc(k)}</p>`).join("");
      slot.innerHTML = `<div class="verdict"><div class="verdict-body">
        <span class="sentiment ${esc(i.sentiment)}">${esc(i.sentiment)}</span>
        <p>${esc(i.summary) || "—"}</p>${points}
        ${i.degraded ? `<span class="degraded">Model offline.</span>` : ""}
      </div></div>`;
    } catch (e) { renderError(slot, e.message, () => $("#news-ai").click()); }
  });
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
  await withBusy(e.submitter, async () => {
    try { await api("/api/alerts", { method: "POST", body }); e.target.reset(); toast("Alert armed."); loadAlerts(); }
    catch (err) { toast(err.message, true); }
  });
});
const CONDS = {
  PRICE_ABOVE: "price above", PRICE_BELOW: "price below",
  RSI_BELOW: "RSI below", AI_SIGNAL_STRONG_BUY: "model: strong buy",
};
async function loadAlerts() {
  const list = $("#alerts-list");
  renderLoading(list, "Loading alerts…", 2);
  try {
    const alerts = await api("/api/alerts");
    if (!alerts.length) {
      renderEmpty(list, "No alerts armed", "The deck only watches what you tell it to.");
      return;
    }
    list.innerHTML = alerts.map(a =>
      `<div class="watch-item"><span class="w-live"></span>
         <span class="w-sym">${esc(a.symbol)}</span>
         <span class="w-cond">${esc(CONDS[a.condition_type] || a.condition_type)}${
           a.threshold_value != null ? " " + fmt(a.threshold_value) : ""}</span>
         <button class="link-btn" data-id="${esc(a.id)}">Disarm</button></div>`).join("");
    list.querySelectorAll(".link-btn").forEach(b =>
      b.addEventListener("click", async () => {
        b.disabled = true;
        try { await api(`/api/alerts/${b.dataset.id}`, { method: "DELETE" }); loadAlerts(); }
        catch (e) { b.disabled = false; toast(e.message, true); }
      }));
  } catch (e) {
    if (isAuthError(e)) return;
    renderError(list, e.message, loadAlerts);
  }
}

/* ---------- ADVANCED MODULES: TABS, CHART, MULTI-AGENT, OPTIONS, RAG, MARKETPLACE ---------- */

// 1. Navigation Tab Switching
document.querySelectorAll(".deck-nav-pills .pill").forEach(pill => {
  pill.addEventListener("click", () => {
    document.querySelectorAll(".deck-nav-pills .pill").forEach(p => p.classList.remove("is-on"));
    pill.classList.add("is-on");
    const view = pill.dataset.view;
    $("#chart-container").hidden = view !== "chart-view";
    $("#options-panel").hidden = view !== "options-view";
    $("#rag-panel").hidden = view !== "rag-view";
    $("#marketplace-panel").hidden = view !== "marketplace-view";
    if (view === "options-view") loadOptionsChain();
    if (view === "marketplace-view") loadMarketplace();
  });
});

// 2. Interactive Candlestick & Level Chart
let chartState = {
  price: 150.0,
  limit: 148.0,
  tp: 165.0,
  sl: 142.0,
  symbol: "AAPL",
};

function initInteractiveChart(symbol = "AAPL", currentPrice = 150.0) {
  chartState.symbol = symbol;
  chartState.price = currentPrice;
  chartState.limit = Number((currentPrice * 0.98).toFixed(2));
  chartState.tp = Number((currentPrice * 1.08).toFixed(2));
  chartState.sl = Number((currentPrice * 0.94).toFixed(2));

  $("#chart-symbol-badge").textContent = `${symbol} · 1D`;
  $("#chart-limit-val").value = chartState.limit;
  $("#chart-tp-val").value = chartState.tp;
  $("#chart-sl-val").value = chartState.sl;

  drawChart();
}

function drawChart() {
  const canvas = $("#interactive-chart");
  // Some environments (including server-side test DOMs) expose the element
  // but not the Canvas 2D API. The surrounding trading surface stays usable.
  if (!canvas || typeof canvas.getContext !== "function") return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#0d1320";
  ctx.fillRect(0, 0, w, h);

  // Draw grid
  ctx.strokeStyle = "rgba(236,231,218,0.05)";
  ctx.lineWidth = 1;
  for (let y = 30; y < h; y += 40) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }

  // Generate 25 price bars around current price
  const bars = 24;
  const barW = Math.floor((w - 100) / bars);
  let p = chartState.price * 0.92;
  const minP = chartState.price * 0.85;
  const maxP = chartState.price * 1.15;
  const priceToY = (val) => h - 30 - ((val - minP) / (maxP - minP)) * (h - 60);

  for (let i = 0; i < bars; i++) {
    const delta = (Math.sin(i * 0.8) * 2 + (Math.random() - 0.48) * 3);
    const open = p;
    const close = i === bars - 1 ? chartState.price : open + delta;
    const high = Math.max(open, close) + Math.random() * 2;
    const low = Math.min(open, close) - Math.random() * 2;
    p = close;

    const isUp = close >= open;
    ctx.fillStyle = isUp ? "#57C7A3" : "#E8795A";
    ctx.strokeStyle = ctx.fillStyle;

    const x = 40 + i * barW;
    const yOpen = priceToY(open);
    const yClose = priceToY(close);
    const yHigh = priceToY(high);
    const yLow = priceToY(low);

    // Wick
    ctx.beginPath();
    ctx.moveTo(x + barW / 2, yHigh);
    ctx.lineTo(x + barW / 2, yLow);
    ctx.stroke();

    // Body
    const top = Math.min(yOpen, yClose);
    const bodyH = Math.max(2, Math.abs(yClose - yOpen));
    ctx.fillRect(x + 2, top, barW - 4, bodyH);
  }

  // Horizontal Interactive Lines
  const drawLine = (val, color, label, dashed = true) => {
    const y = priceToY(val);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.setLineDash(dashed ? [5, 4] : []);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();

    // Tag
    ctx.setLineDash([]);
    ctx.fillStyle = color;
    ctx.fillRect(w - 90, y - 10, 85, 20);
    ctx.fillStyle = "#111";
    ctx.font = "bold 11px IBM Plex Mono, monospace";
    ctx.fillText(`${label}: $${val.toFixed(1)}`, w - 85, y + 4);
  };

  drawLine(chartState.tp, "#57C7A3", "TP", true);
  drawLine(chartState.limit, "#4fa3e3", "LIMIT", true);
  drawLine(chartState.price, "#E8B341", "LAST", false);
  drawLine(chartState.sl, "#E8795A", "SL", true);
}

["chart-limit-val", "chart-tp-val", "chart-sl-val"].forEach(id => {
  $(`#${id}`)?.addEventListener("input", (e) => {
    const val = parseFloat(e.target.value);
    if (!isNaN(val)) {
      if (id === "chart-limit-val") chartState.limit = val;
      if (id === "chart-tp-val") chartState.tp = val;
      if (id === "chart-sl-val") chartState.sl = val;
      drawChart();
    }
  });
});

$("#apply-chart-to-order")?.addEventListener("click", () => {
  $("#tx-symbol").value = chartState.symbol;
  $("#tx-price").value = chartState.limit;
  toast(`Synced ${chartState.symbol} Limit: $${chartState.limit}, SL: $${chartState.sl}, TP: $${chartState.tp} to desk.`);
});

// 3. Multi-Agent Deliberation Handler
$("#mkt-multi-agent-btn")?.addEventListener("click", async (e) => {
  const sym = ($("#mkt-symbol").value || "AAPL").trim().toUpperCase();
  const slot = $("#multi-agent-consensus-slot");
  renderLoading(slot, `Convening multi-agent committee for ${sym}…`, 4);

  await withBusy(e.target, async () => {
    try {
      const data = await api("/api/market/multi-agent/deliberate", {
        method: "POST",
        body: { symbol: sym, risk_tolerance: "moderate" }
      });
      renderMultiAgentConsensus(slot, data);
    } catch (err) {
      renderError(slot, err.message, () => $("#mkt-multi-agent-btn").click());
    }
  });
});

function renderMultiAgentConsensus(el, d) {
  const isVeto = d.risk_veto_applied;
  const badgeClass = isVeto ? "tag neg" : (d.final_action === "BUY" ? "tag pos" : "tag");

  let opinionsHtml = Object.entries(d.opinions).map(([role, op]) => `
    <div class="agent-card ${op.veto ? "agent-veto" : ""}">
      <div class="agent-header">
        <span class="agent-name">${esc(op.name)}</span>
        ${op.veto ? `<span class="agent-veto-badge">VETO</span>` : `<span class="tag ${op.action === 'BUY' ? 'pos' : (op.action === 'SELL' ? 'neg' : '')}">${esc(op.action)} (${(op.confidence*100).toFixed(0)}%)</span>`}
      </div>
      <p class="agent-thesis">${esc(op.thesis)}</p>
      ${op.risks_flagged.length ? `<small style="color:var(--loss);display:block;margin-top:4px;">⚠ ${esc(op.risks_flagged.join("; "))}</small>` : ""}
    </div>
  `).join("");

  el.innerHTML = `
    <div class="signal-card" style="border-color:${isVeto ? 'var(--loss)' : 'var(--signal)'};">
      <div class="signal-top">
        <span class="eyebrow" style="color:var(--signal);">Multi-Agent Committee Consensus</span>
        <span class="${badgeClass}">${esc(d.final_action)} (${(d.final_confidence * 100).toFixed(0)}% conviction)</span>
      </div>
      <p class="signal-rationale">${esc(d.deliberation_summary)}</p>
      <div class="agent-grid">${opinionsHtml}</div>
      <div class="metric-grid" style="margin-top:10px;">
        <div class="metric"><span class="metric-label">Consensus</span><span class="metric-value">${esc(d.consensus_strength).toUpperCase()}</span></div>
        <div class="metric"><span class="metric-label">Suggested SL</span><span class="metric-value neg">$${fmt(d.actionable_plan.suggested_stop_loss)}</span></div>
        <div class="metric"><span class="metric-label">Suggested TP</span><span class="metric-value pos">$${fmt(d.actionable_plan.suggested_take_profit)}</span></div>
        <div class="metric"><span class="metric-label">R:R Ratio</span><span class="metric-value">${fmt(d.actionable_plan.risk_reward_ratio || 2.0)}x</span></div>
      </div>
    </div>
  `;
}

// 4. Options Pricing & Greeks Handler
async function loadOptionsChain() {
  const sym = ($("#mkt-symbol").value || "AAPL").trim().toUpperCase();
  const grid = $("#options-greeks-grid");
  const table = $("#options-chain-table");
  renderLoading(grid, `Fetching ${sym} Options Chain & Greeks…`, 2);

  try {
    const chain = await api(`/api/options/chain/${sym}`);
    const nearestCall = chain.calls[Math.floor(chain.calls.length / 2)] || chain.calls[0];
    const g = nearestCall.greeks;

    grid.innerHTML = `
      <div class="metric"><span class="metric-label">ATM Delta (Δ)</span><span class="metric-value">${g.delta.toFixed(3)}</span><span class="metric-sub">Sensitivity</span></div>
      <div class="metric"><span class="metric-label">Gamma (Γ)</span><span class="metric-value">${g.gamma.toFixed(3)}</span><span class="metric-sub">Delta rate</span></div>
      <div class="metric"><span class="metric-label">Theta (Θ) / day</span><span class="metric-value neg">$${g.theta.toFixed(2)}</span><span class="metric-sub">Time decay</span></div>
      <div class="metric"><span class="metric-label">Vega (ν)</span><span class="metric-value">${g.vega.toFixed(2)}</span><span class="metric-sub">Per 1% IV</span></div>
      <div class="metric"><span class="metric-label">Implied Vol</span><span class="metric-value">${(g.implied_volatility * 100).toFixed(1)}%</span><span class="metric-sub">Annualized</span></div>
    `;

    table.innerHTML = `
      <table style="width:100%;font-size:12px;text-align:left;font-family:var(--mono);">
        <thead><tr style="color:var(--muted);border-bottom:1px solid var(--line);"><th style="padding:6px;">Strike</th><th>Call Bid/Ask</th><th>Put Bid/Ask</th><th>Delta</th><th>Vol</th></tr></thead>
        <tbody>
          ${chain.calls.slice(0, 6).map((c, i) => {
            const p = chain.puts[i] || c;
            return `<tr style="border-bottom:1px solid var(--line-2);"><td style="padding:6px;color:var(--signal);">$${c.strike}</td><td>$${c.bid}/$${c.ask}</td><td>$${p.bid}/$${p.ask}</td><td>${c.greeks.delta.toFixed(2)}</td><td>${c.volume}</td></tr>`;
          }).join("")}
        </tbody>
      </table>
    `;
  } catch (err) {
    renderError(grid, err.message, loadOptionsChain);
  }
}

$("#calc-opt-payoff")?.addEventListener("click", async () => {
  const sym = ($("#mkt-symbol").value || "AAPL").trim().toUpperCase();
  const type = $("#opt-template-select").value;
  const slot = $("#options-payoff-slot");
  renderLoading(slot, "Simulating payoff curve…", 2);

  try {
    const res = await api("/api/options/strategy-payoff", {
      method: "POST",
      body: {
        strategy_type: type,
        underlying_symbol: sym,
        underlying_price: chartState.price,
        legs: [
          { is_stock_leg: true, action: "buy", quantity: 100, premium_or_price: chartState.price },
          { option_type: "call", action: "sell", strike: chartState.price * 1.05, premium_or_price: 3.5, quantity: 1 }
        ]
      }
    });
    slot.innerHTML = `
      <div class="signal-card">
        <span class="eyebrow">${esc(res.strategy_name.toUpperCase())} PAYOFF SIMULATION</span>
        <p style="margin:6px 0;font-size:13px;">${esc(res.summary_thesis)}</p>
        <div class="metric-grid">
          <div class="metric"><span class="metric-label">Max Profit</span><span class="metric-value pos">$${res.max_profit ? fmt(res.max_profit) : 'Unlimited'}</span></div>
          <div class="metric"><span class="metric-label">Max Loss</span><span class="metric-value neg">$${res.max_loss ? fmt(res.max_loss) : 'Unlimited'}</span></div>
          <div class="metric"><span class="metric-label">Breakevens</span><span class="metric-value mono">${res.breakeven_points.map(b => '$'+b).join(', ') || 'N/A'}</span></div>
        </div>
      </div>
    `;
  } catch (err) {
    renderError(slot, err.message, () => $("#calc-opt-payoff").click());
  }
});

// 5. Deep RAG 2.0 Ingest & Tone Shift
$("#rag-ingest-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    symbol: $("#rag-sym").value.trim().toUpperCase(),
    doc_type: $("#rag-doc-type").value,
    title: $("#rag-doc-title").value.trim(),
    raw_text: $("#rag-doc-text").value.trim(),
  };
  await withBusy(e.submitter, async () => {
    try {
      await api("/api/rag/ingest", { method: "POST", body });
      toast("Financial document ingested & indexed into vector store.");
      e.target.reset();
    } catch (err) {
      toast(err.message, true);
    }
  });
});

$("#rag-check-shift")?.addEventListener("click", async () => {
  const sym = ($("#rag-sym").value || $("#mkt-symbol").value || "AAPL").trim().toUpperCase();
  const slot = $("#rag-shift-report");
  renderLoading(slot, `Calculating tone divergence for ${sym}…`, 2);
  try {
    const report = await api(`/api/rag/sentiment-shift/${sym}`);
    slot.innerHTML = `
      <div class="signal-card">
        <span class="eyebrow">Executive Tone &amp; Q&amp;A Divergence (${esc(report.fiscal_period)})</span>
        <p style="margin:6px 0;font-size:13px;">${esc(report.management_tone_summary)}</p>
        <div class="metric-grid">
          <div class="metric"><span class="metric-label">Prepared Remarks</span><span class="metric-value">${report.prepared_remarks_sentiment > 0 ? '+' : ''}${report.prepared_remarks_sentiment.toFixed(2)}</span></div>
          <div class="metric"><span class="metric-label">Q&A Unfiltered</span><span class="metric-value">${report.qa_session_sentiment > 0 ? '+' : ''}${report.qa_session_sentiment.toFixed(2)}</span></div>
          <div class="metric"><span class="metric-label">Sentiment Shift</span><span class="metric-value ${report.sentiment_divergence < -0.1 ? 'neg' : 'pos'}">${report.sentiment_divergence > 0 ? '+' : ''}${report.sentiment_divergence.toFixed(2)}</span></div>
        </div>
      </div>
    `;
  } catch (err) {
    renderError(slot, err.message, () => $("#rag-check-shift").click());
  }
});

// 6. Strategy Marketplace & Copy-Trading Leaderboard
async function loadMarketplace() {
  const list = $("#mp-leaderboard-list");
  renderLoading(list, "Loading strategy leaderboard…", 3);
  try {
    const data = await api("/api/marketplace/leaderboard");
    if (!data.length) {
      renderEmpty(list, "No strategies published yet", "Be the first to publish an algorithm.");
      return;
    }
    list.innerHTML = `
      <table style="width:100%;font-size:12.5px;text-align:left;font-family:var(--mono);">
        <thead><tr style="color:var(--muted);border-bottom:1px solid var(--line);"><th style="padding:8px;">Strategy</th><th>Author</th><th>Sharpe</th><th>CAGR</th><th>Max DD</th><th>Action</th></tr></thead>
        <tbody>
          ${data.map(s => `
            <tr style="border-bottom:1px solid var(--line-2);">
              <td style="padding:8px;"><strong>${esc(s.name)}</strong><br/><small style="color:var(--muted);">${esc(s.category)}</small></td>
              <td>${esc(s.publisher_name)}</td>
              <td style="color:var(--gain);">${s.sharpe_ratio.toFixed(2)}</td>
              <td style="color:var(--gain);">+${s.cagr_pct.toFixed(1)}%</td>
              <td style="color:var(--loss);">${s.max_drawdown_pct.toFixed(1)}%</td>
              <td><button class="btn btn-solid btn-sm copy-btn" data-id="${s.strategy_id}">Mirror Copy</button></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
    list.querySelectorAll(".copy-btn").forEach(b => {
      b.addEventListener("click", async () => {
        try {
          await api("/api/marketplace/subscribe", {
            method: "POST",
            body: { strategy_id: parseInt(b.dataset.id, 10), allocated_capital: 2500.0, copy_mode: "paper" }
          });
          toast("Subscribed! Orders will mirror in Paper Trading mode.");
        } catch (err) {
          toast(err.message, true);
        }
      });
    });
  } catch (err) {
    renderError(list, err.message, loadMarketplace);
  }
}

// Hook into market read to auto-refresh interactive chart
const origMktRead = $("#mkt-read");
if (origMktRead) {
  const origHandler = origMktRead.onclick;
  origMktRead.addEventListener("click", () => {
    const sym = ($("#mkt-symbol").value || "AAPL").trim().toUpperCase();
    initInteractiveChart(sym, chartState.price);
  });
}

// Initialize interactive chart on boot
initInteractiveChart("AAPL", 150.0);

/* ---------- workspace navigation ---------- */
function navigateWorkspace(targetId) {
  const target = document.getElementById(targetId);
  if (!target) return;
  target.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  document.querySelectorAll("[data-scroll-target]").forEach((button) =>
    button.classList.toggle("is-current", button.dataset.scrollTarget === targetId));
}
document.querySelectorAll("[data-scroll-target]").forEach((button) => {
  button.addEventListener("click", () => navigateWorkspace(button.dataset.scrollTarget));
});
document.querySelectorAll("[data-open-view]").forEach((button) => {
  button.addEventListener("click", () => {
    const view = button.dataset.openView;
    const tab = document.querySelector(".pill[data-view='" + view + "']");
    if (tab) tab.click();
    navigateWorkspace("instrument");
  });
});

/* ---------- BOOT ---------- */
store.t ? showDeck() : showAuth();
