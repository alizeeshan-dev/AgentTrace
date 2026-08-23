import { ServerCrash } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Link } from "react-router-dom";
import { cn } from "../../lib/utils";

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description: string;
  actionText?: string;
  actionHref?: string;
  isError?: boolean;
}

export function EmptyState({
  icon: Icon = ServerCrash,
  title,
  description,
  actionText,
  actionHref,
  isError = false,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center p-12 text-center border rounded-xl border-dashed bg-background/50">
      <div className={cn("flex h-20 w-20 items-center justify-center rounded-full bg-muted", isError && "bg-destructive/10 text-destructive")}>
        <Icon className={cn("h-10 w-10 text-muted-foreground", isError && "text-destructive")} />
      </div>
      <h3 className="mt-6 text-xl font-semibold tracking-tight">{title}</h3>
      <p className="mt-2 text-sm text-muted-foreground max-w-[400px]">
        {description}
      </p>
      {actionText && actionHref && (
        <Link
          to={actionHref}
          className="mt-6 inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          {actionText}
        </Link>
      )}
    </div>
  );
}
