import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowUp, Database, MessageSquare, RotateCcw, Square, Upload } from "lucide-react";
import { AnswerMarkdown, SourceList } from "@/components/Answer";
import { Alert, Button, EmptyState, Spinner } from "@/components/ui";
import type { Answer, ChatTurn } from "@/lib/api";
import { useApi } from "@/lib/connection";
import { useSetupStatus } from "@/lib/queries";
import { useWorkspace } from "@/lib/workspace";
import { cn } from "@/lib/utils";

type Message =
  | { id: string; role: "user"; text: string }
  | {
      id: string;
      role: "assistant";
      text: string;
      stage: string | null;
      answer: Answer | null;
      error: string | null;
      pending: boolean;
    };

const STAGE_LABELS: Record<string, string> = {
  understanding: "Understanding your question…",
  searching: "Searching your documents…",
  generating: "Writing the answer…",
};

const EXAMPLES = [
  "Summarize the main points across my documents",
  "What decisions were made, and who made them?",
  "List every amount or deadline that's mentioned",
];

const NOT_FOUND = "Not found in documents.";
const storageKey = (ws: string) => `shakti.chat.${ws}`;

function loadMessages(ws: string): Message[] {
  try {
    const saved: Message[] = JSON.parse(localStorage.getItem(storageKey(ws)) || "[]");
    return saved.filter((m) => m.role === "user" || !m.pending);
  } catch {
    return [];
  }
}

