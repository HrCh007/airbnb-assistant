"use client";

import { useEffect, useState } from "react";
import { Loader2, MapPin, Save } from "lucide-react";
import { adminApi, ApiError } from "@/services/api";
import type { Location, PropertyAdmin, PropertyDetails } from "@/types";
import { btnPrimary, card, input, label } from "./AdminShell";

const SECTIONS: { key: keyof PropertyDetails; title: string; fields: [string, string, boolean?][] }[] = [
  {
    key: "check_in_out",
    title: "Check-in & check-out",
    fields: [
      ["check_in_time", "Check-in time"],
      ["check_out_time", "Check-out time"],
      ["early_check_in_policy", "Early check-in policy", true],
      ["late_check_out_policy", "Late check-out policy", true],
      ["self_check_in_instructions", "Self check-in instructions (sensitive)", true],
      ["key_collection_instructions", "Key collection instructions (sensitive)", true],
    ],
  },
  {
    key: "facilities",
    title: "Facilities",
    fields: [
      ["wifi_name", "Wi-Fi name"],
      ["wifi_password", "Wi-Fi password (sensitive)"],
      ["air_conditioning", "Air conditioning"],
      ["heating", "Heating"],
      ["kitchen", "Kitchen facilities"],
      ["refrigerator", "Refrigerator"],
      ["washing_machine", "Washing machine"],
      ["television", "Television"],
      ["parking", "Parking"],
      ["lift", "Lift"],
      ["power_backup", "Power backup"],
      ["drinking_water", "Drinking water"],
      ["housekeeping", "Housekeeping", true],
    ],
  },
  {
    key: "house_rules",
    title: "House rules",
    fields: [
      ["smoking_policy", "Smoking policy"],
      ["pet_policy", "Pet policy"],
      ["visitors_policy", "Visitors policy"],
      ["noise_restrictions", "Noise restrictions"],
      ["parties_events_policy", "Parties & events"],
      ["security_instructions", "Security instructions", true],
      ["garbage_disposal", "Garbage disposal", true],
      ["other_restrictions", "Other restrictions", true],
    ],
  },
  {
    key: "emergency",
    title: "Emergency information",
    fields: [
      ["host_contact", "Host contact"],
      ["property_manager_contact", "Property manager contact"],
      ["emergency_exit_instructions", "Emergency exit instructions", true],
      ["nearby_hospital", "Nearby hospital"],
      ["emergency_numbers", "Emergency numbers"],
    ],
  },
  {
    key: "neighbourhood",
    title: "Local neighbourhood",
    fields: [
      ["nearest_metro_station", "Nearest metro station"],
      ["nearest_bus_stop", "Nearest bus stop"],
      ["airport_connectivity", "Airport connectivity", true],
      ["nearby_shopping", "Nearby shopping", true],
      ["nearby_restaurants", "Nearby restaurants", true],
      ["nearby_medical", "Nearby medical facilities", true],
      ["nearby_attractions", "Nearby attractions", true],
    ],
  },
];

type Form = {
  name: string;
  location_slug: string;
  address: string;
  neighbourhood: string;
  latitude: string;
  longitude: string;
  discovery_radius_m: string;
  bedrooms: string;
  bathrooms: string;
  max_guests: string;
  description: string;
  amenities: string;
  guest_pin: string;
  is_placeholder: boolean;
  is_active: boolean;
  details: PropertyDetails;
};

const emptyDetails = (): PropertyDetails => ({ check_in_out: {}, facilities: {}, house_rules: {}, emergency: {}, neighbourhood: {} });

function toForm(p?: PropertyAdmin | null): Form {
  return {
    name: p?.name ?? "",
    location_slug: p?.location_slug ?? "",
    address: p?.address ?? "",
    neighbourhood: p?.neighbourhood ?? "",
    latitude: p?.latitude?.toString() ?? "",
    longitude: p?.longitude?.toString() ?? "",
    discovery_radius_m: (p?.discovery_radius_m ?? 3000).toString(),
    bedrooms: p?.bedrooms?.toString() ?? "",
    bathrooms: p?.bathrooms?.toString() ?? "",
    max_guests: p?.max_guests?.toString() ?? "",
    description: p?.description ?? "",
    amenities: (p?.amenities ?? []).join(", "),
    guest_pin: "",
    is_placeholder: p?.is_placeholder ?? false,
    is_active: p?.is_active ?? true,
    details: { ...emptyDetails(), ...(p?.details ?? {}) },
  };
}

