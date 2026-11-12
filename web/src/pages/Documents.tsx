import { useRef, useState, type DragEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, CopyCheck, FileText, Trash2, UploadCloud } from "lucide-react";
import { Alert, Badge, Button, Card, CardHeader, EmptyState, PageHeader, Progress, Spinner } from "@/components/ui";
import { useUploads, type Upload } from "@/lib/uploads";
import { useApi } from "@/lib/connection";
import { useWorkspace } from "@/lib/workspace";
import { cn, formatBytes, formatDate } from "@/lib/utils";

const ACCEPT = [
  ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".txt", ".md", ".markdown", ".json",
  ".eml", ".mbox", ".pst", ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".opus", ".aac", ".mp4",
  ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp",
].join(",");

const STAGE_TEXT: Record<string, string> = {
  reading: "Reading the file",
  embedding: "Indexing for search",
  saving: "Saving",
};

export default function DocumentsPage() {
  const api = useApi();
  const queryClient = useQueryClient();
  const { workspace } = useWorkspace();
  const { uploads, addFiles, clearFinished } = useUploads();
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const docs = useQuery({ queryKey: ["documents", workspace], queryFn: () => api.documents(workspace) });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["documents"] });
    void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
  };

  const remove = useMutation({ mutationFn: api.deleteDocument, onSuccess: refresh });
  const clear = useMutation({ mutationFn: () => api.clearWorkspace(workspace), onSuccess: refresh });

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files, workspace);
  };

  const list = docs.data?.documents ?? [];
  const active = uploads.filter((u) => !u.error && (!u.job || u.job.status === "queued" || u.job.status === "processing"));

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <PageHeader
        title="Documents"
        description={<>Files in the <strong>{workspace}</strong> workspace. Questions only search the workspace you're in.</>}
      />

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors",
          dragging ? "border-brand-500 bg-brand-50/60 dark:bg-brand-500/10" : "border-zinc-300 dark:border-zinc-700",
        )}
      >
        <UploadCloud className="h-10 w-10 text-brand-600" />
        <p className="mt-3 font-medium">Drop files here, or</p>
        <Button className="mt-3" onClick={() => inputRef.current?.click()}>Choose files</Button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => {
            if (e.target.files?.length) addFiles(e.target.files, workspace);
            e.target.value = "";
          }}
        />
        <p className="mt-3 max-w-lg text-xs text-zinc-500">
          PDF, Word, PowerPoint, Excel/CSV, text &amp; Markdown, emails (.eml/.mbox), WhatsApp/Telegram/Slack/Teams exports, audio, and scanned invoices.
          Files are processed on this PC and stay here.
        </p>
      </div>

      {uploads.length > 0 && (
        <Card className="mt-6">
          <CardHeader
            title="Uploads"
            description={active.length ? `${active.length} in progress — you can keep using Shakti meanwhile.` : "All done."}
            action={!active.length && <Button variant="ghost" size="sm" onClick={clearFinished}>Clear list</Button>}
          />
          <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {uploads.map((u) => <UploadRow key={u.id} upload={u} />)}
          </ul>
        </Card>
      )}

      <Card className="mt-6">
        <CardHeader
          title={`In this workspace${list.length ? ` (${list.length})` : ""}`}
          action={
            list.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10"
                loading={clear.isPending}
                onClick={() => {
                  if (window.confirm(`Remove ALL documents from "${workspace}"? This can't be undone.`)) clear.mutate();
                }}
              >
                Remove all
              </Button>
            )
          }
        />
        {docs.isLoading ? (
          <div className="flex items-center gap-2 p-5 text-sm text-zinc-500"><Spinner /> Loading…</div>
        ) : docs.error ? (
          <div className="p-5"><Alert tone="danger">{(docs.error as Error).message}</Alert></div>
        ) : list.length === 0 ? (
          <EmptyState icon={<FileText className="h-6 w-6" />} title="No documents yet">
            Upload a few files above to start asking questions.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-zinc-500">
                  <th className="px-5 py-2 font-medium">Name</th>
                  <th className="px-3 py-2 font-medium">Type</th>
                  <th className="px-3 py-2 text-right font-medium">Passages</th>
                  <th className="px-3 py-2 text-right font-medium">Size</th>
                  <th className="px-3 py-2 font-medium">Added</th>
                  <th className="px-5 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {list.map((d) => (
                  <tr key={d.file_key} className="hover:bg-zinc-50 dark:hover:bg-zinc-900/60">
                    <td className="max-w-xs truncate px-5 py-2.5 font-medium" title={d.file_name}>{d.file_name}</td>
                    <td className="px-3 py-2.5">
                      <div className="flex gap-1">{d.source_types.map((t) => <Badge key={t}>{t}</Badge>)}</div>
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{d.chunk_count}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-zinc-500">{d.legacy ? "—" : formatBytes(d.size_bytes)}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-zinc-500">{formatDate(d.ingested_at)}</td>
                    <td className="px-5 py-2.5 text-right">
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Remove ${d.file_name}`}
                        loading={remove.isPending && remove.variables === d.file_key}
                        onClick={() => {
                          if (window.confirm(`Remove "${d.file_name}" from this workspace?`)) remove.mutate(d.file_key);
                        }}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {remove.error && <div className="p-4"><Alert tone="danger">{(remove.error as Error).message}</Alert></div>}
      </Card>
    </div>
  );
}

function UploadRow({ upload: u }: { upload: Upload }) {
  const job = u.job;
  let icon = <Spinner className="text-brand-600" />;
  let text = "Uploading…";
  let tone: "neutral" | "success" | "danger" = "neutral";

  if (u.error || job?.status === "failed") {
    icon = <AlertCircle className="h-4 w-4 text-red-600" />;
    text = u.error || job?.error || "Failed";
    tone = "danger";
  } else if (u.pollError) {
    text = u.pollError;
  } else if (job?.status === "done" && job.duplicate) {
    icon = <CopyCheck className="h-4 w-4 text-zinc-500" />;
    text = "Already in this workspace — skipped";
  } else if (job?.status === "done") {
    icon = <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
    text = `Ready · ${job.chunk_count} passages${job.replaced ? " · replaced the older version" : ""}`;
    tone = "success";
  } else if (job?.status === "queued") {
    text = "Waiting for the file ahead of it…";
  } else if (job?.status === "processing") {
    text = `${STAGE_TEXT[job.stage ?? ""] ?? "Processing"}…`;
  }

  return (
    <li className="flex items-center gap-3 px-5 py-3">
      {icon}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{u.file.name}</p>
        <p className={cn("text-xs", tone === "danger" ? "text-red-600 dark:text-red-400" : tone === "success" ? "text-emerald-700 dark:text-emerald-400" : "text-zinc-500")}>
          {text}
          {u.workspace && <span className="text-zinc-400"> · {u.workspace}</span>}
        </p>
        {!u.error && job?.status === "processing" && <Progress className="mt-1.5 h-1" value={null} />}
      </div>
      <span className="text-xs tabular-nums text-zinc-400">{formatBytes(u.file.size)}</span>
    </li>
  );
}
