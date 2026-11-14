import { Frame } from "./Frame";

// Mirrors the Models page: Ollama chat models from the companion's suggested
// list, plus the sentence-transformers model used for document search.
const CHAT_MODELS = [
  { name: "llama3.2:1b", detail: "1B parameters · 1.3 GB", state: "installed" },
  { name: "llama3.2:3b", detail: "3B parameters · 2.0 GB", state: "running" },
  { name: "llama3.1:8b", detail: "8B parameters · 4.7 GB", state: "download" },
] as const;

function Row({ name, detail, state }: { name: string; detail: string; state: "installed" | "running" | "download" }) {
  const active = state === "running";
  return (
    <div
      className={
        active
          ? "flex items-center gap-3.5 rounded-xl border border-(--l-accent) bg-[#12201E] px-4 py-3.5"
          : "flex items-center gap-3.5 rounded-xl border border-(--l-line) bg-[#151819] px-4 py-3.5"
      }
    >
      <span
        aria-hidden
        className={
          active
            ? "grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border-[1.5px] border-(--l-accent)"
            : "h-[18px] w-[18px] shrink-0 rounded-full border-[1.5px] border-[#3A4144]"
        }
      >
        {active && <span className="h-2 w-2 rounded-full bg-(--l-accent)" />}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="text-[15px] font-medium">{name}</span>
        <span className="text-xs text-(--l-muted)">{detail}</span>
      </span>
      {state === "installed" && <span className="text-xs text-(--l-muted)">Installed</span>}
      {state === "running" && <span className="rounded-full bg-(--l-cite-bg) px-2.5 py-1 text-xs font-medium text-(--l-cite-fg)">Running</span>}
      {state === "download" && <span className="rounded-lg border border-[#3A4144] px-3 py-[5px] text-xs font-medium">Download</span>}
    </div>
  );
}

export function ModelsFigure() {
  return (
    <Frame
      title="Models"
      label="Illustration of the models screen: three chat models of different sizes with llama3.2:3b running, and a separate search model for indexing files."
      className="flex-[1.35_1_480px]"
      footer="Running on this device. No connection needed."
    >
      <div className="flex flex-col gap-2.5 p-5">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-(--l-muted)">Chat model</span>
        {CHAT_MODELS.map((m) => (
          <Row key={m.name} {...m} />
        ))}
        <div className="h-2" />
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-(--l-muted)">Search model</span>
        <Row name="all-MiniLM-L6-v2" detail="Indexes and searches your files" state="installed" />
      </div>
    </Frame>
  );
}
