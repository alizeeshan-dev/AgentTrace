import { Link, Outlet, useLocation } from "react-router-dom";
import { Activity, LayoutDashboard, PlayCircle, FlaskConical } from "lucide-react";
import { cn } from "../../lib/utils";

const navigation = [
  { name: "Dashboard", href: "/", icon: LayoutDashboard },
  { name: "New Run", href: "/new-run", icon: PlayCircle },
  { name: "Runs", href: "/runs", icon: Activity },
  { name: "Experiments", href: "/experiments", icon: FlaskConical },
];

export function AppShell() {
  const location = useLocation();

  return (
    <div className="min-h-screen flex w-full flex-col lg:flex-row bg-muted/40">
      <aside className="fixed inset-y-0 left-0 z-10 hidden w-64 flex-col border-r bg-background lg:flex">
        <div className="flex h-14 items-center border-b px-6">
          <Link to="/" className="flex items-center gap-2 font-semibold">
            <span className="text-xl font-bold tracking-tight">AgentTrace</span>
          </Link>
        </div>
        <div className="flex-1 overflow-auto py-2">
          <nav className="grid items-start px-4 text-sm font-medium">
            {navigation.map((item) => {
              const isActive = location.pathname === item.href;
              return (
                <Link
                  key={item.name}
                  to={item.href}
                  className={cn(
                    "flex items-center gap-3 rounded-lg px-3 py-2 text-muted-foreground transition-all hover:text-primary",
                    isActive ? "bg-muted text-primary font-semibold" : ""
                  )}
                >
                  <item.icon className="h-4 w-4" />
                  {item.name}
                </Link>
              );
            })}
          </nav>
        </div>
      </aside>

      <main className="flex w-full flex-1 flex-col lg:pl-64">
        <div className="flex-1 p-6 sm:p-10 max-w-[1400px] mx-auto w-full">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
