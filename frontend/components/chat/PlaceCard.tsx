import { Clock, ExternalLink, Heart, MapPin, Navigation, Star } from "lucide-react";
import type { Place } from "@/types";

function distance(km?: number | null) {
  if (km == null) return null;
  return km < 1 ? `${Math.round((km * 1000) / 10) * 10} m` : `${km.toFixed(1)} km`;
}

function price(level?: number | null) {
  if (level == null || level === 0) return null;
  return "₹".repeat(level);
}

export function PlaceCard({ place, index }: { place: Place; index: number }) {
  const d = distance(place.distance_km);
  const isHost = place.source === "host_curated";
  const isMock = place.source === "mock";
  return (
    <article
      className="animate-rise flex w-[16.5rem] shrink-0 flex-col rounded-2xl border border-sand-200 bg-white p-4 shadow-[0_1px_2px_rgba(42,33,27,0.04),0_8px_24px_-12px_rgba(42,33,27,0.18)] sm:w-auto"
      style={{ animationDelay: `${index * 60}ms` }}
      data-testid="place-card"
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          {isHost && (
            <span className="mb-1 inline-flex items-center gap-1 rounded-full bg-terracotta-50 px-2 py-0.5 text-[11px] font-medium text-terracotta-700">
              <Heart className="size-3" aria-hidden /> Host pick
            </span>
          )}
          {isMock && (
            <span className="mb-1 inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800">
              Mock data
            </span>
          )}
          <h3 className="line-clamp-2 font-display text-[17px] leading-snug font-semibold text-ink">{place.name}</h3>
          {place.category && <p className="mt-0.5 truncate text-xs text-ink-muted">{place.category}</p>}
        </div>
        {place.rating != null && (
          <span className="flex shrink-0 items-center gap-1 rounded-lg bg-sand-50 px-2 py-1 text-sm font-semibold text-ink" title={`${place.user_rating_count ?? 0} reviews`}>
            <Star className="size-3.5 fill-saffron-400 text-saffron-400" aria-hidden />
            {place.rating.toFixed(1)}
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-soft">
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
        {price(place.price_level) && <span className="font-medium tracking-wider">{price(place.price_level)}</span>}
      </div>

      {(place.host_note || place.summary) && (
        <p className="mt-2 line-clamp-3 text-[13px] leading-relaxed text-ink-soft">
          {place.host_note ? <em>“{place.host_note}”</em> : place.summary}
        </p>
      )}
      {place.address && <p className="mt-2 line-clamp-1 text-[11px] text-ink-muted">{place.address}</p>}

      {!isMock && (place.directions_url || place.maps_url) && (
        <div className="mt-auto flex gap-2 pt-3">
          {place.directions_url && (
            <a
              href={place.directions_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-ink px-3 py-2 text-xs font-medium text-paper transition hover:bg-ink-soft"
            >
              <Navigation className="size-3.5" aria-hidden /> Directions
            </a>
          )}
          {place.maps_url && (
            <a
              href={place.maps_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-sand-200 px-3 py-2 text-xs font-medium text-ink transition hover:bg-sand-50"
              aria-label={`View ${place.name} on Google Maps`}
            >
              <ExternalLink className="size-3.5" aria-hidden /> Maps
            </a>
          )}
        </div>
      )}
    </article>
  );
}

export function PlaceRail({ places, previous }: { places: Place[]; previous?: boolean }) {
  if (!places.length) return null;
  return (
    <div className="mt-3">
      {previous && <p className="mb-1.5 text-[11px] font-medium tracking-wide text-ink-muted uppercase">From earlier</p>}
      <div className="rail -mx-4 flex gap-3 overflow-x-auto px-4 pb-2 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0">
        {places.map((p, i) => (
          <PlaceCard key={`${p.place_id ?? p.name}-${i}`} place={p} index={i} />
        ))}
      </div>
    </div>
  );
}
