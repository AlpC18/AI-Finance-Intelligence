/**
 * WebSocket connection state machine in web/app.js.
 *
 * The streaming panel is the only part of the deck that must survive the network
 * being hostile: a dropped socket, a rejected token, a backgrounded tab. The
 * rules pinned here are the ones a trader depends on:
 *
 *   - the pill NEVER silently lies: every transition ends in a visible state,
 *   - a rejected credential (1008) stops dead instead of hammering the server,
 *   - reconnects back off exponentially and hand control back to the user,
 *   - a completed stream is distinguishable from a dropped one.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { loadApp } from "./harness.mjs";

const BACKOFF = [1000, 2000, 4000, 8000, 15000, 15000]; // capped at 15s
const MAX_RETRIES = BACKOFF.length;

/** Start a stream and return the app plus its live socket. */
function streaming(symbol = "AAPL") {
  const app = loadApp();
  app.ctx.streamInsight(symbol);
  return { app, sock: app.sockets.last };
}

/** Drop the socket abnormally and fire the scheduled reconnect. */
function dropAndReconnect(app, sock) {
  sock.serverClose(1006, "connection lost");
  app.clock.runNext();
  return app.sockets.last;
}

// --------------------------- connecting / live ------------------------------

test("opening a stream shows the connecting state and dials the right URL", () => {
  const { app, sock } = streaming("AAPL");
  assert.equal(app.connState(), "connecting");
  assert.equal(app.connText(), "Connecting…");
  assert.match(sock.url, /^ws:\/\/localhost:8000\/ws\/insights\/AAPL\?token=test-token$/);
});

test("the symbol is upper-cased and trimmed before it reaches the socket", () => {
  const app = loadApp();
  app.ctx.streamInsight("  aapl  ");
  assert.match(app.sockets.last.url, /\/ws\/insights\/AAPL\?/);
});

test("wss is used when the page is served over https", () => {
  const app = loadApp();
  app.ctx.location.protocol = "https:";
  app.ctx.streamInsight("AAPL");
  assert.match(app.sockets.last.url, /^wss:\/\//);
});

test("an opened socket reports Live", () => {
  const { app, sock } = streaming();
  sock.open();
  assert.equal(app.connState(), "live");
  assert.equal(app.connText(), "Live");
});

// --------------------------- frame handling ---------------------------------

test("token frames accumulate into the stream panel", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.send({ type: "start", symbol: "AAPL" });
  sock.send({ type: "token", text: "Yukselis " });
  sock.send({ type: "token", text: "egilimi." });
  assert.equal(app.stream().textContent, "▌ AAPL\nYukselis egilimi.");
});

test("a malformed frame is ignored rather than breaking the stream", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.send({ type: "token", text: "kept" });
  sock.sendRaw("{not json");
  sock.send({ type: "token", text: " and kept" });
  assert.equal(app.stream().textContent, "kept and kept");
});

test("citations from the end frame are HTML-escaped", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.send({ type: "token", text: "analiz" });
  sock.send({ type: "end", citations: ['<img src=x onerror="alert(1)">'], disclaimer: "not advice" });
  const html = app.el("#desk-cite").innerHTML;
  assert.ok(!html.includes("<img"), "raw markup must not reach the DOM");
  assert.ok(html.includes("&lt;img"), "citation should be escaped");
});

test("an end frame with no output renders an explicit empty state", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.send({ type: "end", citations: [] });
  assert.match(app.stream().innerHTML, /No model output/);
});

test("an error frame is surfaced inline without killing the socket", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.send({ type: "token", text: "kismi" });
  sock.send({ type: "error", detail: "upstream timeout" });
  assert.match(app.stream().textContent, /upstream timeout/);
  assert.equal(app.connState(), "live");
});

test("a blocking event frame raises the circuit-breaker halt banner", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.send({ type: "event", blocking: true, headline: "FOMC", detail: "Rate decision imminent" });
  assert.match(app.el("#deck-halt").innerHTML, /Rate decision imminent/);
});

test("a non-blocking event leaves the desk unhalted", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.send({ type: "event", blocking: false, headline: "minor filing" });
  assert.equal(app.el("#deck-halt").innerHTML, "");
});

