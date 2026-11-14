import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Hero decoration: example questions pop up, a line draws down to the file
 * that answers them, and the file lights up. Two run at a time, picked at
 * random, one from each half of the list so they never share a file slot.
 */
const CHIPS = [
  { k: "k1", left: "14%", top: 215, text: "What were our Q3 sales by region?", path: "M140,257 C140,440 340,420 340,592", file: "Q3 sales report.pdf", node: 34 },
  { k: "k2", left: "62%", top: 195, text: "Summarise chapter 3 of my notes", path: "M620,237 C620,407 580,457 580,592", file: "Thermodynamics notes.pdf", node: 58 },
  { k: "k3", left: "84%", top: 170, text: "What is the notice period in my lease?", path: "M840,212 C840,396 780,472 780,592", file: "Rental agreement.pdf", node: 78 },
  { k: "k4", left: "26%", top: 140, text: "Which paper mentions heat loss?", path: "M260,182 C260,368 340,454 340,592", file: "Literature review.docx", node: 34 },
  { k: "k5", left: "38%", top: 215, text: "What did we decide on Monday?", path: "M380,257 C380,408 580,441 580,592", file: "Meeting notes.docx", node: 58 },
  { k: "k6", left: "80%", top: 225, text: "How do I reset the washing machine?", path: "M800,267 C800,430 780,480 780,592", file: "Appliance manual.pdf", node: 78 },
];
const GROUPS = [
  [0, 3, 4],
  [1, 2, 5],
];
const BASE_DURATION = 4.4;

type Item = { on: boolean; dur: number };

export function HeroDeco() {
  const [items, setItems] = useState<Item[]>(() => CHIPS.map((_, i) => ({ on: i < 2, dur: BASE_DURATION })));

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const current = [0, 1];
    const timers: number[] = [];

    const later = (slot: number, ms: number) => {
      timers.push(window.setTimeout(() => run(slot), ms));
    };
    const run = (slot: number) => {
      const other = current[1 - slot];
      const options = GROUPS[slot].filter((i) => i !== current[slot] && CHIPS[i].node !== CHIPS[other].node);
      const pick = options[Math.floor(Math.random() * options.length)];
      const dur = Math.round((3.6 + Math.random() * 1.8) * 10) / 10;
      const prev = current[slot];
      current[slot] = pick;
      setItems((old) => old.map((it, i) => (i === prev ? { on: false, dur: it.dur } : i === pick ? { on: true, dur } : it)));
      later(slot, dur * 1000 + 150 + Math.random() * 1100);
    };

    later(0, BASE_DURATION * 1000 + 250 + Math.random() * 800);
    later(1, BASE_DURATION * 1000 + 900 + Math.random() * 900);
    return () => timers.forEach((t) => clearTimeout(t));
  }, []);

  const state = (i: number) => ({ cls: items[i].on ? "on" : "off", style: { animationDuration: `${items[i].dur}s` } });

  return (
    <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 z-0 hidden h-[820px] md:block">
      <svg viewBox="0 0 1000 820" preserveAspectRatio="none" width="100%" height="820" fill="none" className="absolute left-0 top-0">
        {CHIPS.map((c, i) => (
          <path
            key={c.k}
            className={cn("cyc-line it", c.k, state(i).cls)}
            style={state(i).style}
            d={c.path}
            strokeWidth="1.25"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>

      {CHIPS.map((c, i) => (
        <div key={c.k} className="absolute -translate-x-1/2" style={{ left: c.left, top: c.top }}>
          <div
            className={cn("cyc-chip it whitespace-nowrap rounded-[10px] border px-4 py-2.5 font-mono text-xs", c.k, state(i).cls)}
            style={state(i).style}
          >
            {c.text}
          </div>
        </div>
      ))}

      {CHIPS.map((c, i) => (
        <div
          key={c.k}
          className={cn("cyc-wrap it absolute top-[592px] flex -translate-x-1/2 flex-col items-center gap-2.5", c.k, state(i).cls)}
          style={{ ...state(i).style, left: `${c.node}%` }}
        >
          <span className="cyc-circle relative grid h-14 w-14 place-items-center rounded-full border" style={state(i).style}>
            <span className="cyc-ring absolute -inset-px rounded-full border" style={state(i).style} />
            <FileText className="h-[22px] w-[22px]" strokeWidth={1.75} />
          </span>
          <span className="whitespace-nowrap font-mono text-xs text-[#B8BFBB]">{c.file}</span>
        </div>
      ))}
    </div>
  );
}
