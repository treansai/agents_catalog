/**
 * Géocodage et calcul d'itinéraire, côté serveur uniquement.
 *
 * L'agent fournit du texte — un nom de lieu — jamais une URL ni un hôte : les fournisseurs
 * viennent exclusivement de l'environnement, ce qui ferme la porte au SSRF. Les clés ne quittent
 * pas le backend ; le navigateur ne reçoit que des coordonnées déjà résolues.
 */

const GEOCODING_HOST = "https://api.maptiler.com";
const UPSTREAM_TIMEOUT_MS = 6_000;
const MAX_POLYLINE_POINTS = 500;

export type TravelMode = "driving" | "walking" | "cycling";

export interface Place {
  name: string;
  lon: number;
  lat: number;
}

export interface Route {
  origin: Place;
  destination: Place;
  mode: TravelMode;
  distanceKm: number;
  durationMin: number;
  coordinates: [number, number][];
}

export interface MapProviders {
  maptilerKey: string | undefined;
  routingUrl: string | undefined;
  /** Géocodeur sans clé, utilisé quand MapTiler n'est pas configuré. `undefined` le désactive. */
  nominatimUrl: string | undefined;
  contact: string;
}

const OSRM_PROFILE: Record<TravelMode, string> = {
  driving: "driving",
  walking: "foot",
  cycling: "bike"
};

export function travelMode(value: unknown): TravelMode {
  return value === "walking" || value === "cycling" ? value : "driving";
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

async function fetchJson(url: string, userAgent?: string): Promise<unknown> {
  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
      ...(userAgent === undefined ? {} : { "User-Agent": userAgent })
    },
    signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS)
  });
  if (!response.ok) throw new Error(`upstream_${response.status}`);
  return response.json();
}

/**
 * Nom de lieu → coordonnées. MapTiler quand une clé est configurée, sinon un géocodeur au
 * protocole Nominatim. Le texte est encodé ; l'hôte vient toujours de l'environnement.
 */
export async function geocode(query: string, providers: MapProviders): Promise<Place | undefined> {
  const term = query.slice(0, 200);
  if (providers.maptilerKey !== undefined) {
    const path = `${GEOCODING_HOST}/geocoding/${encodeURIComponent(term)}.json`;
    const key = encodeURIComponent(providers.maptilerKey);
    return firstPlace(await fetchJson(`${path}?limit=1&key=${key}`), query);
  }
  if (providers.nominatimUrl === undefined) return undefined;
  const url = `${providers.nominatimUrl}/search?format=jsonv2&limit=1&q=${encodeURIComponent(term)}`;
  return nominatimPlace(await fetchJson(url, providers.contact), query);
}

/** Le protocole Nominatim renvoie des coordonnées en chaînes : elles sont converties et bornées. */
export function nominatimPlace(payload: unknown, fallbackName: string): Place | undefined {
  if (!Array.isArray(payload) || payload.length === 0) return undefined;
  const entry = payload[0] as { lat?: unknown; lon?: unknown; display_name?: unknown };
  const lat = Number(entry.lat);
  const lon = Number(entry.lon);
  if (!isNumber(lat) || !isNumber(lon)) return undefined;
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return undefined;
  const name = typeof entry.display_name === "string" ? entry.display_name : fallbackName;
  return { name: name.slice(0, 200), lon: round(lon, 6), lat: round(lat, 6) };
}

function firstPlace(payload: unknown, fallbackName: string): Place | undefined {
  if (typeof payload !== "object" || payload === null) return undefined;
  const features = (payload as { features?: unknown }).features;
  if (!Array.isArray(features) || features.length === 0) return undefined;
  const feature = features[0] as { center?: unknown; place_name?: unknown; text?: unknown };
  const center = feature.center;
  if (!Array.isArray(center) || !isNumber(center[0]) || !isNumber(center[1])) return undefined;
  const name = typeof feature.place_name === "string" ? feature.place_name : fallbackName;
  return { name: name.slice(0, 200), lon: center[0], lat: center[1] };
}

/** Deux points → tracé, via un service au protocole OSRM (auto-hébergé en production). */
export async function route(
  origin: Place,
  destination: Place,
  mode: TravelMode,
  providers: MapProviders
): Promise<Route | undefined> {
  if (providers.routingUrl === undefined) return undefined;
  const pair = `${origin.lon},${origin.lat};${destination.lon},${destination.lat}`;
  const url = `${providers.routingUrl}/route/v1/${OSRM_PROFILE[mode]}/${pair}?overview=full&geometries=geojson`;
  const leg = firstLeg(await fetchJson(url));
  if (leg === undefined) return undefined;
  return { origin, destination, mode, ...leg };
}

function firstLeg(
  payload: unknown
): Pick<Route, "distanceKm" | "durationMin" | "coordinates"> | undefined {
  if (typeof payload !== "object" || payload === null) return undefined;
  const routes = (payload as { routes?: unknown }).routes;
  if (!Array.isArray(routes) || routes.length === 0) return undefined;
  const first = routes[0] as { distance?: unknown; duration?: unknown; geometry?: unknown };
  const geometry = first.geometry as { coordinates?: unknown } | undefined;
  if (!isNumber(first.distance) || !isNumber(first.duration)) return undefined;
  return {
    distanceKm: round(first.distance / 1000, 2),
    durationMin: Math.round(first.duration / 60),
    coordinates: simplify(coordinatesOf(geometry?.coordinates))
  };
}

function coordinatesOf(value: unknown): [number, number][] {
  if (!Array.isArray(value)) return [];
  const points: [number, number][] = [];
  for (const entry of value) {
    if (Array.isArray(entry) && isNumber(entry[0]) && isNumber(entry[1])) {
      points.push([round(entry[0], 6), round(entry[1], 6)]);
    }
  }
  return points;
}

/** Échantillonnage régulier : un trajet long ne peut pas dépasser la borne du schéma. */
export function simplify(points: [number, number][]): [number, number][] {
  if (points.length <= MAX_POLYLINE_POINTS) return points;
  const step = Math.ceil(points.length / (MAX_POLYLINE_POINTS - 1));
  const kept = points.filter((_, index) => index % step === 0);
  const last = points.at(-1);
  if (last !== undefined && kept.at(-1) !== last) kept.push(last);
  return kept;
}

export function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

const EARTH_RADIUS_KM = 6_371;
const AVERAGE_SPEED_KMH: Record<TravelMode, number> = { driving: 22, walking: 4.6, cycling: 14 };

export function haversineKm(from: Place, to: Place): number {
  const toRad = (degrees: number) => (degrees * Math.PI) / 180;
  const deltaLat = toRad(to.lat - from.lat);
  const deltaLon = toRad(to.lon - from.lon);
  const a =
    Math.sin(deltaLat / 2) ** 2 +
    Math.cos(toRad(from.lat)) * Math.cos(toRad(to.lat)) * Math.sin(deltaLon / 2) ** 2;
  return round(2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(a))), 2);
}

/**
 * Repli hors ligne : segment direct et durée estimée. Le composant l'affiche comme une estimation,
 * jamais comme un itinéraire calculé.
 */
export function straightLine(origin: Place, destination: Place, mode: TravelMode): Route {
  const distanceKm = haversineKm(origin, destination);
  return {
    origin,
    destination,
    mode,
    distanceKm,
    durationMin: Math.max(1, Math.round((distanceKm / AVERAGE_SPEED_KMH[mode]) * 60)),
    coordinates: [
      [origin.lon, origin.lat],
      [destination.lon, destination.lat]
    ]
  };
}
