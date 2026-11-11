import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Frame } from "./Frame";

/**
 * A small, self-contained version of the Knowledge graph page: clusters of
 * related entities, drawn over a faint starfield. Click a cluster to open it,
 * then an entity to go one level deeper. Positions are computed from the
 * mockup's layout rules and tweened in JS so lines, dots and labels move
 * together.
 */

const TEAL = "#4FC3AE";
const BLUE = "#7FB2F0";
const PINK = "#EE8FB0";
const ORANGE = "#E8A07E";
const YELLOW = "#E3C35A";
const PURPLE = "#B79CF2";

type Entity = string | [string, string[]];
type Cluster = { id: string; lab: string; col: string; bx: number; by: number; items: Entity[] };

const CLUSTERS: Cluster[] = [
  { id: "m1", lab: "Sales", col: TEAL, bx: 430, by: 120, items: [["Q3 pipeline", ["Forecast by region", "Deal stages"]], "Pricing tiers", ["Discount policy", ["Approval limits", "Exceptions"]], "Key accounts", "Win/loss notes"] },
  { id: "m2", lab: "Contracts", col: TEAL, bx: 190, by: 150, items: ["Vendor agreements", "NDAs", "Renewal dates", "Liability clauses"] },
  { id: "m3", lab: "Hiring", col: BLUE, bx: 120, by: 320, items: ["Job descriptions", "Interview scorecards", ["Offer letters", ["Salary bands", "Start dates"]], "Onboarding"] },
  { id: "m4", lab: "Finance", col: PINK, bx: 330, by: 420, items: ["Budget 2027", "Expense policy", ["Invoices", ["Overdue", "Payment terms"]], "Audit notes"] },
  { id: "m5", lab: "Product", col: ORANGE, bx: 560, by: 290, items: ["Roadmap", ["Release notes", ["Version 2.4", "Version 2.5"]], "Bug reports", "User research"] },
  { id: "m6", lab: "Support", col: YELLOW, bx: 730, by: 140, items: ["Refund requests", "Escalations", ["Help articles", ["Setup guides", "Troubleshooting"]], "Response times"] },
  { id: "m7", lab: "Compliance", col: PURPLE, bx: 760, by: 400, items: ["Data retention", "Security review", "Privacy policy"] },
  { id: "m8", lab: "Operations", col: BLUE, bx: 110, by: 480, items: ["Supplier list", "Inventory", "Shipping"] },
];
const EDGES: [string, string][] = [
  ["m1", "m2"], ["m1", "m5"], ["m2", "m3"], ["m3", "m4"], ["m4", "m5"], ["m4", "m8"], ["m5", "m6"], ["m6", "m7"], ["m5", "m7"], ["m3", "m8"],
];

type GNode = { id: string; par: string | null; lv: number; lab: string; col: string; bx: number; by: number };

const NODES: GNode[] = [];
for (const c of CLUSTERS) {
  NODES.push({ id: c.id, par: null, lv: 0, lab: c.lab, col: c.col, bx: c.bx, by: c.by });
  c.items.forEach((item, i) => {
    const [lab, kids] = typeof item === "string" ? [item, []] : item;
    const sid = `${c.id}s${i}`;
    NODES.push({ id: sid, par: c.id, lv: 1, lab, col: c.col, bx: 0, by: 0 });
    kids.forEach((k, j) => NODES.push({ id: `${sid}k${j}`, par: sid, lv: 2, lab: k, col: c.col, bx: 0, by: 0 }));
  });
}
NODES.sort((a, b) => a.lv - b.lv);
const BY: Record<string, GNode> = Object.fromEntries(NODES.map((n) => [n.id, n]));
const MAINS = NODES.filter((n) => n.lv === 0);
const childrenOf = (id: string) => NODES.filter((n) => n.par === id);

type Pos = { x: number; y: number; r: number; op: number; lop: number; ly: number; halo: number; dl: number };
type Layout = Record<string, Pos>;