// --------------------------- auth rejection ---------------------------------

test("a 1008 policy close stops dead and never retries", () => {
  const { app, sock } = streaming();
  sock.open();
  const before = app.sockets.instances.length;

  sock.serverClose(1008, "invalid token");

  assert.equal(app.connState(), "down");
  assert.equal(app.connText(), "Not authorized");
  assert.deepEqual(app.clock.pending(), [], "no reconnect may be scheduled");
  assert.equal(app.sockets.instances.length, before, "no new socket may be opened");
  assert.match(app.stream().innerHTML, /Sign in again/);
});

// --------------------------- backoff ladder ---------------------------------

test("the first drop schedules a one-second reconnect", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.serverClose(1006, "connection lost");
  assert.equal(app.connState(), "retrying");
  assert.deepEqual(app.clock.pending(), [1000]);
  assert.match(app.connText(), /Reconnecting in 1s · try 1/);
});

test("reconnect delays follow exponential backoff capped at 15s", () => {
  const { app } = streaming();
  let sock = app.sockets.last;
  const delays = [];

  for (let i = 0; i < MAX_RETRIES; i++) {
    // deliberately never open(): a successful open resets the ladder by design,
    // so the climb only happens while reconnects keep failing.
    sock.serverClose(1006, "dropped");
    delays.push(app.clock.pending()[0]);
    app.clock.runNext();          // fire the reconnect
    sock = app.sockets.last;
  }
  assert.deepEqual(delays, BACKOFF);
});

test("the retry counter is shown while reconnecting", () => {
  const { app } = streaming();
  let sock = app.sockets.last;
  sock.serverClose(1006);
  app.clock.runNext();
  assert.match(app.connText(), /Reconnecting · try 1/);
});

test("exhausting the retries hands control back to the user", () => {
  const { app } = streaming();
  let sock = app.sockets.last;

  for (let i = 0; i < MAX_RETRIES; i++) {
    sock.serverClose(1006, "dropped");
    app.clock.runNext();
    sock = app.sockets.last;
  }
  sock.serverClose(1006, "dropped");   // one drop past the budget

  assert.equal(app.connState(), "down");
  assert.equal(app.connText(), "Disconnected");
  const action = app.connAction();
  assert.ok(action, "a manual Reconnect control must be offered");
  assert.equal(action.textContent, "Reconnect");
});

test("the manual Reconnect control opens a fresh socket", () => {
  const { app } = streaming();
  let sock = app.sockets.last;
  for (let i = 0; i < MAX_RETRIES; i++) {
    sock.serverClose(1006);
    app.clock.runNext();
    sock = app.sockets.last;
  }
  sock.serverClose(1006);

  const before = app.sockets.instances.length;
  app.connAction().dispatch("click");
  assert.equal(app.sockets.instances.length, before + 1);
  assert.equal(app.connState(), "connecting");
});

test("Retry now skips the remaining wait", () => {
  const { app, sock } = streaming();
  sock.serverClose(1006);
  const before = app.sockets.instances.length;

  app.connAction().dispatch("click");   // "Retry now"

  assert.equal(app.sockets.instances.length, before + 1);
  assert.deepEqual(app.clock.pending(), [], "the pending timer must be cancelled");
});

test("a successful reconnect resets the backoff ladder", () => {
  const { app } = streaming();
  let sock = app.sockets.last;

  for (let i = 0; i < 3; i++) {           // climb to a 4s delay
    sock.serverClose(1006);
    app.clock.runNext();
    sock = app.sockets.last;
  }
  sock.open();                            // recovered
  assert.equal(app.connState(), "live");

  sock.serverClose(1006);                 // next drop starts from the bottom
  assert.deepEqual(app.clock.pending(), [1000]);
});

// --------------------------- clean completion -------------------------------

test("a clean close after streaming reports completion, not failure", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.send({ type: "token", text: "tamamlandi" });
  sock.serverClose(1000, "done");

  assert.equal(app.connState(), "idle");
  assert.equal(app.connText(), "Stream complete");
  assert.deepEqual(app.clock.pending(), [], "a completed stream must not reconnect");
});

