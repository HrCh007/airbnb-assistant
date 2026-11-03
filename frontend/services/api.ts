import type {
  Curated,
  Dashboard,
  DocumentRow,
  Location,
  PropertyAdmin,
  PropertySummary,
  ServiceStatus,
  Session,
} from "@/types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, cache: "no-store" });
  } catch {
    throw new ApiError(0, "Can't reach the assistant right now. Please check your connection and try again.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : Array.isArray(body.detail) ? body.detail.map((d: { msg: string }) => d.msg).join("; ") : detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const json = (body: unknown, headers: Record<string, string> = {}): RequestInit => ({
  headers: { "Content-Type": "application/json", ...headers },
  body: JSON.stringify(body),
});

// ------------------------------------------------------------------ guest API
export const guestApi = {
  status: () => request<ServiceStatus>("/api/status"),
  locations: () => request<Location[]>("/api/locations"),
  createSession: () => request<Session>("/api/chat/sessions", { method: "POST", ...json({}) }),
  getSession: (id: string, token: string) =>
    request<Session>(`/api/chat/sessions/${id}`, { headers: { "X-Session-Token": token } }),
  select: (id: string, token: string, body: { location_slug?: string; property_id?: string }) =>
    request<Session>(`/api/chat/sessions/${id}/selection`, { method: "PUT", ...json(body, { "X-Session-Token": token }) }),
  verifyPin: (id: string, token: string, pin: string) =>
    request<{ verified: boolean }>(`/api/chat/sessions/${id}/verify-pin`, { method: "POST", ...json({ pin }, { "X-Session-Token": token }) }),
  messages: (id: string, token: string) =>
    request<
      { id: string; role: "user" | "assistant"; content: string; intent?: string; places: never[]; suggestions: string[]; created_at: string }[]
    >(`/api/chat/sessions/${id}/messages`, { headers: { "X-Session-Token": token } }),
  clear: (id: string, token: string) =>
    request<void>(`/api/chat/sessions/${id}/messages`, { method: "DELETE", headers: { "X-Session-Token": token } }),
};

export interface StreamHandlers {
  onEvent: (event: string, data: unknown) => void;
  signal?: AbortSignal;
}

/** POST a chat message and parse the Server-Sent Events response stream. */
export async function streamChat(sessionId: string, token: string, message: string, handlers: StreamHandlers) {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/chat/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Session-Token": token, Accept: "text/event-stream" },
      body: JSON.stringify({ session_id: sessionId, message }),
      signal: handlers.signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Can't reach the assistant right now. Please check your connection and try again.");
  }
  if (!res.ok || !res.body) {
    let detail = "Something went wrong. Please try again.";
    if (res.status === 429) detail = "You're sending messages a bit fast — please wait a moment.";
    else if (res.status === 404) detail = "Your session expired. Please start a new chat.";
    else {
      try {
        const b = await res.json();
        if (typeof b.detail === "string") detail = b.detail;
      } catch {
        /* ignore */
      }
    }
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
      }
      if (!dataLines.length) continue;
      let data: unknown = dataLines.join("\n");
      try {
        data = JSON.parse(data as string);
      } catch {
        /* keep raw */
      }
      handlers.onEvent(event, data);
    }
  }
}

// ------------------------------------------------------------------ admin API
const TOKEN_KEY = "dsa_admin_token";

export const adminAuth = {
  get: () => (typeof window === "undefined" ? null : window.localStorage.getItem(TOKEN_KEY)),
  set: (t: string) => window.localStorage.setItem(TOKEN_KEY, t),
  clear: () => window.localStorage.removeItem(TOKEN_KEY),
};

async function adminRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = adminAuth.get();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  try {
    return await request<T>(path, { ...init, headers });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      adminAuth.clear();
      if (typeof window !== "undefined" && !window.location.pathname.startsWith("/admin/login")) {
        // Hard navigation on purpose: this module is outside React, and a full
        // reload clears any in-memory admin state after the token expires.
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.href = "/admin/login";
      }
    }
    throw e;
  }
}

export const adminApi = {
  login: (email: string, password: string) =>
    request<{ access_token: string; expires_at: string }>("/api/admin/login", { method: "POST", ...json({ email, password }) }),
  me: () => adminRequest<{ email: string; full_name?: string }>("/api/admin/me"),
  dashboard: () => adminRequest<Dashboard>("/api/admin/dashboard"),
  locations: () => adminRequest<Location[]>("/api/admin/locations"),
  createLocation: (body: { slug: string; name: string; tagline?: string }) =>
    adminRequest<Location>("/api/admin/locations", { method: "POST", ...json(body) }),
  properties: () => adminRequest<PropertyAdmin[]>("/api/admin/properties"),
  property: (id: string) => adminRequest<PropertyAdmin>(`/api/admin/properties/${id}`),
  createProperty: (body: unknown) => adminRequest<PropertyAdmin>("/api/admin/properties", { method: "POST", ...json(body) }),
  updateProperty: (id: string, body: unknown) =>
    adminRequest<PropertyAdmin>(`/api/admin/properties/${id}`, { method: "PUT", ...json(body) }),
  deleteProperty: (id: string, hard = false) =>
    adminRequest<void>(`/api/admin/properties/${id}?hard=${hard}`, { method: "DELETE" }),
  documents: (propertyId?: string) =>
    adminRequest<DocumentRow[]>(`/api/admin/documents${propertyId ? `?property_id=${propertyId}` : ""}`),
  upload: (propertyId: string, file: File) => {
    const fd = new FormData();
    fd.append("property_id", propertyId);
    fd.append("file", file);
    return adminRequest<DocumentRow>("/api/admin/documents/upload", { method: "POST", body: fd });
  },
  deleteDocument: (id: string) => adminRequest<void>(`/api/admin/documents/${id}`, { method: "DELETE" }),
  reindex: (id: string) => adminRequest<DocumentRow>(`/api/admin/documents/${id}/reindex`, { method: "POST" }),
  chunks: (id: string) =>
    adminRequest<{ id: string; index: number; section?: string; content: string; is_sensitive: boolean }[]>(
      `/api/admin/documents/${id}/chunks`,
    ),
  curated: (propertyId: string) => adminRequest<Curated[]>(`/api/admin/curated?property_id=${propertyId}`),
  createCurated: (body: unknown) => adminRequest<Curated>("/api/admin/curated", { method: "POST", ...json(body) }),
  updateCurated: (id: string, body: unknown) => adminRequest<Curated>(`/api/admin/curated/${id}`, { method: "PUT", ...json(body) }),
  deleteCurated: (id: string) => adminRequest<void>(`/api/admin/curated/${id}`, { method: "DELETE" }),
};

export type { PropertySummary };