const clamp = (lo: number, hi: number, v: number) => Math.max(lo, Math.min(hi, v));

function ring(cx: number, cy: number, k: number, R: number, face: number, arc: number): [number, number][] {
  const pts: [number, number][] = [];
  for (let i = 0; i < k; i++) {
    const a = k === 1 ? face : face + arc * (i / (k - 1) - 0.5);
    pts.push([cx + R * Math.cos(a), cy + R * Math.sin(a)]);
  }
  return pts;
}

/** Fan `k` points around (cx, cy), facing whichever way keeps them inside the frame and clear of `others`. */
function bestRing(cx: number, cy: number, k: number, R: number, others: [number, number][]): [number, number][] {
  if (k === 0) return [];
  const arc = (Math.min(300, 62 * k) * Math.PI) / 180;
  let best: [number, number][] = [];
  let bestScore = Infinity;
  for (let f = 0; f < 12; f++) {
    const pts = ring(cx, cy, k, R, (f * Math.PI) / 6, arc);
    let score = 0;
    for (const p of pts) {
      const x = clamp(46, 854, p[0]);
      const y = clamp(44, 516, p[1]);
      score += (Math.abs(x - p[0]) + Math.abs(y - p[1])) * 6;
      for (const o of others) {
        const d = Math.hypot(o[0] - p[0], o[1] - p[1]);
        if (d < 70) score += (70 - d) * 3;
      }
    }
    if (score < bestScore) {
      bestScore = score;
      best = pts;
    }
  }
  return best.map((p) => [clamp(46, 854, p[0]), clamp(44, 516, p[1])]);
}

function layoutFor(exp: string[]): Layout {
  const out: Layout = {};
  const sel = exp[0] ?? null;
  const mainPos: Record<string, [number, number]> = {};

  for (const n of MAINS) {
    let x = n.bx;
    let y = n.by;
    if (sel && n.id !== sel) {
      // Nudge the other clusters away from the open one to make room.
      const s = BY[sel];
      const dx = n.bx - s.bx;
      const dy = n.by - s.by;
      const d = Math.hypot(dx, dy) || 1;
      const k = Math.max(0, 1 - d / 560) * 90;
      x = clamp(40, 860, n.bx + (dx / d) * k);
      y = clamp(40, 520, n.by + (dy / d) * k);
    }
    mainPos[n.id] = [x, y];
    const isSel = sel === n.id;
    const dim = sel && !isSel ? 0.3 : 1;
    out[n.id] = { x, y, r: isSel ? 12.5 : 9, op: dim, lop: dim, ly: y + (isSel ? 27 : 23), halo: isSel ? 1 : 0, dl: 0 };
  }
  for (const n of NODES) {
    if (n.lv === 0) continue;
    const p = out[n.par!];
    out[n.id] = { x: p.x, y: p.y, r: n.lv === 1 ? 5.5 : 4.5, op: 0, lop: 0, ly: p.y + 17, halo: 0, dl: 0 };
  }

  if (!sel) return out;
  const place = (n: GNode, pt: [number, number], i: number, r: number) => {
    out[n.id] = { x: pt[0], y: pt[1], r, op: 1, lop: 1, ly: pt[1] + r + 12, halo: 0, dl: 0.05 + i * 0.07 };
  };
  const m = mainPos[sel];
  const subs = childrenOf(sel);
  const others = MAINS.filter((n) => n.id !== sel).map((n) => mainPos[n.id]);
  bestRing(m[0], m[1], subs.length, 98, others).forEach((pt, i) => place(subs[i], pt, i, 5.5));

  const second = exp[1];
  if (second) {
    const sp = out[second];
    for (const s of subs) {
      if (s.id === second) continue;
      const o = out[s.id];
      const dx = o.x - sp.x;
      const dy = o.y - sp.y;
      const d = Math.hypot(dx, dy) || 1;
      const k = Math.max(0, 1 - d / 160) * 36;
      o.x = clamp(46, 854, o.x + (dx / d) * k);
      o.y = clamp(44, 516, o.y + (dy / d) * k);
      o.ly = o.y + 17;
    }
    const kids = childrenOf(second);
    const around: [number, number][] = subs.filter((s) => s.id !== second).map((s) => [out[s.id].x, out[s.id].y]);
    around.push(m);
    bestRing(sp.x, sp.y, kids.length, 58, around).forEach((pt, i) => place(kids[i], pt, i, 4.5));
    sp.r = 7.5;
  }
  return out;
}

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
function lerpPos(a: Pos, b: Pos, t: number): Pos {
  return { x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t), r: lerp(a.r, b.r, t), op: lerp(a.op, b.op, t), lop: lerp(a.lop, b.lop, t), ly: lerp(a.ly, b.ly, t), halo: lerp(a.halo, b.halo, t), dl: b.dl };
}

