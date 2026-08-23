import { Loader2 } from "lucide-react";

export function LoadingState() {
  return (
    <div className="research-panel flex flex-col items-center justify-center p-24 text-center">
      <Loader2 className="h-9 w-9 animate-spin text-primary/60" />
      <p className="mt-4 text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">Loading evidence…</p>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={`animate-pulse rounded-md bg-muted ${className}`} />
  );
}
