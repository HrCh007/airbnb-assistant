import ReactMarkdown from "react-markdown";
import { AlertTriangle, Siren } from "lucide-react";
import type { ChatMessage } from "@/types";
import { PlaceRail } from "./PlaceCard";

export function TypingIndicator() {
  return (
    <span className="inline-flex items-center gap-1 py-1" role="status" aria-label="Assistant is typing">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-2 animate-bounce rounded-full bg-terracotta-500/70"
          style={{ animationDelay: `${i * 140}ms`, animationDuration: "900ms" }}
        />
      ))}
    </span>
  );
}

export function Avatar() {
  return (
    <div
      aria-hidden
      className="flex size-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-terracotta-500 to-saffron-400 font-display text-sm font-semibold text-white shadow-sm"
    >
      D
    </div>
  );
}

export function MessageBubble({ message, onSuggestion }: { message: ChatMessage; onSuggestion?: (s: string) => void }) {
  if (message.role === "user") {
    return (
      <div className="animate-rise flex justify-end" data-testid="user-message">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-terracotta-600 px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap text-white shadow-sm">
          {message.content}
        </div>
      </div>
    );
  }
  const emergency = message.intent === "EMERGENCY_QUERY";
  return (
    <div className="animate-rise flex gap-2.5" data-testid="assistant-message">
      <Avatar />
      <div className="min-w-0 flex-1">
        <div
          className={`inline-block max-w-full rounded-2xl rounded-tl-md border px-4 py-3 text-[15px] leading-relaxed shadow-[0_1px_2px_rgba(42,33,27,0.04)] ${
            message.error
              ? "border-terracotta-100 bg-terracotta-50 text-terracotta-700"
              : emergency
                ? "border-red-200 bg-red-50/60"
                : "border-sand-200 bg-white"
          }`}
        >
          {emergency && (
            <p className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold tracking-wide text-red-700 uppercase">
              <Siren className="size-4" aria-hidden /> Safety first
            </p>
          )}
          {message.pending && !message.content ? (
            <span className="flex items-center gap-2">
              <TypingIndicator />
              {message.status && <span className="text-[13px] text-ink-muted" data-testid="assistant-status">{message.status}</span>}
            </span>
          ) : (
            <div className="prose-chat" data-testid="assistant-text">
              {message.error && <AlertTriangle className="mr-1 mb-0.5 inline size-4" aria-hidden />}
              <ReactMarkdown
                components={{
                  a: ({ href, children }) => (
                    <a href={href} target="_blank" rel="noopener noreferrer">
                      {children}
                    </a>
                  ),
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>
        <PlaceRail places={message.places} previous={message.placesPrevious} />
        {!!message.suggestions.length && onSuggestion && (
          <div className="mt-2.5 flex flex-wrap gap-2" aria-label="Suggested follow-ups">
            {message.suggestions.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => onSuggestion(s)}
                className="rounded-full border border-sand-200 bg-white px-3 py-1.5 text-[13px] text-ink-soft transition hover:border-terracotta-500 hover:text-terracotta-700 active:scale-[0.98]"
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
