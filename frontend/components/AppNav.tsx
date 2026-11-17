"use client";

// Main menu: Dashboard, Odoo configuration, User, and the Sign out button.
//
// Purpose: navigation between the three application pages. The active item is marked (aria-current) and remains usable
// on mobile screens (the menu wraps onto the next line).
//
// Related: uses hooks/useAuth.ts (useLogout); mounted by components/AuthGate.tsx after login.
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useLogout } from "@/hooks/useAuth";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/odoo", label: "Odoo configuration" },
  { href: "/account", label: "User" },
];

export default function AppNav({ username }: { username: string }) {
  const pathname = usePathname();
  const logout = useLogout();

  return (
    <header className="border-b border-zinc-200 dark:border-zinc-800">
      <nav aria-label="Main" className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 sm:px-6">
        <span className="font-semibold">Verve Inventory</span>
        <ul className="flex flex-wrap items-center gap-1">
          {LINKS.map((link) => {
            const active = pathname === link.href;
            return (
              <li key={link.href}>
                <Link
                  href={link.href}
                  aria-current={active ? "page" : undefined}
                  className={`rounded-md px-3 py-1.5 text-sm ${
                    active ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900"
                  }`}
                >
                  {link.label}
                </Link>
              </li>
            );
          })}
        </ul>
        <div className="ml-auto flex items-center gap-3 text-sm">
          <span className="text-zinc-500">{username}</span>
          <button
            type="button"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
            className="rounded-md border border-zinc-300 px-3 py-1.5 disabled:opacity-60 dark:border-zinc-700"
          >
            Sign out
          </button>
        </div>
      </nav>
    </header>
  );
}
