import { useState } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDown, Database, FileAudio, FileSpreadsheet, FileText, Mail, MessageCircle } from "lucide-react";
import type { Citation } from "@/lib/api";
import { cn } from "@/lib/utils";

// [3] → [3](cite:3) so the markdown renderer hands us a link we can turn
// into a citation pill. Skips "[3](" (already a link) and "[3]:" (ref def).
function linkCitations(markdown: string): string {
  return markdown.replace(/\[(\d{1,3})\](?![(:])/g, "[$1](cite:$1)");
}

const urlTransform = (url: string) => (url.startsWith("cite:") ? url : defaultUrlTransform(url));

/**
 * Renders an answer as Markdown. Raw HTML in the model output is NOT
 * rendered (react-markdown escapes it), so a document can't inject script
 * into the page through the answer.
 */
export function AnswerMarkdown({ text, citations, onCite, streaming }: { text: string; citations: Citation[]; onCite: (n: number) => void; streaming?: boolean }) {
  const known = new Set(citations.map((c) => c.index));
  return (
    <div className={cn("prose-answer", streaming && "typing-cursor")}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={urlTransform}
        components={{
          a({ href, children }) {
            if (href?.startsWith("cite:")) {
              const n = Number(href.slice(5));
              if (!known.has(n)) return <span className="text-zinc-400">[{n}]</span>;
              return (
                <button
                  type="button"
                  onClick={() => onCite(n)}
                  className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-md bg-brand-50 px-1 align-text-top text-[11px] font-semibold text-brand-700 hover:bg-brand-100 dark:bg-brand-500/20 dark:text-brand-100"
                  aria-label={`Show source ${n}`}
                >
                  {n}
                </button>
              );
            }
            return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
          },
        }}
      >
        {linkCitations(text)}
      </ReactMarkdown>
    </div>
  );
}

function SourceIcon({ type }: { type: string }) {
  const cls = "h-4 w-4 shrink-0";
  if (type === "structured_db") return <Database className={cls} />;
  if (["excel", "csv"].includes(type)) return <FileSpreadsheet className={cls} />;
  if (["email", "mbox", "eml", "pst"].includes(type)) return <Mail className={cls} />;
  if (["whatsapp", "telegram", "slack", "teams"].includes(type)) return <MessageCircle className={cls} />;
  if (type === "audio") return <FileAudio className={cls} />;
  return <FileText className={cls} />;
}

export const sourceId = (messageId: string, index: number) => `source-${messageId}-${index}`;

export function SourceList({ messageId, citations, highlighted }: { messageId: string; citations: Citation[]; highlighted: number | null }) {
  if (!citations.length) return null;
  return (
    <div className="mt-4">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">Sources</p>
      <ul className="grid gap-2 sm:grid-cols-2">
        {citations.map((c) => (
          <SourceCard key={c.index} id={sourceId(messageId, c.index)} citation={c} highlighted={c.index === highlighted} />
        ))}
      </ul>
    </div>
  );
}

function SourceCard({ id, citation: c, highlighted }: { id: string; citation: Citation; highlighted: boolean }) {
  const [open, setOpen] = useState(false);
  const expanded = open || highlighted;
  return (
    <li
      id={id}
      className={cn(
        "rounded-lg border text-sm transition-colors",
        highlighted ? "border-brand-500 bg-brand-50/50 dark:bg-brand-500/10" : "border-zinc-200 dark:border-zinc-800",
        expanded && "sm:col-span-2",
      )}
    >
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-2 text-left" aria-expanded={expanded}>
        <span className="grid h-5 min-w-5 place-items-center rounded-md bg-zinc-100 px-1 text-[11px] font-semibold dark:bg-zinc-800">{c.index}</span>
        <SourceIcon type={c.source_type} />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{c.file_name}</span>
          <span className="block truncate text-xs text-zinc-500">
            {[c.location, c.sender].filter(Boolean).join(" · ")}
          </span>
        </span>
        <ChevronDown className={cn("h-4 w-4 shrink-0 text-zinc-400 transition-transform", expanded && "rotate-180")} />
      </button>
      {expanded && (
        <p className="max-h-64 overflow-y-auto whitespace-pre-wrap border-t border-zinc-100 px-3 py-2 text-[13px] leading-6 text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
          {c.full_text}
        </p>
      )}
    </li>
  );
}
