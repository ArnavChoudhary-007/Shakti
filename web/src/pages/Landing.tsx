import { useEffect, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";
import { Check, ChevronRight, Layers, Loader2, Lock, WifiOff } from "lucide-react";
import { ChatFigure } from "@/components/landing/ChatFigure";
import { GraphFigure } from "@/components/landing/GraphFigure";
import { HeroDeco } from "@/components/landing/HeroDeco";
import { ModelsFigure } from "@/components/landing/ModelsFigure";
import { useConnection } from "@/lib/connection";
import { DEFAULT_COMPANION_URL, INSTALL_COMMAND, OLLAMA_DOWNLOAD_URL, REPO } from "@/lib/config";
import { cn } from "@/lib/utils";
import "@/components/landing/landing.css";

/**
 * Public landing page. Shown only when no companion is reachable from this
 * browser, so every "Open the app" leads to the install section: once the
 * companion is running, App switches to it automatically.
 */

const REPO_URL = `https://github.com/${REPO}`;

function jumpTo(id: string) {
  return (e: MouseEvent<HTMLAnchorElement>) => {
    const el = document.getElementById(id);
    if (!el) return;
    e.preventDefault();
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  };
}

function NavLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <a href={`#${to}`} onClick={jumpTo(to)} className="inline-flex min-h-11 items-center px-1 text-[15px] text-(--l-muted) transition-colors hover:text-(--l-text)">
      {children}
    </a>
  );
}

function Cta({ size = "lg", className, style }: { size?: "sm" | "lg"; className?: string; style?: CSSProperties }) {
  return (
    <a
      href="#install"
      onClick={jumpTo("install")}
      style={style}
      className={cn(
        "cta inline-flex items-center justify-center rounded-full bg-(--l-text) font-semibold text-(--l-bg) no-underline",
        size === "lg" ? "min-h-14 gap-2.5 px-8 text-[17px]" : "min-h-11 gap-2 px-[22px] text-[15px]",
        className,
      )}
    >
      Open the app
      <ChevronRight className={size === "lg" ? "h-4 w-4" : "h-3.5 w-3.5"} strokeWidth={2.4} aria-hidden />
    </a>
  );
}

function Gridlines() {
  return (
    <div aria-hidden className="gridlines absolute inset-0 z-0 grid grid-cols-3 md:grid-cols-6">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className={cn("a-draw border-l border-(--l-grid)", i === 5 && "border-r")} style={{ animationDelay: `${i * 0.12}s` }} />
      ))}
    </div>
  );
}

const TRUST = [
  { icon: WifiOff, title: "Runs offline", sub: "after setup" },
  { icon: Lock, title: "Files stay local", sub: "never uploaded" },
  { icon: Check, title: "Cited answers", sub: "down to the page" },
  { icon: Layers, title: "Local model", sub: "on your machine" },
];

function Feature({
  id,
  kicker,
  title,
  text,
  bullets,
  figure,
  layout,
}: {
  id?: string;
  kicker: string;
  title: string;
  text: string;
  bullets: string[];
  figure: ReactNode;
  layout: "figure-right" | "figure-below";
}) {
  return (
    <section id={id} className="relative z-10 mx-auto w-full max-w-[1120px] scroll-mt-6 px-6 py-24">
      <div className={cn("flex flex-wrap items-center gap-14", layout === "figure-below" && "flex-col items-stretch gap-12")}>
        <div className={cn("flex min-w-0 flex-col gap-5", layout === "figure-right" ? "flex-[1_1_360px]" : "max-w-[640px]")}>
          <span className="font-mono text-[13px] font-semibold uppercase tracking-[0.16em] text-(--l-accent)">{kicker}</span>
          <h2 className="m-0 text-balance text-[clamp(30px,4vw,46px)] font-semibold leading-[1.08] tracking-[-0.03em]">{title}</h2>
          <p className="m-0 max-w-[480px] text-pretty text-lg leading-[1.6] text-(--l-muted)">{text}</p>
          <ul className="m-0 mt-2 flex list-none flex-col gap-3 p-0">
            {bullets.map((b) => (
              <li key={b} className="flex items-start gap-3 text-base leading-normal text-(--l-text-2)">
                <Check className="mt-0.5 h-5 w-5 shrink-0 text-(--l-accent)" strokeWidth={2} aria-hidden />
                <span>{b}</span>
              </li>
            ))}
          </ul>
        </div>
        {figure}
      </div>
    </section>
  );
}

