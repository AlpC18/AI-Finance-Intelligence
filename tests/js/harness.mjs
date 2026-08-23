/**
 * A dependency-free browser harness for unit-testing the real web/app.js.
 *
 * app.js ships as a plain browser script, not a module, so rather than
 * refactoring production code to be testable (or pulling in jsdom) we evaluate
 * the actual shipped file inside a `node:vm` context backed by the smallest DOM,
 * WebSocket, timer and storage stubs it needs. What gets tested is therefore the
 * byte-for-byte artifact the browser loads.
 *
 * Two deliberate design points:
 *   - `document.querySelector` auto-vivifies elements, so app.js's 16 top-level
 *     `$("#id").addEventListener(...)` calls bind without a full HTML fixture;
 *   - timers are virtual, so exponential backoff is asserted exactly rather than
 *     waited on, keeping the suite instant and deterministic.
 *
 * Only `function` declarations reach the vm global (const/let stay lexical), so
 * tests drive the state machine through `streamInsight`/`wsTeardown` and observe
 * it through the DOM — behaviour, never internals.
 */
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const APP_JS = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), "../../web/app.js"
);

class ClassList {
  constructor(initial = "") { this._set = new Set(String(initial).split(/\s+/).filter(Boolean)); }
  add(...c) { c.forEach((x) => x && this._set.add(x)); }
  remove(...c) { c.forEach((x) => this._set.delete(x)); }
  contains(c) { return this._set.has(c); }
  toggle(c, force) {
    const on = force === undefined ? !this._set.has(c) : !!force;
    on ? this._set.add(c) : this._set.delete(c);
    return on;
  }
  toString() { return [...this._set].join(" "); }
}

class El {
  constructor(tag = "div") {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parent = null;
    this.classList = new ClassList();
    this.dataset = {};
    this.listeners = new Map();
    this._text = "";
    this._html = "";
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.type = "";
  }
  get className() { return this.classList.toString(); }
  set className(v) { this.classList = new ClassList(v); }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); this._html = ""; this.children = []; }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this._text = ""; this.children = []; }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }
  dispatch(type, ev = {}) { (this.listeners.get(type) || []).forEach((fn) => fn(ev)); }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  removeChild(child) { this.children = this.children.filter((c) => c !== child); }
  remove() { if (this.parent) this.parent.removeChild(this); }
  /** Class-selector lookup over appended children (all app.js needs). */
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const want = String(sel).replace(/^\./, "");
    return this.children.filter((c) => c.classList.contains(want));
  }
}

class FakeSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.closed = false;
    this.onopen = this.onmessage = this.onerror = this.onclose = null;
    FakeSocket.instances.push(this);
  }
  /** Simulated server side. */
  open() { this.readyState = 1; this.onopen?.({}); }
  send(frame) { this.onmessage?.({ data: JSON.stringify(frame) }); }
  sendRaw(data) { this.onmessage?.({ data }); }
  error() { this.onerror?.({}); }
  /** Server- or network-initiated close. */
  serverClose(code = 1006, reason = "") {
    this.readyState = 3; this.closed = true; this.onclose?.({ code, reason });
  }
  /** Client-initiated close, as app.js calls it. */
  close() { this.readyState = 3; this.closed = true; this.onclose?.({ code: 1000, reason: "" }); }
  static reset() { FakeSocket.instances = []; }
  static get last() { return FakeSocket.instances[FakeSocket.instances.length - 1]; }
}
FakeSocket.instances = [];

/** Virtual clock: backoff is asserted, not slept through. */
class Clock {
  constructor() { this.timers = new Map(); this.seq = 0; this.now = 0; }
  setTimeout(fn, delay = 0) {
    const id = ++this.seq;
    this.timers.set(id, { fn, at: this.now + delay, delay });
    return id;
  }
  clearTimeout(id) { this.timers.delete(id); }
  /** Delays of every timer currently pending, in scheduling order. */
  pending() { return [...this.timers.values()].map((t) => t.delay); }
  /** Run every timer due at or before now+ms. */
  advance(ms) {
    this.now += ms;
    const due = [...this.timers.entries()]
      .filter(([, t]) => t.at <= this.now)
      .sort((a, b) => a[1].at - b[1].at);
    for (const [id, t] of due) { this.timers.delete(id); t.fn(); }
  }
  /** Fire the single pending non-toast timer (the reconnect timer). */
  runNext() {
    const entries = [...this.timers.entries()].sort((a, b) => a[1].at - b[1].at);
    if (!entries.length) return false;
    const [id, t] = entries[0];
    this.timers.delete(id);
    this.now = t.at;
    t.fn();
    return true;
  }
}

export function loadApp({ token = "test-token", routes = {} } = {}) {
  FakeSocket.reset();
  const clock = new Clock();
  const elements = new Map();
  const storage = new Map();
  if (token) storage.set("afi_token", token);

  const getEl = (sel) => {
    if (!elements.has(sel)) elements.set(sel, new El("div"));
    return elements.get(sel);
  };

  const documentListeners = new Map();
  const windowListeners = new Map();
  const addTo = (map) => (type, fn) => {
    if (!map.has(type)) map.set(type, []);
    map.get(type).push(fn);
  };

  const document = {
    visibilityState: "visible",
    querySelector: getEl,
    querySelectorAll: () => [],
    createElement: (tag) => new El(tag),
    addEventListener: addTo(documentListeners),
    body: new El("body"),
  };

  const sandbox = {
    document,
    console,
    WebSocket: FakeSocket,
    setTimeout: (fn, d) => clock.setTimeout(fn, d),
    clearTimeout: (id) => clock.clearTimeout(id),
    setInterval: () => 0,
    clearInterval: () => {},
    location: { protocol: "http:", host: "localhost:8000" },
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: (k) => storage.delete(k),
    },
    // Routes let a test script one endpoint's payload; anything unrouted keeps
    // the old inert {} so existing tests are unaffected.
    fetch: async (url) => {
      const key = Object.keys(routes).find((r) => String(url).startsWith(r));
      const body = key ? routes[key] : {};
      if (body instanceof Error) throw body;
      return {
        ok: true, status: 200,
        json: async () => body,
        text: async () => JSON.stringify(body),
      };
    },
    addEventListener: addTo(windowListeners),
    navigator: { onLine: true },
    encodeURIComponent,
    decodeURIComponent,
    JSON, Math, Date, Number, String, Object, Array, Promise, Error, RegExp, isNaN,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  const context = vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(APP_JS, "utf8"), context, { filename: "app.js" });

  return {
    ctx: sandbox,
    clock,
    sockets: FakeSocket,
    el: getEl,
    /** Text of the connection pill. */
    connText: () => getEl("#ws-conn-text").textContent,
    /** State class on the pill, e.g. "live" / "retrying" / "down". */
    connState: () => {
      const cls = getEl("#ws-conn").className.split(/\s+/).find((c) => c.startsWith("is-"));
      return cls ? cls.slice(3) : null;
    },
    /** The action button rendered inside the pill, if any. */
    connAction: () => getEl("#ws-conn").querySelector(".conn-act"),
    stream: () => getEl("#desk-stream"),
    curve: () => getEl("#pf-curve"),
    fireDocument: (type, ev = {}) => (documentListeners.get(type) || []).forEach((f) => f(ev)),
    fireWindow: (type, ev = {}) => (windowListeners.get(type) || []).forEach((f) => f(ev)),
    setVisibility: (v) => { document.visibilityState = v; },
  };
}
