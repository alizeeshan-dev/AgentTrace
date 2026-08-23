import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { cn } from "../../lib/utils";

export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle: string;
  action?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex min-w-0 items-start gap-5">
        <Link to="/" className="mt-1 shrink-0" aria-label="AgentTrace dashboard">
          <span className="block text-[18px] font-black tracking-[-0.05em] text-[var(--sage-ink)]">AgentTrace</span>
          <span className="mt-1 block h-[3px] w-20 rounded-full bg-primary" />
        </Link>
        <div className="min-w-0 border-l border-border pl-5">
          <h1 className="text-2xl font-bold tracking-[-0.035em] text-[var(--sage-ink)] sm:text-[28px]">{title}</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{subtitle}</p>
        </div>
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </header>
  );
}

export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <section className={cn("research-panel", className)}>{children}</section>;
}

export function PanelHeading({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <h2 className="text-base font-bold tracking-[-0.02em] text-[var(--sage-ink)]">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function MetricCard({
  label,
  value,
  note,
  bars,
  accent = "sage",
}: {
  label: string;
  value: string | number;
  note?: string;
  bars?: number[];
  accent?: "sage" | "green" | "amber" | "lavender";
}) {
  const barColors = {
    sage: "bg-[#718f78]",
    green: "bg-[#1da774]",
    amber: "bg-[#d59a18]",
    lavender: "bg-[#aaa8e8]",
  };
  return (
    <Panel className="flex min-h-[130px] flex-col p-5">
      <div className="research-kicker">{label}</div>
      <div className="mt-2 flex items-end gap-2">
        <span className="text-[30px] font-bold leading-none tracking-[-0.05em] text-[var(--sage-ink)]">{value}</span>
        {note && <span className="mb-0.5 text-[10px] font-semibold text-primary">{note}</span>}
      </div>
      {bars && <MiniBars values={bars} className="mt-auto pt-4" barClassName={barColors[accent]} />}
    </Panel>
  );
}

export function MiniBars({
  values,
  className,
  barClassName = "bg-primary",
}: {
  values: number[];
  className?: string;
  barClassName?: string;
}) {
  const max = Math.max(1, ...values);
  return (
    <div className={cn("flex h-9 items-end gap-1.5", className)} aria-hidden="true">
      {values.map((value, index) => (
        <span
          key={`${value}-${index}`}
          className={cn("min-h-1 flex-1 rounded-full opacity-95", barClassName)}
          style={{ height: `${Math.max(12, (value / max) * 100)}%` }}
        />
      ))}
    </div>
  );
}

export function OutcomeDonut({
  resolved,
  failed,
  infrastructure,
}: {
  resolved: number;
  failed: number;
  infrastructure: number;
}) {
  const total = resolved + failed + infrastructure;
  const resolvedPct = total ? (resolved / total) * 100 : 0;
  const failedPct = total ? (failed / total) * 100 : 0;
  const style = {
    background: `conic-gradient(#20a673 0 ${resolvedPct}%, #ce5d62 ${resolvedPct}% ${resolvedPct + failedPct}%, #d9a122 ${resolvedPct + failedPct}% 100%)`,
  } as CSSProperties;
  return (
    <div className="flex items-center gap-8">
      <div className="relative h-32 w-32 shrink-0 rounded-full" style={style}>
        <div className="absolute inset-[15px] grid place-items-center rounded-full bg-white text-center">
          <div>
            <div className="text-2xl font-bold leading-none text-[var(--sage-ink)]">{Math.round(resolvedPct)}%</div>
            <div className="mt-1 text-[9px] uppercase tracking-wider text-muted-foreground">resolved</div>
          </div>
        </div>
      </div>
      <div className="min-w-0 flex-1 space-y-3 text-xs">
        <LegendRow color="bg-[#20a673]" label="Resolved" value={resolved} />
        <LegendRow color="bg-[#ce5d62]" label="Verification fail" value={failed} />
        <LegendRow color="bg-[#d9a122]" label="Provider / infra" value={infrastructure} />
      </div>
    </div>
  );
}

function LegendRow({ color, label, value }: { color: string; label: string; value: number }) {
  return (
    <div className="flex items-center gap-2">
      <span className={cn("h-2.5 w-2.5 rounded-full", color)} />
      <span className="min-w-0 flex-1 text-muted-foreground">{label}</span>
      <span className="font-semibold text-foreground">{value}</span>
    </div>
  );
}

export function InlineLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline">
      {children}
      <ArrowUpRight className="h-3 w-3" />
    </Link>
  );
}
