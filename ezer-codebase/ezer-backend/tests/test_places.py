from __future__ import annotations

import httpx
import pytest

from app.agent_ui.places import (
    MapProviders,
    geocode,
    haversine_km,
    nominatim_place,
    route,
    simplify,
    straight_line,
)
from tests.conftest import json_response


def test_reads_a_nominatim_response_and_bounds_the_coordinates():
    place = nominatim_place(
        [{"lat": "48.8582599", "lon": "2.2945006", "display_name": "Tour Eiffel, Paris"}], "Tour Eiffel"
    )
    assert place == {"name": "Tour Eiffel, Paris", "lon": 2.294501, "lat": 48.85826}


def test_refuses_an_empty_or_off_globe_response_instead_of_inventing_a_point():
    assert nominatim_place([], "nulle part") is None
    assert nominatim_place([{"lat": "999", "lon": "0"}], "nulle part") is None
    assert nominatim_place({"error": "unauthorized"}, "nulle part") is None
    assert nominatim_place([{"lat": "abc", "lon": "0"}], "nulle part") is None
    assert nominatim_place([{"lon": "0"}], "nulle part") is None


def test_keeps_the_requested_name_when_the_provider_returns_none():
    place = nominatim_place([{"lat": "48.85", "lon": "2.35"}], "Le Rival")
    assert place is not None
    assert place["name"] == "Le Rival"


def test_brings_a_long_polyline_under_the_schema_bound_endpoints_included():
    points = [[2 + index / 10_000, 48 + index / 10_000] for index in range(2_500)]
    reduced = simplify(points)
    assert len(reduced) <= 500
    assert reduced[0] == points[0]
    assert reduced[-1] == points[-1]


def test_estimates_a_consistent_distance_and_duration_without_a_routing_service():
    gare_de_lyon = {"name": "Gare de Lyon", "lon": 2.3736, "lat": 48.8443}
    rival = {"name": "Le Rival", "lon": 2.3532, "lat": 48.8606}
    assert haversine_km(gare_de_lyon, rival) == pytest.approx(2.35, abs=0.05)

    walk = straight_line(gare_de_lyon, rival, "walking")
    drive = straight_line(gare_de_lyon, rival, "driving")
    assert walk.duration_min > drive.duration_min
    assert len(walk.coordinates) == 2


async def test_geocoding_uses_the_environment_host_and_encodes_the_user_text():
    calls: list[str] = []

    async def fetch(url, **kwargs):
        calls.append(url)
        assert kwargs["headers"]["User-Agent"] == "ezer-contact"
        return json_response([{"lat": "48.85", "lon": "2.35", "display_name": "Quelque part"}])

    providers = MapProviders(None, None, "https://geo.interne.example", "ezer-contact")
    place = await geocode(fetch, "http://169.254.169.254/ & co", providers)

    assert place is not None
    assert calls == [
        "https://geo.interne.example/search?format=jsonv2&limit=1&q=http%3A%2F%2F169.254.169.254%2F%20%26%20co"
    ]


async def test_geocoding_is_disabled_without_any_provider_and_never_calls_out():
    async def fetch(url, **kwargs):  # pragma: no cover - ne doit jamais être appelé
        raise AssertionError("no outbound call expected")

    assert await geocode(fetch, "Paris", MapProviders(None, None, None, "x")) is None


async def test_geocoding_prefers_maptiler_and_fails_on_an_upstream_error():
    async def failing(url, **kwargs):
        assert url.startswith("https://api.maptiler.com/geocoding/Paris.json?limit=1&key=")
        return httpx.Response(503)

    with pytest.raises(RuntimeError, match="upstream_503"):
        await geocode(failing, "Paris", MapProviders("abcdefgh1234", None, "https://unused.example", "x"))


async def test_routing_reads_an_osrm_response():
    async def fetch(url, **kwargs):
        assert (
            url
            == "https://routing.interne.example/route/v1/foot/2.35,48.85;2.36,48.86?overview=full&geometries=geojson"
        )
        return json_response(
            {
                "routes": [
                    {"distance": 1234.5, "duration": 929, "geometry": {"coordinates": [[2.35, 48.85], [2.36, 48.86]]}}
                ]
            }
        )

    origin = {"name": "A", "lon": 2.35, "lat": 48.85}
    destination = {"name": "B", "lon": 2.36, "lat": 48.86}
    providers = MapProviders(None, "https://routing.interne.example", None, "x")
    computed = await route(fetch, origin, destination, "walking", providers)

    assert computed is not None
    assert computed.distance_km == 1.23
    assert computed.duration_min == 15
    assert computed.coordinates == [[2.35, 48.85], [2.36, 48.86]]
    assert await route(fetch, origin, destination, "walking", MapProviders(None, None, None, "x")) is None
