import { Suspense } from "react";
import { AdminShell } from "@/components/admin/AdminShell";

// The console is gated by client-side admin auth, so its pages can't be part of
// a prerendered instant-navigation shell. Opt out of that validation.
export const instant = false;

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={null}>
      <AdminShell>{children}</AdminShell>
    </Suspense>
  );
}
