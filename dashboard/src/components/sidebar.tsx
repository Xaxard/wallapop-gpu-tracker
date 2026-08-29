"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/theme-toggle";
import { LogoutButton } from "@/components/logout-button";
import { LayoutDashboard, Receipt, BadgeEuro, Cpu, ListChecks, SlidersHorizontal } from "lucide-react";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/deals", label: "Deals & Alerts", icon: Receipt },
  { href: "/sales", label: "Sales", icon: BadgeEuro },
  { href: "/models", label: "Models", icon: Cpu },
  { href: "/listings", label: "Listings", icon: ListChecks },
  { href: "/searches", label: "Searches & Junk", icon: SlidersHorizontal },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex h-svh w-56 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground max-lg:hidden">
      <div className="flex items-center gap-2 px-4 py-5">
        <div className="flex size-7 items-center justify-center rounded-md border border-foreground">
          <span className="text-sm font-semibold">W</span>
        </div>
        <span className="text-sm font-semibold tracking-tight">Wallapop Tracker</span>
      </div>

      <nav className="flex flex-1 flex-col gap-0.5 px-2">
        {NAV.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
              )}
            >
              <Icon className="size-4" strokeWidth={2} />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="flex items-center justify-end gap-1 border-t px-3 py-3">
        <ThemeToggle />
        <LogoutButton />
      </div>
    </aside>
  );
}
