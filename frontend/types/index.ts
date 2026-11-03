export type Intent =
  | "PROPERTY_QUERY"
  | "LOCAL_DISCOVERY_QUERY"
  | "GENERAL_NEIGHBOURHOOD_QUERY"
  | "MIXED_QUERY"
  | "GENERAL_CONVERSATION"
  | "EMERGENCY_QUERY";

export interface Location {
  id: string;
  slug: string;
  name: string;
  city: string;
  tagline?: string | null;
  description?: string | null;
  property_count: number;
}

export interface PropertySummary {
  id: string;
  slug: string;
  name: string;
  location_slug: string;
  location_name: string;
  neighbourhood?: string | null;
  address?: string | null;
  bedrooms?: number | null;
  bathrooms?: number | null;
  max_guests?: number | null;
  description?: string | null;
  amenities: string[];
  has_coordinates: boolean;
  requires_pin: boolean;
  is_placeholder: boolean;
}

export interface Place {
  place_id?: string | null;
  name: string;
  category?: string | null;
  types: string[];
  address?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  distance_km?: number | null;
  rating?: number | null;
  user_rating_count?: number | null;
  price_level?: number | null;
  open_now?: boolean | null;
  opening_hours: string[];
  phone?: string | null;
  website?: string | null;
  summary?: string | null;
  maps_url?: string | null;
  directions_url?: string | null;
  source: "google_places" | "host_curated" | "mock";
  host_note?: string | null;
  score?: number | null;
}

export interface Session {
  session_id: string;
  session_token?: string | null;
  location_slug?: string | null;
  location_name?: string | null;
  property?: PropertySummary | null;
  available_properties: PropertySummary[];
  preferences: Record<string, unknown>;
  pin_verified: boolean;
  created_at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  intent?: Intent | null;
  places: Place[];
  placesPrevious?: boolean;
  suggestions: string[];
  pending?: boolean;
  error?: boolean;
  created_at?: string;
}

export interface ServiceStatus {
  llm: "claude" | "offline";
  llm_model?: string | null;
  embeddings: string;
  places: "google_places" | "mock" | "disabled";
  quick_actions: Record<string, string>;
}

// ---------------------------------------------------------------- admin
export interface PropertyDetails {
  check_in_out: Record<string, string | null>;
  facilities: Record<string, string | null>;
  house_rules: Record<string, string | null>;
  emergency: Record<string, string | null>;
  neighbourhood: Record<string, string | null>;
}

export interface PropertyAdmin extends PropertySummary {
  latitude?: number | null;
  longitude?: number | null;
  discovery_radius_m: number;
  details: PropertyDetails;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  document_count: number;
  chunk_count: number;
}

export interface DocumentRow {
  id: string;
  property_id: string;
  property_name?: string | null;
  filename: string;
  document_type: string;
  size_bytes: number;
  status: "pending" | "processing" | "ready" | "failed";
  error_message?: string | null;
  chunk_count: number;
  is_system: boolean;
  uploaded_at: string;
  last_indexed_at?: string | null;
}

export interface Curated {
  id: string;
  property_id: string;
  name: string;
  category: string;
  note?: string | null;
  address?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  google_place_id?: string | null;
  tags: string[];
  is_active: boolean;
  created_at: string;
}

export interface Dashboard {
  properties: number;
  inactive_properties: number;
  placeholder_properties: number;
  missing_coordinates: number;
  documents: number;
  documents_by_status: Record<string, number>;
  chunks: number;
  curated_recommendations: number;
  conversations_7d: number;
  messages_7d: number;
  intents_7d: Record<string, number>;
  integrations: { llm: string; embeddings: string; places: string };
}
