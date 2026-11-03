"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, Square } from "lucide-react";

export function Composer({
  onSend,
  onStop,
  sending,
  disabled,
  placeholder,
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  sending: boolean;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [value]);

  const submit = () => {
    if (!value.trim() || sending || disabled) return;
    onSend(value);
    setValue("");
  };

  return (
    <form
      className="flex items-end gap-2 rounded-[1.4rem] border border-sand-200 bg-white p-1.5 pl-4 shadow-[0_8px_30px_-16px_rgba(42,33,27,0.35)] focus-within:border-terracotta-500/60"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <label htmlFor="chat-input" className="sr-only">
        Message
      </label>
      <textarea
        id="chat-input"
        ref={ref}
        rows={1}
        value={value}
        maxLength={1500}
        disabled={disabled}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={placeholder ?? "Ask about your stay or the neighbourhood…"}
        className="max-h-36 min-h-[2.5rem] flex-1 resize-none bg-transparent py-2 text-[16px] leading-6 text-ink placeholder:text-ink-muted focus:outline-none disabled:opacity-60"
      />
      {sending ? (
        <button
          type="button"
          onClick={onStop}
          aria-label="Stop generating"
          className="flex size-10 shrink-0 items-center justify-center rounded-full bg-sand-100 text-ink transition hover:bg-sand-200"
        >
          <Square className="size-4 fill-current" />
        </button>
      ) : (
        <button
          type="submit"
          aria-label="Send message"
          disabled={!value.trim() || disabled}
          className="flex size-10 shrink-0 items-center justify-center rounded-full bg-terracotta-600 text-white transition hover:bg-terracotta-700 active:scale-95 disabled:bg-sand-200 disabled:text-ink-muted"
        >
          <ArrowUp className="size-5" />
        </button>
      )}
    </form>
  );
}