function GeocodeHelper({ address, onPick }: { address: string; onPick: (lat: number, lng: number) => void }) {
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<{ display_name: string; latitude: number; longitude: number }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="mb-4">
      <button
        type="button"
        className="inline-flex items-center gap-1.5 rounded-xl border border-sand-200 bg-white px-3 py-1.5 text-sm font-medium text-ink hover:bg-sand-50 disabled:opacity-50"
        disabled={busy || address.trim().length < 5}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            const r = await adminApi.geocode(address);
            setResults(r.results);
            if (!r.results.length) setError("No match found. Try a shorter address (area, city).");
          } catch (e) {
            setError(e instanceof ApiError ? e.message : "Lookup failed.");
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? <Loader2 className="size-4 animate-spin" /> : <MapPin className="size-4" />} Find from address
      </button>
      {error && <p className="mt-2 text-xs text-terracotta-700">{error}</p>}
      {results && results.length > 0 && (
        <ul className="mt-2 space-y-1">
          {results.map((r) => (
            <li key={`${r.latitude},${r.longitude}`}>
              <button type="button" onClick={() => { onPick(r.latitude, r.longitude); setResults(null); }}
                className="w-full rounded-lg px-2 py-1 text-left text-xs text-ink-soft hover:bg-sand-50">
                {r.display_name} <span className="text-ink-muted">({r.latitude.toFixed(5)}, {r.longitude.toFixed(5)})</span>
              </button>
            </li>
          ))}
          <li className="px-2 text-[10px] text-ink-muted">Results © OpenStreetMap contributors — check the pin is the right building.</li>
        </ul>
      )}
    </div>
  );
}

const num = (v: string) => (v.trim() === "" ? null : Number(v));

