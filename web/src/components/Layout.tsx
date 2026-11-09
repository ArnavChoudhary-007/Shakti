import { useState, type ReactNode } from "react";
import { NavLink, Link } from "react-router-dom";
import { Boxes, Cpu, FileText, MessageSquare, Moon, Network, Plus, Settings as SettingsIcon, Sun, Zap, Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSetupStatus, useWorkspaces } from "@/lib/queries";
import { normalizeWorkspaceName, useWorkspace } from "@/lib/workspace";
import { useConnection } from "@/lib/connection";
import { Alert, Button } from "./ui";

const NAV = [
  { to: "/chat", label: "Ask", icon: MessageSquare },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/graph", label: "Knowledge graph", icon: Network },
  { to: "/models", label: "Models", icon: Cpu },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 font-semibold tracking-tight", className)}>
      <span className="grid h-7 w-7 place-items-center rounded-lg bg-brand-600 text-white">
        <Zap className="h-4 w-4" fill="currentColor" />
      </span>
      Shakti
    </span>
  );
}

export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => {
        const next = !dark;
        document.documentElement.classList.toggle("dark", next);
        localStorage.setItem("shakti.theme", next ? "dark" : "light");
        setDark(next);
      }}
    >
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  );
}

function WorkspaceSwitcher() {
  const { workspace, setWorkspace, localWorkspaces, addLocalWorkspace } = useWorkspace();
  const { data } = useWorkspaces();
  const names = Array.from(new Set(["default", ...(data?.workspaces.map((w) => w.workspace_id) ?? []), ...localWorkspaces, workspace])).sort();
  const counts = Object.fromEntries((data?.workspaces ?? []).map((w) => [w.workspace_id, w.document_count]));

  return (
    <div>
      <label htmlFor="workspace" className="mb-1.5 flex items-center gap-1.5 px-1 text-xs font-medium uppercase tracking-wide text-zinc-500">
        <Boxes className="h-3.5 w-3.5" /> Workspace
      </label>
      <div className="flex gap-1.5">
        <select
          id="workspace"
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          className="h-9 min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
        >
          {names.map((n) => (
            <option key={n} value={n}>
              {n} {counts[n] ? `(${counts[n]})` : ""}
            </option>
          ))}
        </select>
        <Button
          variant="outline"
          size="icon"
          aria-label="New workspace"
          title="New workspace"
          onClick={() => {
            const raw = window.prompt("Name the new workspace (letters, numbers, - and _):");
            const name = raw ? normalizeWorkspaceName(raw) : "";
            if (!name) return;
            addLocalWorkspace(name);
            setWorkspace(name);
          }}
        >
          <Plus className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

function OllamaIndicator() {
  const { data, isError } = useSetupStatus();
  const ok = data?.running && data.chat_model_installed;
  const label = isError
    ? "Companion unreachable"
    : !data
      ? "Checking…"
      : !data.installed
        ? "Ollama not installed"
        : !data.running
          ? "Ollama not running"
          : !data.chat_model_installed
            ? "No model selected"
            : data.chat_model;
  return (
    <Link to={ok ? "/models" : "/setup"} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800/70">
      <span className={cn("h-2 w-2 shrink-0 rounded-full", ok ? "bg-emerald-500" : data ? "bg-amber-500" : "bg-zinc-400")} />
      <span className="truncate text-zinc-600 dark:text-zinc-400">{label}</span>
    </Link>
  );
}

export function AppLayout({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const { mode } = useConnection();

  const sidebar = (
    <nav className="flex h-full flex-col gap-6 p-4" aria-label="Main">
      <div className="flex items-center justify-between">
        <Link to="/chat" onClick={() => setOpen(false)}>
          <Logo />
        </Link>
        <ThemeToggle />
      </div>
      <WorkspaceSwitcher />
      <ul className="flex flex-col gap-0.5">
        {NAV.map(({ to, label, icon: Icon }) => (
          <li key={to}>
            <NavLink
              to={to}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-100"
                    : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100",
                )
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          </li>
        ))}
      </ul>
      <div className="mt-auto space-y-2">
        <OllamaIndicator />
        <p className="px-2 text-[11px] leading-4 text-zinc-400">
          Everything runs on this PC. {mode === "remote" ? "This website talks to your local Shakti app." : "Your files never leave it."}
        </p>
      </div>
    </nav>
  );

  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 border-r border-zinc-200 bg-zinc-50/60 md:block dark:border-zinc-800 dark:bg-zinc-900/40">{sidebar}</aside>

      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 bg-white dark:bg-zinc-950">{sidebar}</aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-zinc-200 px-4 py-2 md:hidden dark:border-zinc-800">
          <Button variant="ghost" size="icon" aria-label={open ? "Close menu" : "Open menu"} onClick={() => setOpen(!open)}>
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </Button>
          <Logo />
          <span className="w-9" />
        </header>
        <SetupBanner />
        <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}

function SetupBanner() {
  const { data } = useSetupStatus();
  if (!data || (data.running && data.chat_model_installed)) return null;
  const message = !data.installed
    ? "Ollama isn't installed yet, so Shakti can't answer questions."
    : !data.running
      ? "Ollama isn't running."
      : `The model "${data.chat_model}" isn't downloaded yet.`;
  return (
    <div className="border-b border-amber-200 dark:border-amber-500/20">
      <Alert tone="warning" action={<Link to="/setup"><Button size="sm">Finish setup</Button></Link>}>
        {message}
      </Alert>
    </div>
  );
}
