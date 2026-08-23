import { cn } from "../../lib/utils";

export function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  
  let variant = "bg-muted text-muted-foreground"; // default neutral
  
  if (normalized.includes("resolved") || normalized.includes("pass")) {
    variant = "bg-green-100 text-green-800 border-green-200";
  } else if (normalized.includes("fail") || normalized.includes("error")) {
    variant = "bg-red-100 text-red-800 border-red-200";
  } else if (normalized.includes("run") || normalized.includes("progress")) {
    variant = "bg-blue-100 text-blue-800 border-blue-200 animate-pulse";
  }

  return (
    <span className={cn("inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wider", variant)}>
      {status.replace(/_/g, ' ')}
    </span>
  );
}
