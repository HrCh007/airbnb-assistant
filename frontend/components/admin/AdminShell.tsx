"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Building2, FileText, LayoutDashboard, Loader2, LogOut, MessageCircle } from "lucide-react";
import { adminApi, adminAuth } from "@/services/api";

const NAV = [
  { href: "/admin", label: "Dashboard", icon: LayoutDashboard },
  { href: "/admin/properties", label: "Properties", icon: Building2 },
  { href: "/admin/documents", label: "Knowledge", icon: FileText },
];

export function AdminShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<{ email: string } | null>(null);

  useEffect(() => {
    if (!adminAuth.get()) {
      router.replace("/admin/login");
      return;
    }
    adminApi.me().then(setMe).catch(() => router.replace("/admin/login"));
  }, [router]);

  if (!me) {
    return (
      <div className="flex h-dvh items-center justify-center text-ink-muted">
        <Loader2 className="size-6 animate-spin" aria-label="Loading" />
      </div>
    );
  }

  return (
    <div className="min-h-dvh bg-sand-50/40">
      <header className="sticky top-0 z-20 border-b border-sand-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3">
          <Link href="/admin" className="flex items-center gap-2">
            <span className="flex size-9 items-center justify-center rounded-xl bg-ink font-display font-semibold text-paper">HS</span>
            <span className="hidden font-display text-lg font-semibold sm:inline">Host Console</span>
          </Link>
          <nav className="flex flex-1 gap-1 overflow-x-auto">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active = href === "/admin" ? pathname === "/admin" : pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition ${
                    active ? "bg-terracotta-50 text-terracotta-700" : "text-ink-soft hover:bg-sand-50"
                  }`}
                >
                  <Icon className="size-4" aria-hidden /> {label}
                </Link>
              );
            })}
          </nav>
          <Link href="/" className="hidden items-center gap-1.5 text-sm text-ink-soft hover:text-ink md:inline-flex" target="_blank">
            <MessageCircle className="size-4" aria-hidden /> Guest chat
          </Link>
          <button
            type="button"
            onClick={() => {
              adminAuth.clear();
              router.replace("/admin/login");
            }}
            className="inline-flex items-center gap-1.5 rounded-lg px-2 py-2 text-sm text-ink-soft hover:bg-sand-50"
            title={me.email}
          >
            <LogOut className="size-4" aria-hidden /> <span className="hidden sm:inline">Sign out</span>
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}

export function PageTitle({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="font-display text-2xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-ink-soft">{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}

export const card = "rounded-2xl border border-sand-200 bg-white p-5 shadow-[0_1px_2px_rgba(42,33,27,0.04)]";
export const btnPrimary =
  "inline-flex items-center gap-1.5 rounded-xl bg-ink px-4 py-2 text-sm font-medium text-paper transition hover:bg-ink-soft disabled:opacity-50";
export const btnSecondary =
  "inline-flex items-center gap-1.5 rounded-xl border border-sand-200 bg-white px-3 py-2 text-sm font-medium text-ink transition hover:bg-sand-50 disabled:opacity-50";
export const input =
  "w-full rounded-xl border border-sand-200 bg-white px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-terracotta-500 focus:outline-none";
export const label = "mb-1 block text-xs font-medium text-ink-soft";
