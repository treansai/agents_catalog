"use client";

import { useEffect, useRef, useState } from "react";

import { EzCard } from "./primitives";
import { asString, type AgentComponentProps } from "./agent-props";

/**
 * Carte d'itinéraire.
 *
 * Le composant ne connaît ni fournisseur ni clé : le style vient d'une route serveur, et le tracé
 * d'un résolveur autorisé. Sans fond de carte configuré, le trajet reste lisible sur un fond uni
 * plutôt que de laisser un cadre vide.
 */

const ACCENT = "#880d1e";
const SURFACE = "#050505";

export interface RoutePlace {
  name: string;
  lon: number;
  lat: number;
}

export interface RouteData {
  origin: RoutePlace;
  destination: RoutePlace;
  mode: "driving" | "walking" | "cycling";
  distanceKm: number;
  durationMin: number;
  estimated?: boolean;
  coordinates: [number, number][];
}

const MODE_LABELS: Record<RouteData["mode"], string> = {
  driving: "en voiture",
  walking: "à pied",
  cycling: "à vélo",
};

const BLANK_STYLE = {
  version: 8 as const,
  sources: {},
  layers: [{ id: "fond", type: "background" as const, paint: { "background-color": SURFACE } }],
};

function parseRoute(value: unknown): RouteData | null {
  if (typeof value !== "object" || value === null) return null;
  const candidate = value as Partial<RouteData>;
  const { origin, destination, coordinates } = candidate;
  if (origin === undefined || destination === undefined || !Array.isArray(coordinates)) return null;
  if (coordinates.length < 2) return null;
  return candidate as RouteData;
}

function duration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
}

async function loadStyleUrl(): Promise<string | null> {
  try {
    const response = await fetch("/api/agent-ui/map-config", { cache: "no-store" });
    if (!response.ok) return null;
    const payload = (await response.json()) as { styleUrl?: unknown };
    return typeof payload.styleUrl === "string" ? payload.styleUrl : null;
  } catch {
    return null;
  }
}

function routeLayers(route: RouteData) {
  return {
    trace: {
      type: "Feature" as const,
      properties: {},
      geometry: { type: "LineString" as const, coordinates: route.coordinates },
    },
    ends: {
      type: "FeatureCollection" as const,
      features: [route.origin, route.destination].map((place, index) => ({
        type: "Feature" as const,
        properties: { arrivee: index === 1 },
        geometry: { type: "Point" as const, coordinates: [place.lon, place.lat] },
      })),
    },
  };
}

function boundsOf(route: RouteData): [[number, number], [number, number]] {
  const longitudes = route.coordinates.map(([lon]) => lon);
  const latitudes = route.coordinates.map(([, lat]) => lat);
  return [
    [Math.min(...longitudes), Math.min(...latitudes)],
    [Math.max(...longitudes), Math.max(...latitudes)],
  ];
}

export function RouteMapAgent({ props, data, dataStatus }: AgentComponentProps): React.JSX.Element {
  const route = parseRoute(data);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (route === null || containerRef.current === null) return;
    const container = containerRef.current;
    let map: { remove: () => void } | null = null;
    let cancelled = false;

    void (async () => {
      try {
        const [{ Map: MapLibreMap }, styleUrl] = await Promise.all([
          import("maplibre-gl"),
          loadStyleUrl(),
        ]);
        if (cancelled) return;
        const instance = new MapLibreMap({
          container,
          style: styleUrl ?? BLANK_STYLE,
          bounds: boundsOf(route),
          fitBoundsOptions: { padding: 34, maxZoom: 15 },
          attributionControl: { compact: true },
        });
        map = instance;
        instance.on("load", () => drawRoute(instance, route));
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      map?.remove();
    };
  }, [route]);

  if (route === null) {
    return <p className="ezc-fallback">{dataStatus === "loading" ? "Calcul du trajet…" : "Trajet indisponible."}</p>;
  }

  return (
    <EzCard style={{ display: "grid", gap: 10, padding: 14 }}>
      <RouteHeading note={asString(props.note)} route={route} title={asString(props.title)} />
      <div
        aria-label={`Trajet vers ${route.destination.name}`}
        ref={containerRef}
        role="application"
        style={{ height: 220, borderRadius: 12, overflow: "hidden", background: SURFACE }}
      />
      {failed ? <p style={{ margin: 0, fontSize: 11, color: "#5c4d4f" }}>Carte indisponible ; le trajet reste décrit ci-dessus.</p> : null}
    </EzCard>
  );
}

function RouteHeading({
  note,
  route,
  title,
}: {
  note: string;
  route: RouteData;
  title: string;
}): React.JSX.Element {
  return (
    <div style={{ display: "grid", gap: 3 }}>
      <span style={{ fontSize: 14, fontWeight: 600, color: "#f2eded" }}>
        {title === "" ? route.destination.name : title}
      </span>
      <span className="ezc-mono" style={{ fontSize: 10, letterSpacing: "0.08em", color: "#8a7679" }}>
        {`${route.distanceKm} km · ${duration(route.durationMin)} ${MODE_LABELS[route.mode]}`}
        {route.estimated === true ? " · estimation à vol d’oiseau" : ""}
      </span>
      <span style={{ fontSize: 11, color: "#5c4d4f" }}>
        {note === "" ? `Départ : ${route.origin.name}` : note}
      </span>
    </div>
  );
}

/** Deux couches seulement : le tracé, puis les deux extrémités. */
function drawRoute(map: import("maplibre-gl").Map, route: RouteData): void {
  const { trace, ends } = routeLayers(route);
  map.addSource("trajet", { type: "geojson", data: trace });
  map.addSource("etapes", { type: "geojson", data: ends });
  map.addLayer({
    id: "trajet-ligne",
    type: "line",
    source: "trajet",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ACCENT, "line-width": 4, "line-opacity": 0.95 },
  });
  map.addLayer({
    id: "trajet-etapes",
    type: "circle",
    source: "etapes",
    paint: {
      "circle-radius": ["case", ["get", "arrivee"], 7, 5],
      "circle-color": ["case", ["get", "arrivee"], ACCENT, "#e8dedf"],
      "circle-stroke-color": SURFACE,
      "circle-stroke-width": 2,
    },
  });
}

export default RouteMapAgent;