test("a clean close that delivered nothing is treated as a drop", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.serverClose(1000, "");             // 1000 but no frame ever arrived
  assert.equal(app.connState(), "retrying");
  assert.deepEqual(app.clock.pending(), [1000]);
});

// --------------------------- manual teardown --------------------------------

test("tearing the socket down goes idle and schedules no reconnect", () => {
  const { app, sock } = streaming();
  sock.open();
  app.ctx.wsTeardown();

  assert.equal(app.connState(), "idle");
  assert.equal(app.connText(), "Idle");
  assert.deepEqual(app.clock.pending(), []);
});

test("starting a new stream replaces the previous socket", () => {
  const { app, sock } = streaming("AAPL");
  sock.open();
  app.ctx.streamInsight("MSFT");

  assert.ok(sock.closed, "the previous socket must be closed");
  assert.match(app.sockets.last.url, /\/ws\/insights\/MSFT\?/);
  assert.equal(app.connState(), "connecting");
});

// --------------------------- environment recovery ---------------------------

test("returning to the tab recovers a connection that exhausted its retries", () => {
  // Regression: the reconnect timer handle was never released when it fired, so
  // this guard saw a stale `ws.timer` forever and silently refused to recover.
  const { app } = streaming();
  let sock = app.sockets.last;
  for (let i = 0; i < MAX_RETRIES; i++) {
    sock.serverClose(1006);
    app.clock.runNext();
    sock = app.sockets.last;
  }
  sock.serverClose(1006);                 // budget spent -> "Disconnected"
  assert.equal(app.connState(), "down");

  const before = app.sockets.instances.length;
  app.fireDocument("visibilitychange");
  assert.equal(app.sockets.instances.length, before + 1);
});

test("a rejected session is NOT retried when the tab regains focus", () => {
  // Regression: focus bypassed the 1008 stop and re-ran the rejection loop.
  const { app, sock } = streaming();
  sock.open();
  sock.serverClose(1008, "invalid token");

  const before = app.sockets.instances.length;
  app.fireDocument("visibilitychange");
  app.fireWindow("online");

  assert.equal(app.sockets.instances.length, before, "must stay stopped until re-auth");
  assert.equal(app.connText(), "Not authorized");
});

test("a completed stream is not silently re-run on focus or reconnect", () => {
  // Restarting a finished stream would re-invoke the model and bill for it.
  const { app, sock } = streaming();
  sock.open();
  sock.send({ type: "token", text: "bitti" });
  sock.serverClose(1000, "done");

  const before = app.sockets.instances.length;
  app.fireDocument("visibilitychange");
  app.fireWindow("online");

  assert.equal(app.sockets.instances.length, before);
  assert.equal(app.connText(), "Stream complete");
});

test("an explicit new stream clears a previous terminal stop", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.serverClose(1008, "invalid token");

  app.ctx.streamInsight("MSFT");           // user re-authenticated and retried
  assert.equal(app.connState(), "connecting");
  app.sockets.last.open();
  assert.equal(app.connState(), "live");
});

test("a hidden tab does not reconnect", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.serverClose(1006);
  app.clock.timers.clear();
  app.setVisibility("hidden");

  const before = app.sockets.instances.length;
  app.fireDocument("visibilitychange");
  assert.equal(app.sockets.instances.length, before);
});

test("a live socket is not duplicated when the tab regains focus", () => {
  const { app, sock } = streaming();
  sock.open();
  const before = app.sockets.instances.length;
  app.fireDocument("visibilitychange");
  assert.equal(app.sockets.instances.length, before, "must not open a second socket");
});

test("the network coming back triggers an immediate reconnect", () => {
  const { app, sock } = streaming();
  sock.open();
  sock.serverClose(1006);
  const before = app.sockets.instances.length;

  app.fireWindow("online");

  assert.equal(app.sockets.instances.length, before + 1);
  assert.equal(app.connState(), "connecting");
});

test("coming online after a manual teardown stays idle", () => {
  const { app, sock } = streaming();
  sock.open();
  app.ctx.wsTeardown();
  const before = app.sockets.instances.length;

  app.fireWindow("online");

  assert.equal(app.sockets.instances.length, before, "a deliberate stop must stay stopped");
});