// ── Starfield (deterministic, so the picture is the same on every visit) ──
function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const PALETTE = [TEAL, BLUE, PINK, ORANGE, YELLOW, PURPLE];

function makeStarfield() {
  const rnd = mulberry32(11);
  const stars = Array.from({ length: 80 }, () => ({
    x: 20 + rnd() * 860,
    y: 30 + rnd() * 500,
    r: [1.6, 2, 2.4, 3][Math.floor(rnd() * 4)],
    col: PALETTE[Math.floor(rnd() * PALETTE.length)],
    op: [0.5, 0.7, 0.9][Math.floor(rnd() * 3)],
    delay: (rnd() * 4).toFixed(1),
  }));
  const seen = new Set<string>();
  const paths: string[] = [];
  stars.forEach((s, i) => {
    const near = stars
      .map((o, j) => ({ j, d: Math.hypot(o.x - s.x, o.y - s.y) }))
      .filter((o) => o.j !== i && o.d < 120)
      .sort((a, b) => a.d - b.d)
      .slice(0, 1 + Math.floor(rnd() * 2));
    for (const { j } of near) {
      const key = i < j ? `${i}-${j}` : `${j}-${i}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const o = stars[j];
      const mx = (s.x + o.x) / 2;
      const my = (s.y + o.y) / 2;
      const nx = -(o.y - s.y);
      const ny = o.x - s.x;
      const len = Math.hypot(nx, ny) || 1;
      const bend = (rnd() - 0.5) * 40;
      paths.push(`M${s.x.toFixed(0)} ${s.y.toFixed(0)} Q${(mx + (nx / len) * bend).toFixed(0)} ${(my + (ny / len) * bend).toFixed(0)} ${o.x.toFixed(0)} ${o.y.toFixed(0)}`);
    }
  });
  return { stars, paths };
}

export function GraphFigure() {
  const [exp, setExp] = useState<string[]>([]);
  const [pos, setPos] = useState<Layout>(() => layoutFor([]));
  const posRef = useRef(pos);
  posRef.current = pos;
  const starfield = useMemo(makeStarfield, []);
  const expKey = exp.join("/");

  useEffect(() => {
    const target = layoutFor(expKey ? expKey.split("/") : []);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setPos(target);
      return;
    }
    const from = posRef.current;
    const t0 = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const elapsed = (now - t0) / 1000;
      let done = true;
      const next: Layout = {};
      for (const id in target) {
        const b = target[id];
        const t = clamp(0, 1, (elapsed - b.dl) / 0.62);
        if (t < 1) done = false;
        next[id] = lerpPos(from[id], b, 1 - (1 - t) ** 3);
      }
      setPos(next);
      if (!done) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [expKey]);

  const sel = exp[0] ?? null;
  const pick = (n: GNode) => {
    if (n.lv === 0) setExp(exp[0] === n.id ? [] : [n.id]);
    else if (n.lv === 1) setExp(exp[1] === n.id ? [n.par!] : [n.par!, n.id]);
  };
  const onKey = (n: GNode) => (e: KeyboardEvent<SVGGElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      pick(n);
    }
  };

  const focus = exp.length ? BY[exp[exp.length - 1]] : null;
  const focusKids = focus ? childrenOf(focus.id).length : 0;
  const status = !focus
    ? "Select a cluster to see what's in it"
    : focus.lv === 0
      ? `${focus.lab}: ${focusKids} related entities. Select one to go deeper.`
      : focusKids
        ? `${focus.lab}, in ${BY[focus.par!].lab}: ${focusKids} linked passages`
        : `${focus.lab}, in ${BY[focus.par!].lab}`;

  return (
    <Frame
      title="Knowledge graph"
      className="mx-auto w-full max-w-[860px]"
      footer={
        <div className="flex min-h-7 items-center justify-between gap-3">
          <span aria-live="polite" className="min-w-0 text-[13px] text-(--l-text-2)">{status}</span>
          {exp.length > 0 && (
            <button
              type="button"
              onClick={() => setExp([])}
              className="shrink-0 rounded-lg border border-[#3A4144] px-3 py-1 text-xs font-medium text-(--l-text) hover:bg-[#1E2325]"
            >
              Show all
            </button>
          )}
        </div>
      }
    >
      <div className="overflow-x-auto">
        <svg viewBox="0 0 900 560" className="block min-w-[640px] w-full select-none" fontFamily="inherit">
          <g aria-hidden>
            {starfield.paths.map((d, i) => (
              <path key={i} d={d} fill="none" stroke="#3F474A" strokeWidth="1" opacity="0.55" vectorEffect="non-scaling-stroke" />
            ))}
            {starfield.stars.map((s, i) => (
              <circle key={i} className="tw" cx={s.x} cy={s.y} r={s.r} fill={s.col} opacity={s.op} style={{ animationDelay: `${s.delay}s` }} />
            ))}
          </g>

          <g aria-hidden>
            {EDGES.map(([a, b]) => {
              const A = pos[a];
              const B = pos[b];
              const hot = sel !== null && (a === sel || b === sel);
              return (
                <line
                  key={`${a}-${b}`}
                  x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                  className={hot ? "flow" : undefined}
                  stroke={hot ? BY[sel!].col : "#5A6367"}
                  strokeOpacity={hot ? 0.9 : sel ? 0.25 : 0.7}
                  strokeWidth={hot ? 1.5 : 1}
                  vectorEffect="non-scaling-stroke"
                />
              );
            })}
            {NODES.filter((n) => n.lv > 0 && pos[n.id].op > 0.01).map((n) => {
              const P = pos[n.par!];
              const C = pos[n.id];
              return <line key={n.id} x1={P.x} y1={P.y} x2={C.x} y2={C.y} stroke={n.col} strokeOpacity={C.op * 0.6} strokeWidth={1} vectorEffect="non-scaling-stroke" />;
            })}
          </g>

          {NODES.map((n) => {
            const p = pos[n.id];
            const visible = p.op > 0.01;
            const pressed = exp.includes(n.id);
            return (
              <g
                key={n.id}
                className="graph-node"
                transform={`translate(${p.x} ${p.y})`}
                role="button"
                tabIndex={visible && n.lv < 2 ? 0 : -1}
                aria-label={n.lab}
                aria-pressed={pressed}
                pointerEvents={visible ? "auto" : "none"}
                onClick={() => pick(n)}
                onKeyDown={onKey(n)}
              >
                <circle className="graph-hit" r={Math.max(16, p.r + 8)} fill="transparent" stroke="transparent" strokeWidth={1} />
                {p.halo > 0.01 && <circle className="gring" r={p.r + 6} fill="none" stroke={n.col} strokeOpacity={p.halo} />}
                <circle r={p.r} fill={n.col} fillOpacity={p.op} />
                <text y={p.ly - p.y} textAnchor="middle" fontSize={n.lv === 0 ? 12 : 11} fill="var(--l-text-2)" fillOpacity={p.lop}>
                  {n.lab}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </Frame>
  );
}
