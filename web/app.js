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
async function loadDeck() {
  const stats = $("#deck-stats");
  renderLoading(stats, "Reading the desk…", 2);
  try {
    const d = await api("/api/trade/deck");
    const ks = d.kill_switch || {};
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

/* ---------- BOOT ---------- */
store.t ? showDeck() : showAuth();
