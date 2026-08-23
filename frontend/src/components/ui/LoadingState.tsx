import { Loader2 } from "lucide-react";

export function LoadingState() {
  return (
    <div className="flex flex-col items-center justify-center p-24 text-center">
      <Loader2 className="h-10 w-10 animate-spin text-muted-foreground/50" />
      <p className="mt-4 text-sm font-medium text-muted-foreground">Loading data...</p>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={`animate-pulse rounded-md bg-muted ${className}`} />
  );
}
