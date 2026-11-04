import { Clock, ExternalLink, FlaskConical, Heart, Leaf, Map, MapPin, Navigation, Wallet } from "lucide-react";
import type { Place } from "@/types";

function distance(km?: number | null) {
  if (km == null) return null;
  return km < 1 ? `${Math.round((km * 1000) / 10) * 10} m` : `${km.toFixed(1)} km`;
}

const SOURCE_BADGE: Record<Place["source"], { label: string; className: string; icon?: typeof Heart }> = {
  host_curated: { label: "Host pick", className: "bg-terracotta-50 text-terracotta-700", icon: Heart },
  osm: { label: "OpenStreetMap", className: "bg-sand-50 text-ink-soft", icon: Map },
  osm_snapshot: { label: "Saved map data", className: "bg-sand-50 text-ink-soft", icon: Map },
  sample: { label: "Sample · fictional", className: "bg-amber-100 text-amber-800", icon: FlaskConical },
};

export function PlaceCard({ place, index }: { place: Place; index: number }) {
  const d = distance(place.distance_km);
  const badge = SOURCE_BADGE[place.source];
  const BadgeIcon = badge.icon;
  return (
    <article
      className="animate-rise flex w-[16.5rem] shrink-0 flex-col rounded-2xl border border-sand-200 bg-white p-4 shadow-[0_1px_2px_rgba(42,33,27,0.04),0_8px_24px_-12px_rgba(42,33,27,0.18)] sm:w-auto"
      style={{ animationDelay: `${index * 60}ms` }}
      data-testid="place-card"
    >
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${badge.className}`}>
          {BadgeIcon && <BadgeIcon className="size-3" aria-hidden />} {badge.label}
        </span>
        {place.rating != null && <span className="text-xs font-semibold text-ink">★ {place.rating.toFixed(1)}</span>}
      </div>
      <h3 className="line-clamp-2 font-display text-[17px] leading-snug font-semibold text-ink">{place.name}</h3>
      {(place.cuisine || place.category) && (
        <p className="mt-0.5 truncate text-xs text-ink-muted">{[place.category, place.cuisine].filter(Boolean).join(" · ")}</p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-soft">
        {d && (
          <span className="inline-flex items-center gap-1">
            <MapPin className="size-3.5" aria-hidden /> {d} away
          </span>
        )}
        {place.open_now != null && (
          <span className={`inline-flex items-center gap-1 font-medium ${place.open_now ? "text-leaf-600" : "text-terracotta-700"}`}>
            <Clock className="size-3.5" aria-hidden /> {place.open_now ? "Open now" : "Closed now"}
          </span>
        )}
        {place.price_note && (
          <span className="inline-flex items-center gap-1" title={place.price_note}>
            <Wallet className="size-3.5" aria-hidden /> Budget
          </span>
        )}
        {place.diet && (
          <span className="inline-flex items-center gap-1 text-leaf-600">
            <Leaf className="size-3.5" aria-hidden /> {place.diet}
          </span>
        )}
      </div>

      {place.opening_hours && <p className="mt-1.5 truncate font-mono text-[11px] text-ink-muted" title={place.opening_hours}>{place.opening_hours}</p>}
      {(place.host_note || place.summary) && (
        <p className="mt-2 line-clamp-3 text-[13px] leading-relaxed text-ink-soft">
          {place.host_note ? <em>“{place.host_note}”</em> : place.summary}
        </p>
      )}
      {place.address && <p className="mt-1.5 line-clamp-1 text-[11px] text-ink-muted">{place.address}</p>}

      {(place.directions_url || place.osm_url) && (
        <div className="mt-auto flex gap-2 pt-3">
          {place.directions_url && (
            <a href={place.directions_url} target="_blank" rel="noopener noreferrer"
              className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-ink px-3 py-2 text-xs font-medium text-paper transition hover:bg-ink-soft">
              <Navigation className="size-3.5" aria-hidden /> Directions
            </a>
          )}
          {place.osm_url && (
            <a href={place.osm_url} target="_blank" rel="noopener noreferrer" aria-label={`View ${place.name} on OpenStreetMap`}
              className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-sand-200 px-3 py-2 text-xs font-medium text-ink transition hover:bg-sand-50">
              <ExternalLink className="size-3.5" aria-hidden /> Map
            </a>
          )}
        </div>
      )}
    </article>
  );
}

export function PlaceRail({ places, previous }: { places: Place[]; previous?: boolean }) {
  if (!places.length) return null;
  const osm = places.some((p) => p.source === "osm" || p.source === "osm_snapshot");
  return (
    <div className="mt-3">
      {previous && <p className="mb-1.5 text-[11px] font-medium tracking-wide text-ink-muted uppercase">From earlier</p>}
      <div className="rail -mx-4 flex gap-3 overflow-x-auto px-4 pb-2 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0">
        {places.map((p, i) => (
          <PlaceCard key={`${p.osm_id ?? p.name}-${i}`} place={p} index={i} />
        ))}
      </div>
      {osm && (
        <p className="mt-1 text-[10px] text-ink-muted">
          Map data ©{" "}
          <a className="underline" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">
            OpenStreetMap contributors
          </a>
        </p>
      )}
    </div>
  );
}
