"use client";

import { useCallback, useEffect, useState } from "react";
import { Heart, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { adminApi, ApiError } from "@/services/api";
import type { Curated } from "@/types";
import { btnPrimary, btnSecondary, card, input, label } from "./AdminShell";

const CATEGORIES = [
  "restaurant", "cafe", "street_food", "bakery", "bar", "pub", "nightlife", "shopping", "market", "attraction",
  "park", "monument", "pharmacy", "hospital", "gym", "service", "experience", "other",
];

type Form = {
  name: string; category: string; note: string; address: string; latitude: string; longitude: string;
  osm_id: string; price_level: string; tags: string; is_active: boolean;
};

const blank: Form = { name: "", category: "restaurant", note: "", address: "", latitude: "", longitude: "", osm_id: "", price_level: "", tags: "", is_active: true };

const toForm = (c: Curated): Form => ({
  name: c.name, category: c.category, note: c.note ?? "", address: c.address ?? "",
  latitude: c.latitude?.toString() ?? "", longitude: c.longitude?.toString() ?? "", osm_id: c.osm_id ?? "",
  price_level: c.price_level?.toString() ?? "", tags: c.tags.join(", "), is_active: c.is_active,
});

export function CuratedPanel({ propertyId }: { propertyId: string }) {
  const [items, setItems] = useState<Curated[] | null>(null);
  const [form, setForm] = useState<Form>(blank);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => adminApi.curated(propertyId).then(setItems), [propertyId]);
  useEffect(() => {
    load();
  }, [load]);

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body = {
      property_id: propertyId,
      name: form.name.trim(),
      category: form.category,
      note: form.note.trim() || null,
      address: form.address.trim() || null,
      latitude: form.latitude ? Number(form.latitude) : null,
      longitude: form.longitude ? Number(form.longitude) : null,
      osm_id: form.osm_id.trim() || null,
      price_level: form.price_level ? Number(form.price_level) : null,
      tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
      is_active: form.is_active,
    };
    try {
      if (editing) await adminApi.updateCurated(editing, body);
      else await adminApi.createCurated(body);
      setForm(blank);
      setEditing(null);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={card}>
      <h2 className="font-display text-lg font-semibold">Host picks</h2>
      <p className="mb-4 text-sm text-ink-soft">
        Your own recommendations. They appear alongside OpenStreetMap results when a guest asks for something similar, with your note shown as a
        &quot;Host pick&quot;. Add the coordinates so the guest sees the distance.
      </p>
      {!items ? (
        <Loader2 className="size-5 animate-spin text-ink-muted" />
      ) : items.length === 0 ? (
        <p className="mb-4 text-sm text-ink-muted">No host picks yet.</p>
      ) : (
        <ul className="mb-5 divide-y divide-sand-100">
          {items.map((c) => (
            <li key={c.id} className="flex items-start justify-between gap-3 py-3" data-testid="curated-row">
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-1.5 font-medium">
                  <Heart className="size-3.5 text-terracotta-600" aria-hidden /> {c.name}
                  <span className="rounded-full bg-sand-50 px-2 py-0.5 text-[11px] font-normal text-ink-soft">{c.category}</span>
                  {!c.is_active && <span className="rounded-full bg-sand-100 px-2 py-0.5 text-[11px] font-normal text-ink-muted">hidden</span>}
                </p>
                {c.note && <p className="mt-0.5 text-sm text-ink-soft italic">“{c.note}”</p>}
                {c.address && <p className="text-xs text-ink-muted">{c.address}</p>}
              </div>
              <div className="flex shrink-0 gap-1">
                <button type="button" aria-label={`Edit ${c.name}`} className="rounded-lg p-1.5 text-ink-soft hover:bg-sand-50"
                  onClick={() => { setEditing(c.id); setForm(toForm(c)); }}>
                  <Pencil className="size-4" />
                </button>
                <button type="button" aria-label={`Delete ${c.name}`} className="rounded-lg p-1.5 text-terracotta-700 hover:bg-terracotta-50"
                  onClick={async () => { if (confirm(`Delete "${c.name}"?`)) { await adminApi.deleteCurated(c.id); load(); } }}>
                  <Trash2 className="size-4" />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={submit} className="grid gap-3 rounded-xl bg-sand-50/60 p-4 sm:grid-cols-2">
        <p className="text-sm font-medium sm:col-span-2">{editing ? "Edit host pick" : "Add a host pick"}</p>
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
          <label className={label} htmlFor="c-note">Host note (why you recommend it)</label>
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
          <label className={label} htmlFor="c-price">Price level (your estimate)</label>
          <select id="c-price" className={input} value={form.price_level} onChange={(e) => set("price_level", e.target.value)}>
            <option value="">Not specified</option>
            <option value="1">₹ budget</option>
            <option value="2">₹₹ moderate</option>
            <option value="3">₹₹₹ pricey</option>
            <option value="4">₹₹₹₹ premium</option>
          </select>
        </div>
        <div>
          <label className={label} htmlFor="c-osm">OpenStreetMap ID (optional, e.g. node/123456)</label>
          <input id="c-osm" className={input} value={form.osm_id} onChange={(e) => set("osm_id", e.target.value)} placeholder="node/123456" />
        </div>
        <div className="sm:col-span-2">
          <label className={label} htmlFor="c-tags">Tags (comma separated)</label>
          <input id="c-tags" className={input} value={form.tags} onChange={(e) => set("tags", e.target.value)} placeholder="family, vegetarian, late night" />
        </div>
        <label className="flex items-center gap-2 text-sm sm:col-span-2">
          <input type="checkbox" checked={form.is_active} onChange={(e) => set("is_active", e.target.checked)} className="size-4 accent-terracotta-600" />
          Show to guests
        </label>
        {error && <p className="text-sm text-terracotta-700 sm:col-span-2">{error}</p>}
        <div className="flex gap-2 sm:col-span-2">
          <button type="submit" disabled={busy} className={btnPrimary}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />} {editing ? "Save changes" : "Add host pick"}
          </button>
          {editing && (
            <button type="button" className={btnSecondary} onClick={() => { setEditing(null); setForm(blank); }}>
              <X className="size-4" /> Cancel
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
