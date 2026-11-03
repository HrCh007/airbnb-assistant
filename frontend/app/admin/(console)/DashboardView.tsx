"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertTriangle, Building2, FileText, Loader2, MapPinOff, MessageCircle, Sparkles } from "lucide-react";
import { card, PageTitle } from "@/components/admin/AdminShell";
import { adminApi, ApiError } from "@/services/api";
import type { Dashboard } from "@/types";

function Stat({ icon: Icon, label, value, hint }: { icon: typeof Building2; label: string; value: number | string; hint?: string }) {
  return (
    <div className={card}>
      <div className="flex items-center gap-2 text-sm text-ink-soft">
        <Icon className="size-4" aria-hidden /> {label}
      </div>
      <p className="mt-2 font-display text-3xl font-semibold">{value}</p>
      {hint && <p className="mt-1 text-xs text-ink-muted">{hint}</p>}
    </div>
  );
}

export function DashboardView() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminApi.dashboard().then(setData).catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load"));
  }, []);

  if (error) return <p className="text-terracotta-700">{error}</p>;
  if (!data) return <Loader2 className="size-6 animate-spin text-ink-muted" aria-label="Loading" />;

  const warnings: string[] = [];
  if (data.placeholder_properties) warnings.push(`${data.placeholder_properties} propert${data.placeholder_properties === 1 ? "y uses" : "ies use"} placeholder data — replace it with real details.`);
  if (data.missing_coordinates) warnings.push(`${data.missing_coordinates} propert${data.missing_coordinates === 1 ? "y is" : "ies are"} missing coordinates, so local search can't run for them.`);
  if (data.documents_by_status.failed) warnings.push(`${data.documents_by_status.failed} document(s) failed to process.`);
  if (data.integrations.llm.startsWith("offline")) warnings.push("Claude is not configured (ANTHROPIC_API_KEY): the assistant runs in offline extractive mode.");
  if (data.integrations.places !== "google") warnings.push(`Local discovery provider is "${data.integrations.places}". Set GOOGLE_MAPS_API_KEY for live Google Places results.`);

  return (
    <>
      <PageTitle title="Dashboard" subtitle="An overview of your guest assistant." />
      {warnings.length > 0 && (
        <div className="mb-6 space-y-2 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          {warnings.map((w) => (
            <p key={w} className="flex gap-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /> {w}
            </p>
          ))}
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat icon={Building2} label="Active properties" value={data.properties} hint={data.inactive_properties ? `${data.inactive_properties} inactive` : undefined} />
        <Stat icon={FileText} label="Documents" value={data.documents} hint={`${data.chunks} knowledge chunks`} />
        <Stat icon={Sparkles} label="Host picks" value={data.curated_recommendations} />
        <Stat icon={MessageCircle} label="Conversations (7d)" value={data.conversations_7d} hint={`${data.messages_7d} messages`} />
      </div>
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <div className={card}>
          <h2 className="font-display text-lg font-semibold">What guests asked (7 days)</h2>
          {Object.keys(data.intents_7d).length === 0 ? (
            <p className="mt-2 text-sm text-ink-muted">No conversations yet.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {Object.entries(data.intents_7d)
                .sort((a, b) => b[1] - a[1])
                .map(([intent, n]) => {
                  const max = Math.max(...Object.values(data.intents_7d));
                  return (
                    <li key={intent} className="text-sm">
                      <div className="flex justify-between text-ink-soft">
                        <span>{intent.replace(/_/g, " ").toLowerCase()}</span>
                        <span className="font-medium text-ink">{n}</span>
                      </div>
                      <div className="mt-1 h-1.5 rounded-full bg-sand-100">
                        <div className="h-1.5 rounded-full bg-terracotta-500" style={{ width: `${(n / max) * 100}%` }} />
                      </div>
                    </li>
                  );
                })}
            </ul>
          )}
        </div>
        <div className={card}>
          <h2 className="font-display text-lg font-semibold">Integrations</h2>
          <dl className="mt-3 space-y-2 text-sm">
            {Object.entries(data.integrations).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4">
                <dt className="text-ink-soft">{({ llm: "LLM", embeddings: "Embeddings", places: "Places" } as Record<string, string>)[k] ?? k}</dt>
                <dd className="text-right font-medium">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-4 flex gap-2">
            <Link href="/admin/properties" className="text-sm font-medium text-terracotta-700 hover:underline">Manage properties →</Link>
          </div>
          {data.missing_coordinates > 0 && (
            <p className="mt-3 flex items-center gap-1.5 text-xs text-ink-muted">
              <MapPinOff className="size-3.5" aria-hidden /> Add coordinates for every property.
            </p>
          )}
        </div>
      </div>
    </>
  );
}