export default function ChatPage() {
  const api = useApi();
  const { workspace } = useWorkspace();
  const status = useSetupStatus();
  const docs = useQuery({ queryKey: ["documents", workspace], queryFn: () => api.documents(workspace) });

  const [messages, setMessages] = useState<Message[]>(() => loadMessages(workspace));
  const [input, setInput] = useState("");
  const [model, setModel] = useState<string>("");
  const [highlight, setHighlight] = useState<{ msg: string; n: number } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Switching workspace switches conversation.
  useEffect(() => {
    abortRef.current?.abort();
    setMessages(loadMessages(workspace));
  }, [workspace]);

  useEffect(() => {
    localStorage.setItem(storageKey(workspace), JSON.stringify(messages.slice(-60)));
  }, [messages, workspace]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const busy = messages.some((m) => m.role === "assistant" && m.pending);
  const ready = !!status.data?.running && !!status.data?.chat_model_installed;
  const models = status.data?.models ?? [];
  const activeModel = model || status.data?.chat_model || "";

  const patch = (id: string, fn: (m: Extract<Message, { role: "assistant" }>) => Partial<Extract<Message, { role: "assistant" }>>) =>
    setMessages((prev) => prev.map((m) => (m.id === id && m.role === "assistant" ? { ...m, ...fn(m) } : m)));

  async function send(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    const history: ChatTurn[] = messages
      .slice(-6)
      .map((m) => (m.role === "user" ? { role: "user" as const, content: m.text } : { role: "assistant" as const, content: m.answer?.answer ?? m.text }))
      .filter((t) => t.content);
    const userMsg: Message = { id: crypto.randomUUID(), role: "user", text: q };
    const id = crypto.randomUUID();
    setMessages((prev) => [...prev, userMsg, { id, role: "assistant", text: "", stage: "searching", answer: null, error: null, pending: true }]);
    setInput("");

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await api.ask(
        { query: q, workspace_id: workspace, model: activeModel || undefined, history },
        {
          onStatus: (s) => patch(id, () => ({ stage: s.stage })),
          onToken: (t) => patch(id, (m) => ({ text: m.text + t, stage: null })),
          onDone: (a) => patch(id, () => ({ answer: a, text: a.answer, pending: false, stage: null })),
          onError: (msg) => patch(id, () => ({ error: msg, pending: false, stage: null })),
        },
        controller.signal,
      );
    } catch (e) {
      const aborted = controller.signal.aborted;
      patch(id, (m) => ({ pending: false, stage: null, error: aborted ? (m.text ? null : "Stopped.") : (e as Error).message }));
    } finally {
      abortRef.current = null;
      inputRef.current?.focus();
    }
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(input);
    }
  };

  const noDocs = docs.data && docs.data.documents.length === 0;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-zinc-200 px-4 py-2.5 sm:px-6 dark:border-zinc-800">
        <p className="truncate text-sm text-zinc-500">
          Asking <strong className="text-zinc-900 dark:text-zinc-100">{workspace}</strong>
          {docs.data ? ` · ${docs.data.documents.length} document${docs.data.documents.length === 1 ? "" : "s"}` : ""}
        </p>
        <div className="flex items-center gap-2">
          {models.length > 0 && (
            <select
              aria-label="Model"
              value={activeModel}
              onChange={(e) => setModel(e.target.value)}
              className="h-8 max-w-48 rounded-lg border border-zinc-300 bg-white px-2 text-xs dark:border-zinc-700 dark:bg-zinc-900"
            >
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          )}
          {messages.length > 0 && (
            <Button variant="ghost" size="sm" onClick={() => setMessages([])} disabled={busy} title="Start a new conversation">
              <RotateCcw className="h-3.5 w-3.5" /> New chat
            </Button>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-4 py-6 sm:px-6">
          {messages.length === 0 ? (
            noDocs ? (
              <EmptyState
                icon={<Upload className="h-6 w-6" />}
                title="Add documents to this workspace first"
                action={<Link to="/documents"><Button>Upload documents</Button></Link>}
              >
                Shakti answers only from your own files, and cites exactly where each fact came from.
              </EmptyState>
            ) : (
              <EmptyState icon={<MessageSquare className="h-6 w-6" />} title="Ask anything about your documents">
                <span className="mt-4 flex flex-col gap-2">
                  {EXAMPLES.map((ex) => (
                    <button
                      key={ex}
                      onClick={() => send(ex)}
                      disabled={!ready}
                      className="rounded-lg border border-zinc-200 px-3 py-2 text-left text-sm text-zinc-700 hover:border-brand-500 hover:bg-brand-50/50 disabled:opacity-50 dark:border-zinc-800 dark:text-zinc-300 dark:hover:bg-brand-500/10"
                    >
                      {ex}
                    </button>
                  ))}
                </span>
              </EmptyState>
            )
          ) : (
            <ol className="space-y-6">
              {messages.map((m) =>
                m.role === "user" ? (
                  <li key={m.id} className="flex justify-end">
                    <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 text-[15px] text-white">{m.text}</p>
                  </li>
                ) : (
                  <li key={m.id}>
                    <AssistantMessage
                      message={m}
                      highlighted={highlight?.msg === m.id ? highlight.n : null}
                      onCite={(n) => {
                        setHighlight({ msg: m.id, n });
                        setTimeout(() => document.getElementById(`source-${n}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 50);
                      }}
                    />
                  </li>
                ),
              )}
            </ol>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="border-t border-zinc-200 bg-white/80 px-4 py-3 backdrop-blur sm:px-6 dark:border-zinc-800 dark:bg-zinc-950/80">
        <form
          className="mx-auto max-w-3xl"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          {!ready && status.data && (
            <div className="mb-2"><Alert tone="warning">Finish setup before asking — Shakti needs Ollama and a model.</Alert></div>
          )}
          <div className="flex items-end gap-2 rounded-2xl border border-zinc-300 bg-white p-2 focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/20 dark:border-zinc-700 dark:bg-zinc-900">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              rows={1}
              placeholder={ready ? "Ask a question about your documents…" : "Setup isn't finished yet"}
              disabled={!ready}
              aria-label="Your question"
              className="max-h-40 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-[15px] focus:outline-none"
              style={{ height: "auto" }}
              onInput={(e) => {
                const t = e.currentTarget;
                t.style.height = "auto";
                t.style.height = `${Math.min(t.scrollHeight, 160)}px`;
              }}
            />
            {busy ? (
              <Button type="button" variant="secondary" size="icon" aria-label="Stop" onClick={() => abortRef.current?.abort()}>
                <Square className="h-4 w-4" fill="currentColor" />
              </Button>
            ) : (
              <Button type="submit" size="icon" aria-label="Send" disabled={!input.trim() || !ready}>
                <ArrowUp className="h-4 w-4" />
              </Button>
            )}
          </div>
          <p className="mt-1.5 text-center text-[11px] text-zinc-400">Answers come only from documents in this workspace. Check the cited sources for anything important.</p>
        </form>
      </div>
    </div>
  );
}

function AssistantMessage({ message: m, highlighted, onCite }: { message: Extract<Message, { role: "assistant" }>; highlighted: number | null; onCite: (n: number) => void }) {
  const citations = m.answer?.citations ?? [];
  const notFound = m.answer?.answer === NOT_FOUND;

  return (
    <div className="space-y-2">
      {m.pending && m.stage && !m.text && (
        <p className="flex items-center gap-2 text-sm text-zinc-500"><Spinner /> {STAGE_LABELS[m.stage] ?? "Working…"}</p>
      )}
      {notFound ? (
        <Alert tone="neutral" title="Not found in your documents">
          None of the documents in this workspace seem to answer that. Try rephrasing, or check the right workspace is selected.
        </Alert>
      ) : (
        m.text && <AnswerMarkdown text={m.text} citations={citations} onCite={onCite} streaming={m.pending} />
      )}
      {m.error && <Alert tone="danger">{m.error}</Alert>}
      {m.answer?.used_sql && (
        <p className={cn("inline-flex items-center gap-1.5 text-xs text-zinc-500")}>
          <Database className="h-3.5 w-3.5" /> Answered from your spreadsheet and invoice data
        </p>
      )}
      {!notFound && <SourceList citations={citations} highlighted={highlighted} />}
    </div>
  );
}