export function PropertyForm({ property, onSaved }: { property?: PropertyAdmin | null; onSaved: (p: PropertyAdmin) => void }) {
  const [form, setForm] = useState<Form>(() => toForm(property));
  const [locations, setLocations] = useState<Location[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    adminApi.locations().then((l) => {
      setLocations(l);
      setForm((f) => (f.location_slug ? f : { ...f, location_slug: l[0]?.slug ?? "" }));
    });
  }, []);

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => ({ ...f, [k]: v }));
  const setDetail = (section: keyof PropertyDetails, field: string, v: string) =>
    setForm((f) => ({ ...f, details: { ...f.details, [section]: { ...f.details[section], [field]: v || null } } }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    const body: Record<string, unknown> = {
      name: form.name.trim(),
      location_slug: form.location_slug,
      address: form.address.trim() || null,
      neighbourhood: form.neighbourhood.trim() || null,
      latitude: num(form.latitude),
      longitude: num(form.longitude),
      discovery_radius_m: Number(form.discovery_radius_m) || 3000,
      bedrooms: num(form.bedrooms),
      bathrooms: num(form.bathrooms),
      max_guests: num(form.max_guests),
      description: form.description.trim() || null,
      amenities: form.amenities.split(",").map((a) => a.trim()).filter(Boolean),
      is_placeholder: form.is_placeholder,
      is_active: form.is_active,
      details: form.details,
    };
    if (form.guest_pin) body.guest_pin = form.guest_pin === "-" ? "" : form.guest_pin;
    try {
      const saved = property ? await adminApi.updateProperty(property.id, body) : await adminApi.createProperty(body);
      setMsg({ ok: true, text: "Saved. The property profile was re-indexed for the assistant." });
      setForm((f) => ({ ...f, guest_pin: "" }));
      onSaved(saved);
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Save failed." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-5">
      <section className={card}>
        <h2 className="mb-4 font-display text-lg font-semibold">Basic information</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label className={label} htmlFor="name">Property name *</label>
            <input id="name" required minLength={2} className={input} value={form.name} onChange={(e) => set("name", e.target.value)} />
          </div>
          <div>
            <label className={label} htmlFor="loc">Location *</label>
            <select id="loc" className={input} value={form.location_slug} onChange={(e) => set("location_slug", e.target.value)}>
              {locations.map((l) => (
                <option key={l.slug} value={l.slug}>{l.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className={label} htmlFor="nb">Neighbourhood</label>
            <input id="nb" className={input} value={form.neighbourhood} onChange={(e) => set("neighbourhood", e.target.value)} placeholder="e.g. Sector 10" />
          </div>
          <div className="sm:col-span-2">
            <label className={label} htmlFor="addr">Complete address</label>
            <textarea id="addr" rows={2} className={input} value={form.address} onChange={(e) => set("address", e.target.value)} />
          </div>
          <div className="grid grid-cols-3 gap-3 sm:col-span-2">
            <div>
              <label className={label} htmlFor="bed">Bedrooms</label>
              <input id="bed" type="number" min={0} className={input} value={form.bedrooms} onChange={(e) => set("bedrooms", e.target.value)} />
            </div>
            <div>
              <label className={label} htmlFor="bath">Bathrooms</label>
              <input id="bath" type="number" min={0} step="0.5" className={input} value={form.bathrooms} onChange={(e) => set("bathrooms", e.target.value)} />
            </div>
            <div>
              <label className={label} htmlFor="guests">Max guests</label>
              <input id="guests" type="number" min={1} className={input} value={form.max_guests} onChange={(e) => set("max_guests", e.target.value)} />
            </div>
          </div>
          <div className="sm:col-span-2">
            <label className={label} htmlFor="desc">Description</label>
            <textarea id="desc" rows={3} className={input} value={form.description} onChange={(e) => set("description", e.target.value)} />
          </div>
          <div className="sm:col-span-2">
            <label className={label} htmlFor="amen">Amenities (comma separated)</label>
            <input id="amen" className={input} value={form.amenities} onChange={(e) => set("amenities", e.target.value)} placeholder="Wi-Fi, Air conditioning, Kitchen" />
          </div>
        </div>
      </section>

      <section className={card}>
        <h2 className="font-display text-lg font-semibold">Location intelligence</h2>
        <p className="mt-1 mb-4 text-sm text-ink-soft">
          Use the property&apos;s exact coordinates. Local search and distances are measured from here. Click
          <em> Find from address</em> (OpenStreetMap), or right-click the building on any map and copy its coordinates.
        </p>
        <GeocodeHelper address={form.address} onPick={(lat, lng) => setForm((f) => ({ ...f, latitude: String(lat), longitude: String(lng) }))} />
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <label className={label} htmlFor="lat">Latitude</label>
            <input id="lat" inputMode="decimal" className={input} value={form.latitude} onChange={(e) => set("latitude", e.target.value)} placeholder="28.5245" />
          </div>
          <div>
            <label className={label} htmlFor="lng">Longitude</label>
            <input id="lng" inputMode="decimal" className={input} value={form.longitude} onChange={(e) => set("longitude", e.target.value)} placeholder="77.2066" />
          </div>
          <div>
            <label className={label} htmlFor="rad">Discovery radius (metres)</label>
            <input id="rad" type="number" min={100} max={50000} step={100} className={input} value={form.discovery_radius_m} onChange={(e) => set("discovery_radius_m", e.target.value)} />
          </div>
        </div>
      </section>

      {SECTIONS.map((sec) => (
        <section key={sec.key} className={card}>
          <h2 className="mb-4 font-display text-lg font-semibold">{sec.title}</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {sec.fields.map(([field, text, wide]) => (
              <div key={field} className={wide ? "sm:col-span-2" : ""}>
                <label className={label} htmlFor={`${sec.key}-${field}`}>{text}</label>
                {wide ? (
                  <textarea id={`${sec.key}-${field}`} rows={2} className={input} value={form.details[sec.key]?.[field] ?? ""} onChange={(e) => setDetail(sec.key, field, e.target.value)} />
                ) : (
                  <input id={`${sec.key}-${field}`} className={input} value={form.details[sec.key]?.[field] ?? ""} onChange={(e) => setDetail(sec.key, field, e.target.value)} />
                )}
              </div>
            ))}
          </div>
        </section>
      ))}

      <section className={card}>
        <h2 className="mb-4 font-display text-lg font-semibold">Access & visibility</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className={label} htmlFor="pin">Guest PIN {property?.requires_pin ? "(set — enter a new one to change, “-” to remove)" : "(optional)"}</label>
            <input id="pin" className={input} value={form.guest_pin} onChange={(e) => set("guest_pin", e.target.value.trim())} placeholder="4–12 letters/digits" maxLength={12} />
            <p className="mt-1 text-xs text-ink-muted">When set, Wi-Fi passwords and access codes are only shared after the guest enters this PIN.</p>
          </div>
          <div className="space-y-3 pt-5">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.is_active} onChange={(e) => set("is_active", e.target.checked)} className="size-4 accent-terracotta-600" /> Active (visible to guests)
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.is_placeholder} onChange={(e) => set("is_placeholder", e.target.checked)} className="size-4 accent-terracotta-600" /> Contains placeholder data
            </label>
          </div>
        </div>
      </section>

      <div className="sticky bottom-0 -mx-4 flex items-center gap-3 border-t border-sand-200 bg-white/95 px-4 py-3 backdrop-blur">
        <button type="submit" disabled={busy} className={btnPrimary}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />} {property ? "Save changes" : "Create property"}
        </button>
        {msg && <p role="status" className={`text-sm ${msg.ok ? "text-leaf-600" : "text-terracotta-700"}`}>{msg.text}</p>}
      </div>
    </form>
  );
}
