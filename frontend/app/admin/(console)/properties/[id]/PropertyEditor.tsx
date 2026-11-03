"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, Loader2, PowerOff, Trash2 } from "lucide-react";
import { btnSecondary, PageTitle } from "@/components/admin/AdminShell";
import { CuratedPanel } from "@/components/admin/CuratedPanel";
import { DocumentsPanel } from "@/components/admin/DocumentsPanel";
import { PropertyForm } from "@/components/admin/PropertyForm";
import { adminApi, ApiError } from "@/services/api";
import type { PropertyAdmin } from "@/types";

const TABS = ["Details", "Documents", "Host picks"] as const;

export function PropertyEditor({ id }: { id: string }) {
  const router = useRouter();
  const [property, setProperty] = useState<PropertyAdmin | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Details");

  useEffect(() => {
    adminApi.property(id).then(setProperty).catch((e) => setError(e instanceof ApiError ? e.message : "Not found"));
  }, [id]);

  if (error) return <p className="text-terracotta-700">{error}</p>;
  if (!property) return <Loader2 className="size-6 animate-spin text-ink-muted" aria-label="Loading" />;

  return (
    <>
      <Link href="/admin/properties" className="mb-3 inline-flex items-center gap-1 text-sm text-ink-soft hover:text-ink">
        <ArrowLeft className="size-4" /> All properties
      </Link>
      <PageTitle
        title={property.name}
        subtitle={`${property.location_name} · ${property.document_count} documents · ${property.chunk_count} knowledge chunks`}
        actions={
          <div className="flex gap-2">
            {property.is_active && (
              <button type="button" className={btnSecondary} onClick={async () => {
                if (confirm("Deactivate this property? Guests will no longer see it.")) {
                  await adminApi.deleteProperty(property.id);
                  setProperty({ ...property, is_active: false });
                }
              }}>
                <PowerOff className="size-4" /> Deactivate
              </button>
            )}
            <button type="button" className={`${btnSecondary} text-terracotta-700`} onClick={async () => {
              if (confirm("Permanently delete this property, its documents and knowledge? This cannot be undone.")) {
                await adminApi.deleteProperty(property.id, true);
                router.replace("/admin/properties");
              }
            }}>
              <Trash2 className="size-4" /> Delete
            </button>
          </div>
        }
      />
      <div role="tablist" className="mb-5 flex gap-1 rounded-xl bg-sand-100/70 p-1 sm:inline-flex">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} type="button" onClick={() => setTab(t)}
            className={`flex-1 rounded-lg px-4 py-1.5 text-sm font-medium transition sm:flex-none ${tab === t ? "bg-white text-ink shadow-sm" : "text-ink-soft"}`}>
            {t}
          </button>
        ))}
      </div>
      {tab === "Details" && <PropertyForm property={property} onSaved={setProperty} />}
      {tab === "Documents" && <DocumentsPanel propertyId={property.id} />}
      {tab === "Host picks" && <CuratedPanel propertyId={property.id} />}
    </>
  );
}
