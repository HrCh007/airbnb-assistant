"use client";

import { useEffect, useRef, useState } from "react";
import {
  Compass,
  FlaskConical,
  Home,
  KeyRound,
  Loader2,
  LockOpen,
  MapPin,
  Pill,
  RotateCcw,
  ShoppingBag,
  UtensilsCrossed,
  Wine,
} from "lucide-react";
import { useChat } from "@/hooks/useChat";
import { Composer } from "./Composer";
import { LocationCards, PropertyCards } from "./LocationPicker";
import { Avatar, MessageBubble } from "./MessageBubble";
import { PinDialog } from "./PinDialog";

const QUICK_ACTIONS = [
  { key: "property", label: "Property info", icon: Home },
  { key: "food", label: "Food nearby", icon: UtensilsCrossed },
  { key: "things_to_do", label: "Things to do", icon: Compass },
  { key: "shopping", label: "Shopping", icon: ShoppingBag },
  { key: "nightlife", label: "Nightlife", icon: Wine },
  { key: "essentials", label: "Essentials", icon: Pill },
] as const;

const GREETING =
  "Hey! Welcome to your local Delhi guide. I'm here to help you make the most of your stay. Whether you need information about your accommodation, great places to eat, things to do or local recommendations, just ask me!";

export function ChatApp() {
  const chat = useChat();
  const { session, status, messages } = chat;
  const [pinOpen, setPinOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const property = session?.property ?? null;
  const needsProperty = !!session?.location_slug && !property && (session?.available_properties.length ?? 0) > 0;
  const devMode = status && (status.llm === "offline" || status.places !== "google_places");

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, property?.id, needsProperty]);

  const quick = (key: string) => {
    const q = status?.quick_actions?.[key];
    if (q) chat.send(q);
  };

  return (
    <div className="mx-auto flex h-dvh max-w-3xl flex-col">
      {/* ---------------------------------------------------------------- header */}
      <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-sand-100 bg-paper/85 px-4 pt-[max(0.75rem,env(safe-area-inset-top))] pb-3 backdrop-blur">
        <div aria-label="Delhi Stays logo" className="flex size-10 items-center justify-center rounded-2xl bg-ink font-display text-lg font-semibold text-paper">
          DS
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="font-display text-lg leading-tight font-semibold">Delhi Stays</h1>
          <p className="truncate text-xs text-ink-muted" data-testid="header-context">
            {property ? (
              <>
                <MapPin className="mr-0.5 mb-0.5 inline size-3" aria-hidden />
                {property.name}
              </>
            ) : (
              "Your local guide"
            )}
          </p>
        </div>
        {devMode && (
          <span
            className="hidden items-center gap-1 rounded-full bg-amber-100 px-2 py-1 text-[11px] font-medium text-amber-800 sm:inline-flex"
            title={`AI: ${status?.llm} · Places: ${status?.places} · Embeddings: ${status?.embeddings}`}
          >
            <FlaskConical className="size-3" aria-hidden /> Dev mode
          </span>
        )}
        {property?.requires_pin && (
          <button
            type="button"
            onClick={() => setPinOpen(true)}
            disabled={session?.pin_verified}
            aria-label={session?.pin_verified ? "Booking verified" : "Verify booking PIN"}
            className="rounded-full p-2 text-ink-soft transition hover:bg-sand-100 disabled:text-leaf-600"
          >
            {session?.pin_verified ? <LockOpen className="size-5" /> : <KeyRound className="size-5" />}
          </button>
        )}
        {property && (
          <button
            type="button"
            onClick={chat.changeLocation}
            className="rounded-full px-3 py-1.5 text-xs font-medium text-ink-soft ring-1 ring-sand-200 transition hover:bg-sand-50"
            data-testid="change-location"
          >
            Change stay
          </button>
        )}
        {messages.length > 0 && (
          <button type="button" onClick={chat.clear} aria-label="Clear conversation" className="rounded-full p-2 text-ink-soft transition hover:bg-sand-100">
            <RotateCcw className="size-5" />
          </button>
        )}
      </header>

      {/* ---------------------------------------------------------------- body */}
      <main className="flex-1 overflow-y-auto px-4 pt-5 pb-4" aria-live="polite">
        {chat.booting ? (
          <div className="flex h-full items-center justify-center text-ink-muted">
            <Loader2 className="size-6 animate-spin" aria-label="Loading" />
          </div>
        ) : (
          <div className="space-y-5">
            <div className="flex gap-2.5">
              <Avatar />
              <div className="rounded-2xl rounded-tl-md border border-sand-200 bg-white px-4 py-3 text-[15px] leading-relaxed">
                <p>{GREETING}</p>
                {!property && <p className="mt-2 font-medium">Which neighbourhood are you staying in?</p>}
              </div>
            </div>


            {!property && !needsProperty && chat.locations && (
              <LocationCards locations={chat.locations} onSelect={chat.selectLocation} busy={chat.selecting} />
            )}

            {needsProperty && session && (
              <div className="space-y-3">
                <p className="text-sm text-ink-soft">
                  We have a few stays in <strong>{session.location_name}</strong>. Which one is yours?
                </p>
                <PropertyCards properties={session.available_properties} onSelect={chat.selectProperty} busy={chat.selecting} />
              </div>
            )}

            {property && (
              <div className="animate-rise flex gap-2.5" data-testid="property-ready">
                <Avatar />
                <div className="rounded-2xl rounded-tl-md border border-sand-200 bg-white px-4 py-3 text-[15px] leading-relaxed">
                  You&apos;re all set at <strong>{property.name}</strong>
                  {property.neighbourhood ? <> in {property.neighbourhood}</> : null}. Ask me anything, or tap a shortcut below.
                  {property.is_placeholder && (
                    <span className="mt-1 block text-xs text-ink-muted">(This is a placeholder demo property with fictional details.)</span>
                  )}
                </div>
              </div>
            )}

            {devMode && (
              <p className="rounded-xl bg-amber-50/80 px-3 py-2 text-xs text-amber-900 ring-1 ring-amber-200">
                <strong>Development mode:</strong>{" "}
                {status?.llm === "offline" && "AI answers run offline (no Claude API key) and quote the property guide directly. "}
                {status?.places === "mock" && "Local results are fictional mock data. "}
                {status?.places === "disabled" && "Live local search is off (no Google Maps API key). "}
              </p>
            )}

            {messages.map((m, i) => (
              <MessageBubble key={m.id} message={m} onSuggestion={i === messages.length - 1 && !chat.sending ? chat.send : undefined} />
            ))}

            {chat.error && (
              <p role="alert" className="rounded-xl bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700">
                {chat.error}
              </p>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </main>

      {/* ---------------------------------------------------------------- footer */}
      <footer className="border-t border-sand-100 bg-paper/95 px-4 pt-2 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        {property && (
          <nav aria-label="Quick actions" className="rail -mx-4 mb-2 flex gap-2 overflow-x-auto px-4 pb-1">
            {QUICK_ACTIONS.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                type="button"
                disabled={chat.sending}
                onClick={() => quick(key)}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-sand-200 bg-white px-3 py-1.5 text-[13px] font-medium text-ink-soft transition hover:border-terracotta-500 hover:text-terracotta-700 disabled:opacity-50"
              >
                <Icon className="size-4" aria-hidden /> {label}
              </button>
            ))}
          </nav>
        )}
        <Composer
          onSend={chat.send}
          onStop={chat.stop}
          sending={chat.sending}
          disabled={chat.booting}
          placeholder={property ? "Ask me anything…" : "Or tell me where you're staying…"}
        />
        <p className="mt-1.5 text-center text-[11px] text-ink-muted">
          AI assistant · Information may change — confirm important details with your host.
        </p>
      </footer>

      {pinOpen && <PinDialog onClose={() => setPinOpen(false)} onVerify={chat.verifyPin} />}
    </div>
  );
}