function CopyCommand({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mt-3 flex items-stretch overflow-hidden rounded-xl border border-[#2A3033] bg-[#0F1213]">
      <code className="flex-1 overflow-x-auto whitespace-nowrap px-3.5 py-3 font-mono text-[13px] text-(--l-text-2)">{value}</code>
      <button
        type="button"
        className="border-l border-[#2A3033] px-4 text-sm font-medium text-(--l-text) hover:bg-[#1E2325]"
        onClick={() => {
          void navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? "Copied!" : "Copy"}
      </button>
    </div>
  );
}

function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-[#3A4144] px-1.5 font-mono text-xs text-(--l-text)">{children}</kbd>;
}

function InstallCard() {
  const { retry } = useConnection();
  const isWindows = /windows/i.test(navigator.userAgent);
  const [checks, setChecks] = useState(0);

  // Keep looking for the companion while the user installs it; App takes
  // over as soon as it answers.
  useEffect(() => {
    const t = setInterval(() => {
      retry();
      setChecks((n) => n + 1);
    }, 4000);
    return () => clearInterval(t);
  }, [retry]);

  const link = "font-medium text-(--l-accent) underline-offset-4 hover:underline";

  return (
    <div id="install" className="mt-4 w-full max-w-[640px] scroll-mt-6 rounded-2xl border border-(--l-line) bg-(--l-panel) p-6 text-left shadow-[0_40px_80px_-40px_rgba(0,0,0,0.9)] sm:p-7">
      <p className="font-mono text-xs font-semibold uppercase tracking-[0.16em] text-(--l-accent)">Shakti isn't running on this PC</p>

      {isWindows ? (
        <>
          <p className="mt-3 text-[15px] leading-relaxed text-(--l-text-2)">
            Press <Kbd>Win</Kbd> + <Kbd>R</Kbd>, paste this command and press Enter. It installs Shakti for your user only, with no admin rights needed.
          </p>
          <CopyCommand value={INSTALL_COMMAND} />
          <p className="mt-2.5 text-xs leading-relaxed text-(--l-muted)">
            The first install downloads about 1–2 GB (Python and AI libraries) and takes a few minutes.{" "}
            <a className={link} href={`${REPO_URL}/blob/main/install.ps1`} target="_blank" rel="noreferrer">Read the script</a> before running it.
          </p>
        </>
      ) : (
        <p className="mt-3 text-[15px] leading-relaxed text-(--l-text-2)">
          Windows only for now. A one-click installer for macOS and Linux is coming; meanwhile you can{" "}
          <a className={link} href={`${REPO_URL}#readme`} target="_blank" rel="noreferrer">run Shakti from source</a>.
        </p>
      )}

      <ol className="mt-5 flex list-none flex-col gap-2 border-t border-(--l-line-soft) p-0 pt-5 text-sm text-(--l-muted)">
        <li className="flex gap-3"><span className="font-mono text-(--l-accent)">1</span> Shakti opens in your browser and helps you install <a className={link} href={OLLAMA_DOWNLOAD_URL} target="_blank" rel="noreferrer">Ollama</a>, the free engine that runs AI models locally.</li>
        <li className="flex gap-3"><span className="font-mono text-(--l-accent)">2</span> It suggests a model that fits your PC's memory and downloads it, along with the small models that index your files.</li>
        <li className="flex gap-3"><span className="font-mono text-(--l-accent)">3</span> Add documents, sorted into workspaces, and ask.</li>
      </ol>

      <div className="mt-5 flex items-center gap-3 border-t border-(--l-line-soft) pt-5">
        <Loader2 className="h-5 w-5 shrink-0 animate-spin text-(--l-accent)" aria-hidden />
        <div className="text-sm">
          <p className="font-medium">Looking for Shakti on this PC…</p>
          <p className="text-(--l-muted)">
            This page connects automatically once it's running.
            {checks > 1 && " If your browser asks to allow access to apps or devices on this computer, choose Allow."}
          </p>
        </div>
      </div>
      <p className="mt-4 text-sm text-(--l-muted)">
        Already installed? <a className={link} href={DEFAULT_COMPANION_URL}>Open Shakti</a> or start it from the Start menu.
      </p>
    </div>
  );
}

export default function LandingPage() {
  return (
    <div className="landing relative flex min-h-full flex-col overflow-hidden bg-(--l-bg) font-sans leading-normal text-(--l-text) antialiased">
      <Gridlines />
      <HeroDeco />

      <div id="top" className="relative z-10 flex min-h-[820px] flex-col">
        <header className="flex justify-center px-6 pt-9">
          <nav
            aria-label="Main"
            className="a-drop flex flex-wrap items-center justify-center gap-x-5 gap-y-2 rounded-full border border-(--l-line) bg-(--l-panel) py-2 pl-6 pr-2 shadow-[0_16px_40px_-20px_rgba(0,0,0,0.8)]"
          >
            <a href="#top" onClick={jumpTo("top")} className="flex min-h-11 items-center gap-2.5 no-underline">
              <span className="grid h-7 w-7 place-items-center rounded-lg bg-(--l-accent) text-(--l-accent-ink)">
                <Lock className="h-4 w-4" strokeWidth={2.2} aria-hidden />
              </span>
              <span className="text-lg font-semibold tracking-[-0.01em]">Shakti</span>
            </a>
            <NavLink to="features">How it works</NavLink>
            <NavLink to="private">Privacy</NavLink>
            <a href={REPO_URL} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center px-1 text-[15px] text-(--l-muted) transition-colors hover:text-(--l-text)">
              GitHub
            </a>
            <Cta size="sm" />
          </nav>
        </header>

        <main className="flex flex-1 flex-col items-center justify-center gap-5 px-6 py-12 text-center">
          <h1 className="a-reveal m-0 text-balance text-[clamp(52px,11.5vw,148px)] font-semibold leading-[0.95] tracking-[-0.05em]" style={{ animationDelay: "0.2s" }}>
            Ask your files
          </h1>
          <p className="a-reveal m-0 text-balance font-mono text-[13px] font-medium uppercase tracking-[0.18em] text-(--l-muted)" style={{ animationDelay: "0.45s" }}>
            Private answers from your documents, on your device
          </p>
          <Cta className="a-rise mt-3 shadow-[0_16px_40px_-16px_rgba(79,195,174,0.35)]" style={{ animationDelay: "0.7s" }} />
        </main>

        <div className="mx-auto grid w-full max-w-[1100px] grid-cols-[repeat(auto-fit,minmax(min(200px,100%),1fr))] gap-6 px-6 pb-10 pt-6">
          {TRUST.map(({ icon: Icon, title, sub }, i) => (
            <div key={title} className="a-rise flex items-center justify-center gap-3" style={{ animationDelay: `${1 + i * 0.1}s` }}>
              <Icon className="h-[26px] w-[26px] shrink-0 text-(--l-accent)" strokeWidth={1.6} aria-hidden />
              <span className="flex flex-col text-[13px] leading-[1.35]">
                <span className="font-medium">{title}</span>
                <span className="text-(--l-muted)">{sub}</span>
              </span>
            </div>
          ))}
        </div>
      </div>

      <Feature
        id="features"
        kicker="01 / Ask"
        title="A chat that thinks on your machine"
        text="Your question is matched against your documents, the best passages go to a language model installed on this computer, and the answer comes back with the file and page it came from."
        bullets={["Every answer cites the document, page, message or row", "Keep files in separate workspaces and ask within one", "Works with the internet switched off"]}
        figure={<ChatFigure />}
        layout="figure-right"
      />

      <Feature
        kicker="02 / Knowledge graph"
        title="See how your documents connect"
        text="Shakti pulls the people, companies, products and terms out of every file and draws how they relate. Related ones are grouped into named clusters, so you can see what a workspace is about at a glance."
        bullets={["Entities and their relationships drawn as one map", "Clusters are named automatically from what's in them", "Built on your device from your own files"]}
        figure={<GraphFigure />}
        layout="figure-below"
      />

      <Feature
        kicker="03 / Local models"
        title="Pick the model that fits your machine"
        text="Download the models you want and switch between them in one click. Smaller models answer faster on modest hardware, larger ones write more detailed answers. All of them run on your own computer."
        bullets={["Install, switch and remove models from one screen", "Shakti suggests models that fit your computer's memory", "Not even your prompts leave the device"]}
        figure={<ModelsFigure />}
        layout="figure-right"
      />

      <section id="private" className="relative z-10 mx-auto flex w-full max-w-[1120px] scroll-mt-6 flex-col items-center gap-6 px-6 pb-[120px] pt-24 text-center">
        <h2 className="m-0 max-w-[720px] text-balance text-[clamp(34px,6vw,72px)] font-semibold leading-none tracking-[-0.04em]">Run it on your own machine</h2>
        <p className="m-0 max-w-[520px] text-pretty text-lg leading-[1.6] text-(--l-muted)">
          Add a document, pick a model and ask your first question. Files, questions and answers all stay on this PC: there's no account, no cloud and nothing to pay.
        </p>
        <InstallCard />
      </section>

      <footer className="relative z-10 w-full border-t border-[#1C2022]">
        <div className="mx-auto flex max-w-[1120px] flex-wrap items-center justify-between gap-3 px-6 py-7 text-sm text-(--l-muted)">
          <span>
            Shakti. Free and open source on{" "}
            <a className="text-(--l-text-2) underline-offset-4 hover:underline" href={REPO_URL} target="_blank" rel="noreferrer">GitHub</a>.
          </span>
          <span>Everything runs on your device.</span>
        </div>
      </footer>
    </div>
  );
}
