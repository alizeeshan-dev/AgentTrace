import { cn } from "../../lib/utils";

export function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  
  let variant = "border-border bg-muted text-muted-foreground";
  
  if (normalized.includes("resolved") || normalized.includes("pass")) {
    variant = "border-[#cfe8dc] bg-[#e8f6ef] text-[#177650]";
  } else if (normalized.includes("fail") || normalized.includes("error")) {
    variant = "border-[#f0cccc] bg-[#fdecec] text-[#b14248]";
  } else if (normalized.includes("run") || normalized.includes("progress")) {
    variant = "border-[#d6ddca] bg-[#edf1e5] text-[#6b7654] animate-pulse";
  } else if (normalized.includes("repair") || normalized.includes("counter")) {
    variant = "border-[#ecdca8] bg-[#fff5d9] text-[#9a6a00]";
  }

  return (
    <span className={cn("inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.1em]", variant)}>
      {status.replace(/_/g, ' ')}
    </span>
  );
}
