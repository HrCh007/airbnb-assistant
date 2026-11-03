import { ArrowRight, BedDouble, Building2, Loader2, Plane, ShoppingBag, Trees, Users } from "lucide-react";
import type { Location, PropertySummary } from "@/types";

const LOOKS: Record<string, { icon: typeof Plane; gradient: string }> = {
  dwarka: { icon: Plane, gradient: "from-[#f3dccb] via-[#f7ebe0] to-white" },
  janakpuri: { icon: Trees, gradient: "from-[#dfe8d6] via-[#eef3e8] to-white" },
  saket: { icon: ShoppingBag, gradient: "from-[#f6d9cf] via-[#fbeae2] to-white" },
};

export function LocationCards({
  locations,
  onSelect,
  busy,
}: {
  locations: Location[];
  onSelect: (slug: string) => void;
  busy: boolean;
}) {
  if (!locations.length) {
    return <p className="text-sm text-ink-muted">No locations are configured yet.</p>;
  }
  return (
    <div className="grid gap-3 sm:grid-cols-3" role="list" aria-label="Choose your neighbourhood">
      {locations.map((loc, i) => {
        const look = LOOKS[loc.slug] ?? { icon: Building2, gradient: "from-sand-100 via-sand-50 to-white" };
        const Icon = look.icon;
        return (
          <button
            key={loc.id}
            role="listitem"
            type="button"
            disabled={busy || loc.property_count === 0}
            onClick={() => onSelect(loc.slug)}
            data-testid={`location-${loc.slug}`}
            className={`group animate-rise relative flex items-center gap-4 overflow-hidden rounded-2xl border border-sand-200 bg-gradient-to-br ${look.gradient} p-4 text-left shadow-[0_1px_2px_rgba(42,33,27,0.04)] transition hover:-translate-y-0.5 hover:border-terracotta-500/60 hover:shadow-[0_12px_30px_-14px_rgba(143,64,36,0.45)] focus-visible:ring-2 focus-visible:ring-terracotta-500 focus-visible:outline-none active:scale-[0.99] disabled:opacity-60 sm:flex-col sm:items-start sm:p-5`}
            style={{ animationDelay: `${i * 70}ms` }}
          >
            <span className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-white/80 text-terracotta-600 shadow-sm">
              <Icon className="size-6" aria-hidden />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block font-display text-xl font-semibold text-ink">{loc.name}</span>
              {loc.tagline && <span className="mt-0.5 block text-[13px] leading-snug text-ink-soft">{loc.tagline}</span>}
            </span>
            <ArrowRight className="size-5 shrink-0 text-ink-muted transition group-hover:translate-x-0.5 group-hover:text-terracotta-600 sm:absolute sm:top-5 sm:right-5" aria-hidden />
          </button>
        );
      })}
    </div>
  );
}

export function PropertyCards({
  properties,
  onSelect,
  busy,
}: {
  properties: PropertySummary[];
  onSelect: (id: string) => void;
  busy: boolean;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {properties.map((p) => (
        <button
          key={p.id}
          type="button"
          disabled={busy}
          onClick={() => onSelect(p.id)}
          data-testid="property-card"
          className="animate-rise rounded-2xl border border-sand-200 bg-white p-4 text-left transition hover:border-terracotta-500/60 hover:shadow-md focus-visible:ring-2 focus-visible:ring-terracotta-500 focus-visible:outline-none"
        >
          <span className="block font-display text-lg font-semibold">{p.name}</span>
          {p.neighbourhood && <span className="block text-sm text-ink-soft">{p.neighbourhood}</span>}
          <span className="mt-2 flex gap-3 text-xs text-ink-muted">
            {p.bedrooms != null && (
              <span className="inline-flex items-center gap-1">
                <BedDouble className="size-3.5" aria-hidden /> {p.bedrooms} bed
              </span>
            )}
            {p.max_guests != null && (
              <span className="inline-flex items-center gap-1">
                <Users className="size-3.5" aria-hidden /> up to {p.max_guests}
              </span>
            )}
          </span>
        </button>
      ))}
      {busy && <Loader2 className="size-5 animate-spin text-ink-muted" aria-label="Loading" />}
    </div>
  );
}
