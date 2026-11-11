import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** The app-window frame used by the landing page illustrations. */
export function Frame({
  title,
  label,
  footer,
  className,
  children,
}: {
  title: string;
  /** Accessible description of what the illustration shows (static figures only). */
  label?: string;
  footer?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <figure
      role={label ? "img" : undefined}
      aria-label={label}
      className={cn(
        "m-0 min-w-0 overflow-hidden rounded-2xl border border-(--l-line) bg-(--l-panel) shadow-[0_40px_80px_-40px_rgba(0,0,0,0.9)]",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-3 border-b border-(--l-line-soft) px-4 py-3">
        <span className="text-[13px] font-medium">{title}</span>
        <span className="inline-flex items-center gap-2 text-xs text-(--l-muted)">
          <span aria-hidden className="h-[7px] w-[7px] rounded-full bg-(--l-accent)" />
          Offline
        </span>
      </div>
      {children}
      {footer && <div className="border-t border-(--l-line-soft) px-4 py-2.5 text-xs text-(--l-muted)">{footer}</div>}
    </figure>
  );
}

export function Cite({ n }: { n: number }) {
  return (
    <span className="inline-block rounded-md bg-(--l-cite-bg) px-1.5 text-[11px] font-semibold leading-[18px] text-(--l-cite-fg)">{n}</span>
  );
}
