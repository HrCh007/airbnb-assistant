"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, guestApi, streamChat } from "@/services/api";
import type { ChatMessage, Intent, Location, Place, ServiceStatus, Session } from "@/types";

const SESSION_KEY = "dsa_session";

type Stored = { id: string; token: string };

function loadStored(): Stored | null {
  try {
    const raw = window.localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Stored) : null;
  } catch {
    return null;
  }
}

const uid = () => Math.random().toString(36).slice(2);

export function useChat() {
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [locations, setLocations] = useState<Location[] | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [booting, setBooting] = useState(true);
  const [sending, setSending] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tokenRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // ---------------------------------------------------------------- boot
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [st, locs] = await Promise.all([guestApi.status(), guestApi.locations()]);
        if (cancelled) return;
        setStatus(st);
        setLocations(locs);
        const stored = loadStored();
        if (stored) {
          try {
            const [s, msgs] = await Promise.all([
              guestApi.getSession(stored.id, stored.token),
              guestApi.messages(stored.id, stored.token),
            ]);
            if (cancelled) return;
            tokenRef.current = stored.token;
            setSession(s);
            setMessages(
              msgs.map((m) => ({
                id: m.id,
                role: m.role,
                content: m.content,
                intent: (m.intent as Intent) ?? null,
                places: m.places ?? [],
                suggestions: m.suggestions ?? [],
                created_at: m.created_at,
              })),
            );
          } catch {
            window.localStorage.removeItem(SESSION_KEY);
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : "Couldn't load the assistant.");
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const ensureSession = useCallback(async (): Promise<{ id: string; token: string }> => {
    if (session && tokenRef.current) return { id: session.session_id, token: tokenRef.current };
    const s = await guestApi.createSession();
    tokenRef.current = s.session_token!;
    window.localStorage.setItem(SESSION_KEY, JSON.stringify({ id: s.session_id, token: s.session_token }));
    setSession(s);
    return { id: s.session_id, token: s.session_token! };
  }, [session]);

  // ---------------------------------------------------------------- selection
  const selectLocation = useCallback(
    async (slug: string) => {
      setSelecting(true);
      setError(null);
      try {
        const { id, token } = await ensureSession();
        setSession(await guestApi.select(id, token, { location_slug: slug }));
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Couldn't select that location.");
      } finally {
        setSelecting(false);
      }
    },
    [ensureSession],
  );

  const selectProperty = useCallback(
    async (propertyId: string) => {
      setSelecting(true);
      try {
        const { id, token } = await ensureSession();
        setSession(await guestApi.select(id, token, { property_id: propertyId }));
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Couldn't select that property.");
      } finally {
        setSelecting(false);
      }
    },
    [ensureSession],
  );

  const changeLocation = useCallback(() => {
    // Keep the session (and preferences) but go back to the location picker.
    setSession((s) => (s ? { ...s, location_slug: null, location_name: null, property: null, available_properties: [] } : s));
  }, []);

  // ---------------------------------------------------------------- messaging
  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message || sending) return;
      setError(null);
      let ids: { id: string; token: string };
      try {
        ids = await ensureSession();
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Couldn't start a chat.");
        return;
      }
      const userMsg: ChatMessage = { id: uid(), role: "user", content: message, places: [], suggestions: [] };
      const botId = uid();
      const botMsg: ChatMessage = { id: botId, role: "assistant", content: "", places: [], suggestions: [], pending: true };
      setMessages((m) => [...m.map((x) => ({ ...x, suggestions: [] })), userMsg, botMsg]);
      setSending(true);
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const patch = (fn: (m: ChatMessage) => ChatMessage) =>
        setMessages((all) => all.map((m) => (m.id === botId ? fn(m) : m)));
      try {
        await streamChat(ids.id, ids.token, message, {
          signal: ctrl.signal,
          onEvent: (event, data) => {
            switch (event) {
              case "token":
                patch((m) => ({ ...m, content: m.content + (data as string), pending: false }));
                break;
              case "places": {
                const d = data as { previous: boolean; items: Place[] };
                patch((m) => ({ ...m, places: d.items, placesPrevious: d.previous }));
                break;
              }
              case "meta":
                patch((m) => ({ ...m, intent: (data as { intent: Intent }).intent }));
                break;
              case "session":
                setSession((s) => ({ ...(data as Session), session_token: s?.session_token }));
                break;
              case "suggestions":
                patch((m) => ({ ...m, suggestions: data as string[] }));
                break;
              case "done":
                patch((m) => ({ ...m, id: (data as { message_id: string }).message_id ?? m.id, pending: false }));
                break;
              case "error":
                patch((m) => ({
                  ...m,
                  pending: false,
                  error: true,
                  content: m.content || (data as { message: string }).message,
                }));
                break;
            }
          },
        });
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          const msg = e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
          patch((m) => ({ ...m, pending: false, error: true, content: m.content || msg }));
        }
      } finally {
        patch((m) => ({ ...m, pending: false }));
        setSending(false);
        abortRef.current = null;
      }
    },
    [ensureSession, sending],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const clear = useCallback(async () => {
    abortRef.current?.abort();
    if (session && tokenRef.current) {
      try {
        await guestApi.clear(session.session_id, tokenRef.current);
      } catch {
        /* non-fatal */
      }
    }
    setMessages([]);
  }, [session]);

  const verifyPin = useCallback(
    async (pin: string) => {
      if (!session || !tokenRef.current) return false;
      try {
        await guestApi.verifyPin(session.session_id, tokenRef.current, pin);
        setSession((s) => (s ? { ...s, pin_verified: true } : s));
        return true;
      } catch {
        return false;
      }
    },
    [session],
  );

  return {
    status,
    locations,
    session,
    messages,
    booting,
    sending,
    selecting,
    error,
    setError,
    send,
    stop,
    clear,
    selectLocation,
    selectProperty,
    changeLocation,
    verifyPin,
  };
}
