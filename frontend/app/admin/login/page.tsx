"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { adminApi, adminAuth, ApiError } from "@/services/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <form
        className="w-full max-w-sm rounded-3xl border border-sand-200 bg-white p-7 shadow-xl shadow-sand-200/40"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            const res = await adminApi.login(email, password);
            adminAuth.set(res.access_token);
            router.replace("/admin");
          } catch (err) {
            setError(err instanceof ApiError ? err.message : "Login failed.");
          } finally {
            setBusy(false);
          }
        }}
      >
        <span className="flex size-11 items-center justify-center rounded-2xl bg-ink font-display text-lg font-semibold text-paper">HS</span>
        <h1 className="mt-4 font-display text-2xl font-semibold">Host Console</h1>
        <p className="mt-1 text-sm text-ink-soft">Manage properties, knowledge and local picks.</p>
        <label className="mt-6 block text-xs font-medium text-ink-soft" htmlFor="email">Email</label>
        <input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)}
          className="mt-1 w-full rounded-xl border border-sand-200 px-3 py-2.5 text-sm focus:border-terracotta-500 focus:outline-none" />
        <label className="mt-4 block text-xs font-medium text-ink-soft" htmlFor="password">Password</label>
        <input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)}
          className="mt-1 w-full rounded-xl border border-sand-200 px-3 py-2.5 text-sm focus:border-terracotta-500 focus:outline-none" />
        {error && <p role="alert" className="mt-3 text-sm text-terracotta-700">{error}</p>}
        <button type="submit" disabled={busy} className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-ink py-2.5 text-sm font-medium text-paper hover:bg-ink-soft disabled:opacity-60">
          {busy && <Loader2 className="size-4 animate-spin" />} Sign in
        </button>
      </form>
    </div>
  );
}
