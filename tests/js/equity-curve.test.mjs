/**
 * The equity curve — drawn by hand, so its geometry is worth asserting.
 *
 * The property under test throughout is honesty about gaps. Snapshots are
 * captured only on days the user was active, so the curve is sparse; a span
 * nobody sampled must render as dashed and be described as unobserved, never
 * drawn as a solid line implying the book sat still. Everything else here
 * (scaling, the flat-book case, the degenerate one-point case) exists to keep
 * that drawing from lying in some other way.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadApp } from "./harness.mjs";

/** Evaluate the app, then call renderEquityCurve with a scripted report. */
function draw(report) {
  const app = loadApp();
  app.ctx.renderEquityCurve(report);
  return app.curve().innerHTML;
}

const point = (date, equity) => ({ date, equity });

function report(points, over = {}) {
  return {
    points,
    covered_days: points.length,
    span_days: points.length,
    sparse: false,
    start_equity: points.length ? points[0].equity : 0,
    current_equity: points.length ? points[points.length - 1].equity : 0,
    return_pct: 0,
    max_drawdown_pct: 0,
    ...over,
  };
}

const paths = (html) => html.match(/<path[^>]*>/g) || [];
const dots = (html) => html.match(/<circle[^>]*>/g) || [];

test("a curve is drawn once there are two points", () => {
  const html = draw(report([point("2026-08-01", 1000), point("2026-08-02", 1100)]));

  assert.match(html, /<svg/);
  assert.equal(paths(html).length, 1, "one segment between two points");
  assert.equal(dots(html).length, 2, "a marker on each observed day");
});

test("a single day states that the curve needs two, rather than drawing one", () => {
  const html = draw(report([point("2026-08-01", 1000)]));

  assert.doesNotMatch(html, /<svg/);
  assert.match(html, /appears once there are two/);
});

test("no history at all is an explicit empty state", () => {
  const html = draw(report([]));

  assert.doesNotMatch(html, /<svg/);
  assert.match(html, /No equity history yet/);
});

test("an unobserved span is dashed, not drawn as a flat hold", () => {
  const html = draw(report(
    [point("2026-08-01", 1000), point("2026-08-10", 1100)],
    { covered_days: 2, span_days: 10, sparse: true },
  ));

  const [segment] = paths(html);
  assert.match(segment, /is-gap/, "the nine-day gap must be dashed");
  assert.match(html, /never sampled, not flat/);
  assert.match(html, /2 of 10 days observed/);
});

test("consecutive days are drawn solid", () => {
  const html = draw(report([point("2026-08-01", 1000), point("2026-08-02", 1100)]));

  assert.doesNotMatch(paths(html)[0], /is-gap/);
});

test("only the unobserved spans of a mixed curve are dashed", () => {
  const html = draw(report(
    [point("2026-08-01", 1000), point("2026-08-02", 1050), point("2026-08-09", 1200)],
    { covered_days: 3, span_days: 9, sparse: true },
  ));

  const [first, second] = paths(html);
  assert.doesNotMatch(first, /is-gap/, "Aug 1 -> 2 was observed");
  assert.match(second, /is-gap/, "Aug 2 -> 9 was not");
});

test("points are placed by elapsed time, not by their index", () => {
  // Three points where the second sits one day after the first and seven
  // before the third: index spacing would put it in the middle, time does not.
  const html = draw(report(
    [point("2026-08-01", 1000), point("2026-08-02", 1000), point("2026-08-09", 1000)],
    { covered_days: 3, span_days: 9, sparse: true },
  ));

  const xs = dots(html).map((c) => Number(c.match(/cx="([\d.]+)"/)[1]));
  const [a, b, c] = xs;
  assert.ok(b - a < (c - b) / 4, `expected a time scale, got x=${xs}`);
});

test("a flat book is centred rather than pinned to the floor", () => {
  const html = draw(report([point("2026-08-01", 1000), point("2026-08-02", 1000)]));

  const ys = dots(html).map((c) => Number(c.match(/cy="([\d.]+)"/)[1]));
  assert.deepEqual(ys, [45, 45], "both points on the vertical midline");
});

test("the highest and lowest equity anchor the vertical extremes", () => {
  const html = draw(report([
    point("2026-08-01", 1000), point("2026-08-02", 2000), point("2026-08-03", 1500),
  ]));

  const ys = dots(html).map((c) => Number(c.match(/cy="([\d.]+)"/)[1]));
  assert.ok(ys[1] < ys[2] && ys[2] < ys[0], "higher equity draws higher (smaller y)");
});

test("a gain and a loss are coloured differently", () => {
  const pts = [point("2026-08-01", 1000), point("2026-08-02", 1100)];
  const up = draw(report(pts, { return_pct: 10 }));
  const down = draw(report(pts, { return_pct: -10 }));

  assert.match(paths(up)[0], /is-up/);
  assert.match(paths(down)[0], /is-down/);
});

test("the headline figures are shown alongside the drawing", () => {
  const html = draw(report(
    [point("2026-08-01", 1000), point("2026-08-02", 1250)],
    { return_pct: 25, start_equity: 1000, current_equity: 1250, max_drawdown_pct: 4.5 },
  ));

  assert.match(html, /\+25\.00%/);
  assert.match(html, /\$1,000\.00/);
  assert.match(html, /\$1,250\.00/);
  assert.match(html, /max drawdown 4\.50%/);
});

test("the drawing carries an accessible label", () => {
  const html = draw(report(
    [point("2026-08-01", 1000), point("2026-08-02", 1100)], { return_pct: 10 },
  ));

  assert.match(html, /role="img"/);
  assert.match(html, /aria-label="Equity curve, \+10\.00 percent, 2 of 2 days observed"/);
});

test("each marker names its date and value for hover", () => {
  const html = draw(report([point("2026-08-01", 1000), point("2026-08-02", 1100)]));

  assert.match(html, /<title>2026-08-01 · \$1,000\.00<\/title>/);
});

test("a date from the payload cannot inject markup", () => {
  const html = draw(report([
    point("<img src=x onerror=alert(1)>", 1000), point("2026-08-02", 1100),
  ]));

  assert.doesNotMatch(html, /<img/, "the date is escaped inside the marker title");
});
