"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Loader2, MapPin, Plus } from "lucide-react";
import { btnPrimary, card, PageTitle } from "@/components/admin/AdminShell";
import { adminApi } from "@/services/api";
import type { PropertyAdmin } from "@/types";

export function PropertiesView() {
  const [items, setItems] = useState<PropertyAdmin[] | null>(null);
  useEffect(() => {
    adminApi.properties().then(setItems).catch(() => setItems([]));
  }, []);

  return (
    <>
      <PageTitle
        title="Properties"
        subtitle="Each property has its own address, coordinates, discovery radius and isolated knowledge base."
        actions={
          <Link href="/admin/properties/new" className={btnPrimary}>
            <Plus className="size-4" aria-hidden /> Add property
          </Link>
        }
      />
      {!items ? (
        <Loader2 className="size-6 animate-spin text-ink-muted" aria-label="Loading" />
      ) : items.length === 0 ? (
        <div className={card}>No properties yet.</div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {items.map((p) => (
            <Link key={p.id} href={`/admin/properties/${p.id}`} className={`${card} block transition hover:border-terracotta-500/50 hover:shadow-md`} data-testid="admin-property">
              <div className="flex items-start justify-between gap-2">
                <h2 className="font-display text-lg font-semibold">{p.name}</h2>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${p.is_active ? "bg-green-50 text-leaf-600" : "bg-sand-100 text-ink-muted"}`}>
                  {p.is_active ? "Active" : "Inactive"}
                </span>
              </div>
              <p className="mt-1 flex items-center gap-1 text-sm text-ink-soft">
                <MapPin className="size-3.5" aria-hidden /> {p.location_name}
                {p.neighbourhood ? ` · ${p.neighbourhood}` : ""}
              </p>
              <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                {p.is_placeholder && <span className="rounded-full bg-amber-100 px-2 py-0.5 font-medium text-amber-800">Placeholder data</span>}
                {!p.has_coordinates && <span className="rounded-full bg-terracotta-50 px-2 py-0.5 font-medium text-terracotta-700">No coordinates</span>}
                {p.requires_pin && <span className="rounded-full bg-sand-100 px-2 py-0.5 font-medium text-ink-soft">Guest PIN</span>}
                <span className="rounded-full bg-sand-50 px-2 py-0.5 text-ink-soft">{p.document_count} docs · {p.chunk_count} chunks</span>
                <span className="rounded-full bg-sand-50 px-2 py-0.5 text-ink-soft">{(p.discovery_radius_m / 1000).toFixed(1)} km radius</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
