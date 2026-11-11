import { ArrowUp, Search } from "lucide-react";
import { Cite, Frame } from "./Frame";

const SOURCES = [
  { n: 1, where: "Q3 sales report.pdf, p. 2", text: "…North finished the quarter ahead of West and South…" },
  { n: 2, where: "Q3 sales report.pdf, p. 5", text: "…growth followed the distributor agreement signed in July…" },
];

export function ChatFigure() {
  return (
    <Frame
      title="Ask"
      label="Illustration of the chat screen: a question about a sales report, an answer with two numbered citations, and the source passages underneath."
      className="flex-[1.35_1_480px]"
      footer="Language model: llama3.2:3b, running on this device"
    >
      <div className="flex flex-col gap-[18px] p-5">
        <div className="max-w-[85%] self-end rounded-[14px] bg-[#1E2326] px-3.5 py-2.5 text-sm leading-normal">What were our Q3 sales by region?</div>

        <div className="flex items-start gap-3">
          <span aria-hidden className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-(--l-accent) text-(--l-accent-ink)">
            <Search className="h-3.5 w-3.5" strokeWidth={2.2} />
          </span>
          <p className="m-0 text-sm leading-[1.7] text-[#E3E5E1]">
            The North region led the quarter, ahead of West and South <Cite n={1} />. The report puts the growth down to the distributor agreement signed in July <Cite n={2} />.
            <span className="caret ml-1 inline-block h-[15px] w-[7px] bg-(--l-accent) align-[-2px]" aria-hidden />
          </p>
        </div>

        <div className="flex flex-wrap gap-2.5">
          {SOURCES.map((s) => (
            <div key={s.n} className="flex min-w-0 flex-[1_1_200px] flex-col gap-1.5 rounded-[10px] border border-(--l-line) bg-(--l-panel-2) p-3">
              <span className="flex items-center gap-2 text-xs font-medium">
                <Cite n={s.n} />
                {s.where}
              </span>
              <span className="text-xs leading-normal text-(--l-muted)">{s.text}</span>
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between gap-3 rounded-xl border border-[#2A3033] bg-[#0F1213] py-2.5 pl-3.5 pr-2.5">
          <span className="text-sm text-(--l-muted)">Ask about your documents</span>
          <span aria-hidden className="grid h-[34px] w-[34px] place-items-center rounded-[9px] bg-(--l-accent) text-(--l-accent-ink)">
            <ArrowUp className="h-[18px] w-[18px]" strokeWidth={2.2} />
          </span>
        </div>
      </div>
    </Frame>
  );
}
