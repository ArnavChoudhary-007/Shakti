import { useEffect, useState } from "react";
import { BookOpenCheck, Cpu, FileSearch, Lock, MonitorSmartphone, Quote } from "lucide-react";
import { Logo, ThemeToggle } from "@/components/Layout";
import { Alert, Button, Card, CopyField, Spinner } from "@/components/ui";
import { useConnection } from "@/lib/connection";
import { DEFAULT_COMPANION_URL, INSTALL_COMMAND, OLLAMA_DOWNLOAD_URL, REPO } from "@/lib/config";

const FEATURES = [
  { icon: Lock, title: "Private by design", text: "Your files and questions never leave your PC. No account, no cloud, no subscription." },
  { icon: Quote, title: "Answers with sources", text: "Every answer cites the exact page, message or row it came from, so you can check it." },
  { icon: FileSearch, title: "All your formats", text: "PDFs, Word, PowerPoint, spreadsheets, emails, WhatsApp and Slack exports, audio, scanned invoices." },
  { icon: Cpu, title: "Runs on your hardware", text: "Shakti picks AI models that fit your computer's memory, and you can switch any time." },
];

function useIsWindows() {
  return /windows/i.test(navigator.userAgent);
}

export default function LandingPage() {
  const { retry } = useConnection();
  const isWindows = useIsWindows();
  const [checks, setChecks] = useState(0);

  // Keep looking for the companion while the user installs it.
  useEffect(() => {
    const t = setInterval(() => {
      retry();
      setChecks((n) => n + 1);
    }, 4000);
    return () => clearInterval(t);
  }, [retry]);

  return (
    <div className="min-h-full bg-gradient-to-b from-brand-50/60 to-white dark:from-zinc-900 dark:to-zinc-950">
      <header className="mx-auto flex max-w-5xl items-center justify-between px-6 py-5">
        <Logo />
        <div className="flex items-center gap-2">
          <a href={`https://github.com/${REPO}`} target="_blank" rel="noreferrer" className="text-sm text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100">GitHub</a>
          <ThemeToggle />
        </div>
      </header>

      <section className="mx-auto max-w-5xl px-6 pb-12 pt-10 text-center sm:pt-16">
        <h1 className="mx-auto max-w-3xl text-4xl font-bold tracking-tight sm:text-5xl">
          Ask your documents anything. <span className="text-brand-600">Privately.</span>
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-lg text-zinc-600 dark:text-zinc-400">
          Shakti is a free AI research assistant that runs entirely on your own computer. Drop in your files, ask questions in plain language, and get answers with citations.
        </p>
        <div className="mt-8 flex justify-center gap-3">
          <a href="#install"><Button size="lg">Get started — it's free</Button></a>
        </div>
      </section>

      <section className="mx-auto grid max-w-5xl gap-4 px-6 sm:grid-cols-2 lg:grid-cols-4">
        {FEATURES.map(({ icon: Icon, title, text }) => (
          <Card key={title} className="p-5">
            <Icon className="h-6 w-6 text-brand-600" />
            <h3 className="mt-3 font-semibold">{title}</h3>
            <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{text}</p>
          </Card>
        ))}
      </section>

      <section id="install" className="mx-auto max-w-3xl scroll-mt-8 px-6 py-16">
        <h2 className="text-center text-2xl font-semibold tracking-tight">Set up in three steps</h2>
        <ol className="mt-8 space-y-4">
          <li>
            <Card className="p-5">
              <p className="font-semibold">1. Install the Shakti app</p>
              {isWindows ? (
                <>
                  <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
                    Press <kbd className="rounded border px-1 text-xs">Win</kbd> + <kbd className="rounded border px-1 text-xs">R</kbd>, paste this command and press Enter. It installs Shakti for your user only — no admin rights needed.
                  </p>
                  <div className="mt-3"><CopyField value={INSTALL_COMMAND} /></div>
                  <p className="mt-2 text-xs text-zinc-500">
                    The first install downloads about 1–2 GB (Python and AI libraries) and takes a few minutes.{" "}
                    <a className="underline" href={`https://github.com/${REPO}/blob/main/install.ps1`} target="_blank" rel="noreferrer">Read the script</a> before running it.
                  </p>
                </>
              ) : (
                <Alert tone="neutral" title="Windows only for now">
                  A one-click installer for macOS and Linux is coming. Meanwhile you can{" "}
                  <a className="underline" href={`https://github.com/${REPO}#readme`} target="_blank" rel="noreferrer">run Shakti from source</a>.
                </Alert>
              )}
            </Card>
          </li>
          <li>
            <Card className="p-5">
              <p className="font-semibold">2. Let Shakti set up the AI</p>
              <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
                Shakti opens in your browser and walks you through installing{" "}
                <a className="underline" href={OLLAMA_DOWNLOAD_URL} target="_blank" rel="noreferrer">Ollama</a> (the free engine that runs AI models locally) and downloading a model that suits your PC.
              </p>
            </Card>
          </li>
          <li>
            <Card className="p-5">
              <p className="font-semibold">3. Upload documents and ask</p>
              <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">Everything is processed on your machine. Organize files into workspaces and ask across them.</p>
            </Card>
          </li>
        </ol>

        <Card className="mt-8 flex items-center gap-3 p-5">
          <Spinner className="text-brand-600" />
          <div className="text-sm">
            <p className="font-medium">Looking for Shakti on this PC…</p>
            <p className="text-zinc-500">
              This page connects automatically once it's running.
              {checks > 1 && " If your browser asks to allow access to apps or devices on this computer, choose Allow."}
            </p>
          </div>
        </Card>
        <p className="mt-3 text-center text-sm text-zinc-500">
          Already installed?{" "}
          <a className="font-medium text-brand-600 hover:underline dark:text-brand-500" href={DEFAULT_COMPANION_URL}>Open Shakti</a>{" "}
          or start it from the Start menu.
        </p>
      </section>

      <footer className="border-t border-zinc-200 py-8 text-center text-sm text-zinc-500 dark:border-zinc-800">
        <p className="inline-flex items-center gap-2"><BookOpenCheck className="h-4 w-4" /> Free and open source · <MonitorSmartphone className="h-4 w-4" /> Your data stays on your device</p>
      </footer>
    </div>
  );
}
