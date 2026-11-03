"use client";

import { useCallback, useEffect, useState } from "react";
import { Heart, Loader2, Plus, Trash2 } from "lucide-react";
import { adminApi, ApiError } from "@/services/api";
import type { Curated } from "@/types";
import { btnPrimary, card, input, label } from "./AdminShell";

const CATEGORIES = ["restaurant", "cafe", "street_food", "bar", "nightlife", "shopping", "market", "attraction", "park", "pharmacy", "hospital", "service", "experience", "other"];

const blank = { name: "", category: "restaurant", note: "", address: "", latitude: "", longitude: "", google_place_id: "", tags: "" };

export function CuratedPanel({ propertyId }: { propertyId: string }) {
  const [items, setItems] = useState<Curated[] | null>(null);
  const [form, setForm] = useState(blank);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => adminApi.curated(propertyId).then(setItems), [propertyId]);
  useEffect(() => {
    load();
  }, [load]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await adminApi.createCurated({
        property_id: propertyId,
        name: form.name.trim(),
        category: form.category,
        note: form.note.trim() || null,
        address: form.address.trim() || null,
        latitude: form.latitude ? Number(form.latitude) : null,
        longitude: form.longitude ? Number(form.longitude) : null,
        google_place_id: form.google_place_id.trim() || null,
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
      });
      setForm(blank);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  };

  const set = (k: keyof typeof blank, v: string) => setForm((f) => ({ ...f, [k]: v }));

  return (
    <section className={card}>
      <h2 className="font-display text-lg font-semibold">Host picks</h2>
      <p className="mb-4 text-sm text-ink-soft">
        Your personal recommendations, shown alongside live Google Places results when relevant. Add a short note on why you love each place.
      </p>
      {!items ? (
        <Loader2 className="size-5 animate-spin text-ink-muted" />
      ) : items.length === 0 ? (
        <p className="mb-4 text-sm text-ink-muted">No host picks yet.</p>
      ) : (
        <ul className="mb-5 divide-y divide-sand-100">
          {items.map((c) => (
            <li key={c.id} className="flex items-start justify-between gap-3 py-3">
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 font-medium"><Heart className="size-3.5 text-terracotta-600" aria-hidden /> {c.name}
                  <span className="rounded-full bg-sand-50 px-2 py-0.5 text-[11px] font-normal text-ink-soft">{c.category}</span></p>
                {c.note && <p className="mt-0.5 text-sm text-ink-soft italic">“{c.note}”</p>}
                {c.address && <p className="text-xs text-ink-muted">{c.address}</p>}
              </div>
              <button type="button" aria-label={`Delete ${c.name}`} className="rounded-lg p-1.5 text-terracotta-700 hover:bg-terracotta-50"
                onClick={async () => { await adminApi.deleteCurated(c.id); load(); }}>
                <Trash2 className="size-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={submit} className="grid gap-3 rounded-xl bg-sand-50/60 p-4 sm:grid-cols-2">
        <div>
          <label className={label} htmlFor="c-name">Name *</label>
          <input id="c-name" required minLength={2} className={input} value={form.name} onChange={(e) => set("name", e.target.value)} />
        </div>
        <div>
          <label className={label} htmlFor="c-cat">Category *</label>
          <select id="c-cat" className={input} value={form.category} onChange={(e) => set("category", e.target.value)}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c.replace("_", " ")}</option>)}
          </select>
        </div>
        <div className="sm:col-span-2">
          <label className={label} htmlFor="c-note">Why you recommend it</label>
          <textarea id="c-note" rows={2} className={input} value={form.note} onChange={(e) => set("note", e.target.value)} />
        </div>
        <div className="sm:col-span-2">
          <label className={label} htmlFor="c-addr">Address</label>
          <input id="c-addr" className={input} value={form.address} onChange={(e) => set("address", e.target.value)} />
        </div>
        <div>
          <label className={label} htmlFor="c-lat">Latitude</label>
          <input id="c-lat" inputMode="decimal" className={input} value={form.latitude} onChange={(e) => set("latitude", e.target.value)} />
        </div>
        <div>
          <label className={label} htmlFor="c-lng">Longitude</label>
          <input id="c-lng" inputMode="decimal" className={input} value={form.longitude} onChange={(e) => set("longitude", e.target.value)} />
        </div>
        <div>
          <label className={label} htmlFor="c-pid">Google Place ID (optional)</label>
          <input id="c-pid" className={input} value={form.google_place_id} onChange={(e) => set("google_place_id", e.target.value)} />
        </div>
        <div>
          <label className={label} htmlFor="c-tags">Tags (comma separated)</label>
          <input id="c-tags" className={input} value={form.tags} onChange={(e) => set("tags", e.target.value)} placeholder="family, vegetarian, late night" />
        </div>
        {error && <p className="text-sm text-terracotta-700 sm:col-span-2">{error}</p>}
        <div className="sm:col-span-2">
          <button type="submit" disabled={busy} className={btnPrimary}>{busy ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />} Add host pick</button>
        </div>
      </form>
    </section>
  );
}
