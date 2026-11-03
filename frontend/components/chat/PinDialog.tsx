"use client";

import { useState } from "react";
import { KeyRound, X } from "lucide-react";

export function PinDialog({ onClose, onVerify }: { onClose: () => void; onVerify: (pin: string) => Promise<boolean> }) {
  const [pin, setPin] = useState("");
  const [state, setState] = useState<"idle" | "busy" | "error">("idle");

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/30 p-4 backdrop-blur-sm sm:items-center" role="dialog" aria-modal="true" aria-labelledby="pin-title">
      <form
        className="animate-rise w-full max-w-sm rounded-3xl bg-white p-6 shadow-2xl"
        onSubmit={async (e) => {
          e.preventDefault();
          setState("busy");
          const ok = await onVerify(pin);
          if (ok) onClose();
          else setState("error");
        }}
      >
        <div className="mb-4 flex items-start justify-between">
          <span className="flex size-11 items-center justify-center rounded-2xl bg-terracotta-50 text-terracotta-600">
            <KeyRound className="size-5" aria-hidden />
          </span>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-full p-1.5 text-ink-muted hover:bg-sand-50">
            <X className="size-5" />
          </button>
        </div>
        <h2 id="pin-title" className="font-display text-xl font-semibold">Verify your booking</h2>
        <p className="mt-1 text-sm text-ink-soft">
          Enter the guest PIN from your booking message to unlock Wi-Fi and access details.
        </p>
        <input
          autoFocus
          inputMode="text"
          autoComplete="one-time-code"
          value={pin}
          onChange={(e) => {
            setPin(e.target.value.trim());
            setState("idle");
          }}
          maxLength={12}
          className="mt-4 w-full rounded-xl border border-sand-200 px-4 py-3 text-center font-mono text-lg tracking-[0.4em] focus:border-terracotta-500 focus:outline-none"
          aria-label="Guest PIN"
        />
        {state === "error" && <p className="mt-2 text-sm text-terracotta-700">That PIN didn&apos;t match. Please check and try again.</p>}
        <button
          type="submit"
          disabled={pin.length < 4 || state === "busy"}
          className="mt-4 w-full rounded-xl bg-ink py-3 text-sm font-medium text-paper transition hover:bg-ink-soft disabled:opacity-50"
        >
          {state === "busy" ? "Checking…" : "Unlock"}
        </button>
      </form>
    </div>
  );
}
