"""Géocodage et calcul d'itinéraire, côté serveur uniquement.

L'agent fournit du texte (un nom de lieu), jamais une URL ni un hôte : les fournisseurs viennent
exclusivement de l'environnement, ce qui ferme la porte au SSRF. Les clés ne quittent pas le backend ;
le navigateur ne reçoit que des coordonnées déjà résolues.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

from app.services.http_fetch import HttpFetch

GEOCODING_HOST = "https://api.maptiler.com"
UPSTREAM_TIMEOUT_S = 6.0
MAX_POLYLINE_POINTS = 500

TravelMode = Literal["driving", "walking", "cycling"]
Place = dict[str, Any]
Coordinate = tuple[float, float]

OSRM_PROFILE: dict[str, str] = {"driving": "driving", "walking": "foot", "cycling": "bike"}
EARTH_RADIUS_KM = 6_371
AVERAGE_SPEED_KMH: dict[str, float] = {"driving": 22, "walking": 4.6, "cycling": 14}


@dataclass(frozen=True)
class MapProviders:
    maptiler_key: str | None
    routing_url: str | None
    # Géocodeur sans clé, utilisé quand MapTiler n'est pas configuré. `None` le désactive.
    nominatim_url: str | None
    contact: str


@dataclass
class Route:
    origin: Place
    destination: Place
    mode: str
    distance_km: float
    duration_min: int
    coordinates: list[list[float]]


def js_round(value: float) -> int:
    """`Math.round` : l'arrondi se fait vers +∞ sur les demi-valeurs (et non à l'entier pair)."""
    return math.floor(value + 0.5)


def round_to(value: float, digits: int) -> float:
    factor = 10**digits
    return js_round(value * factor) / factor


def travel_mode(value: Any) -> TravelMode:
    return value if value in ("walking", "cycling") else "driving"


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


async def fetch_json(fetch: HttpFetch, url: str, user_agent: str | None = None) -> Any:
    headers = {"Accept": "application/json"}
    if user_agent is not None:
        headers["User-Agent"] = user_agent
    response = await fetch(url, method="GET", headers=headers, timeout=UPSTREAM_TIMEOUT_S)
    if not response.is_success:
        raise RuntimeError(f"upstream_{response.status_code}")
    return json.loads(response.text)


async def geocode(fetch: HttpFetch, query: str, providers: MapProviders) -> Place | None:
    """Nom de lieu → coordonnées.

    MapTiler quand une clé est configurée, sinon un géocodeur au protocole Nominatim. Le texte est
    encodé ; l'hôte vient toujours de l'environnement.
    """
    term = query[:200]
    if providers.maptiler_key is not None:
        path = f"{GEOCODING_HOST}/geocoding/{_encode_uri_component(term)}.json"
        key = _encode_uri_component(providers.maptiler_key)
        return _first_place(await fetch_json(fetch, f"{path}?limit=1&key={key}"), query)
    if providers.nominatim_url is None:
        return None
    url = f"{providers.nominatim_url}/search?format=jsonv2&limit=1&q={_encode_uri_component(term)}"
    return nominatim_place(await fetch_json(fetch, url, providers.contact), query)


def _encode_uri_component(value: str) -> str:
    return quote(value, safe="!~*'()")


def nominatim_place(payload: Any, fallback_name: str) -> Place | None:
    """Le protocole Nominatim renvoie des coordonnées en chaînes : elles sont converties et bornées."""
    if not isinstance(payload, list) or len(payload) == 0:
        return None
    entry = payload[0]
    if not isinstance(entry, dict):
        return None
    lat = _to_number(entry.get("lat", _ABSENT))
    lon = _to_number(entry.get("lon", _ABSENT))
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    if abs(lat) > 90 or abs(lon) > 180:
        return None
    display = entry.get("display_name")
    name = display if isinstance(display, str) else fallback_name
    return {"name": name[:200], "lon": round_to(lon, 6), "lat": round_to(lat, 6)}


_ABSENT = object()


def _to_number(value: Any) -> float:
    """`Number(value)` de JavaScript pour les formes qu'un géocodeur peut renvoyer."""
    if value is _ABSENT:
        return math.nan
    if value is None:
        return 0.0
    if isinstance(value, bool | int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return 0.0
        try:
            return float(text)
        except ValueError:
            return math.nan
    return math.nan


def _first_place(payload: Any, fallback_name: str) -> Place | None:
    if not isinstance(payload, dict):
        return None
    features = payload.get("features")
    if not isinstance(features, list) or len(features) == 0:
        return None
    feature = features[0]
    if not isinstance(feature, dict):
        return None
    center = feature.get("center")
    if not isinstance(center, list) or len(center) < 2 or not _is_number(center[0]) or not _is_number(center[1]):
        return None
    place_name = feature.get("place_name")
    name = place_name if isinstance(place_name, str) else fallback_name
    return {"name": name[:200], "lon": center[0], "lat": center[1]}


async def route(
    fetch: HttpFetch, origin: Place, destination: Place, mode: TravelMode, providers: MapProviders
) -> Route | None:
    """Deux points → tracé, via un service au protocole OSRM (auto-hébergé en production)."""
    if providers.routing_url is None:
        return None
    start = f"{_js_number(origin['lon'])},{_js_number(origin['lat'])}"
    end = f"{_js_number(destination['lon'])},{_js_number(destination['lat'])}"
    pair = f"{start};{end}"
    url = f"{providers.routing_url}/route/v1/{OSRM_PROFILE[mode]}/{pair}?overview=full&geometries=geojson"
    leg = _first_leg(await fetch_json(fetch, url))
    if leg is None:
        return None
    distance_km, duration_min, coordinates = leg
    return Route(origin, destination, mode, distance_km, duration_min, coordinates)


def _js_number(value: float) -> str:
    """Rendu d'un nombre comme `String(number)` en JavaScript (2 plutôt que 2.0)."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _first_leg(payload: Any) -> tuple[float, int, list[list[float]]] | None:
    if not isinstance(payload, dict):
        return None
    routes = payload.get("routes")
    if not isinstance(routes, list) or len(routes) == 0:
        return None
    first = routes[0]
    if not isinstance(first, dict):
        return None
    geometry = first.get("geometry")
    if not _is_number(first.get("distance")) or not _is_number(first.get("duration")):
        return None
    raw = geometry.get("coordinates") if isinstance(geometry, dict) else None
    return (
        round_to(first["distance"] / 1000, 2),
        js_round(first["duration"] / 60),
        simplify(_coordinates_of(raw)),
    )


def _coordinates_of(value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        return []
    points: list[list[float]] = []
    for entry in value:
        if isinstance(entry, list) and len(entry) >= 2 and _is_number(entry[0]) and _is_number(entry[1]):
            points.append([round_to(entry[0], 6), round_to(entry[1], 6)])
    return points


def simplify(points: list[Any]) -> list[Any]:
    """Échantillonnage régulier : un trajet long ne peut pas dépasser la borne du schéma."""
    if len(points) <= MAX_POLYLINE_POINTS:
        return points
    step = math.ceil(len(points) / (MAX_POLYLINE_POINTS - 1))
    kept = [point for index, point in enumerate(points) if index % step == 0]
    last = points[-1]
    if kept[-1] is not last:
        kept.append(last)
    return kept


def haversine_km(origin: Place, destination: Place) -> float:
    def to_rad(degrees: float) -> float:
        return degrees * math.pi / 180

    delta_lat = to_rad(destination["lat"] - origin["lat"])
    delta_lon = to_rad(destination["lon"] - origin["lon"])
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(to_rad(origin["lat"])) * math.cos(to_rad(destination["lat"])) * math.sin(delta_lon / 2) ** 2
    )
    return round_to(2 * EARTH_RADIUS_KM * math.asin(min(1, math.sqrt(a))), 2)


def straight_line(origin: Place, destination: Place, mode: TravelMode) -> Route:
    """Repli hors ligne : segment direct et durée estimée.

    Le composant l'affiche comme une estimation, jamais comme un itinéraire calculé.
    """
    distance_km = haversine_km(origin, destination)
    return Route(
        origin=origin,
        destination=destination,
        mode=mode,
        distance_km=distance_km,
        duration_min=max(1, js_round(distance_km / AVERAGE_SPEED_KMH[mode] * 60)),
        coordinates=[[origin["lon"], origin["lat"]], [destination["lon"], destination["lat"]]],
    )
