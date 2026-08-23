import { Link, Outlet, useLocation } from "react-router-dom";
import { Activity, LayoutDashboard, Play, FlaskConical } from "lucide-react";
import { cn } from "../../lib/utils";

const navigation = [
  { name: "Dashboard", href: "/", icon: LayoutDashboard },
  { name: "New Run", href: "/new-run", icon: Play },
  { name: "Runs", href: "/runs", icon: Activity },
  { name: "Experiments", href: "/experiments", icon: FlaskConical },
];

export function AppShell() {
  const location = useLocation();

  return (
    <div className="min-h-screen w-full bg-background">
      <main className="w-full">
        <div className="mx-auto min-h-screen w-full max-w-[1220px] px-4 pb-28 pt-7 sm:px-7 sm:pt-10 lg:px-10">
          <Outlet />
        </div>
      </main>

      <nav
        aria-label="Primary navigation"
        className="fixed bottom-4 left-1/2 z-50 flex w-[calc(100%-2rem)] max-w-[660px] -translate-x-1/2 items-center gap-1 rounded-2xl border border-white/10 bg-[#20372b] p-1.5 text-white shadow-[0_16px_40px_rgba(26,48,36,0.28)] sm:bottom-6"
      >
        {navigation.map((item) => {
          const isActive = item.href === "/" ? location.pathname === "/" : location.pathname.startsWith(item.href);
          return (
            <Link
              key={item.name}
              to={item.href}
              className={cn(
                "flex min-w-0 flex-1 items-center justify-center gap-2 rounded-xl px-2 py-2.5 text-xs font-medium text-white/65 transition sm:px-4",
                isActive ? "bg-[#8dac91] text-white shadow-sm" : "hover:bg-white/8 hover:text-white"
              )}
            >
              <item.icon className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{item.name}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
