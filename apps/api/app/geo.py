"""Geography helpers for line A (map / nearby).

Haversine straight-line distance (km) between two WGS-84 points, and
Google Maps links (Hong Kong default: Google Maps works in HK; no
mainland map provider is wired in). Straight-line distance is NOT
walking / driving distance or time — that needs a routing service.
"""
import math

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def google_maps_search_url(lat: float, lng: float, label: str = "") -> str:
    query = f"{lat},{lng}"
    if label:
        query += f" ({label})"
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def google_maps_directions_url(lat: float, lng: float) -> str:
    return f"https://www.google.com/maps/dir/?api=1&destination={lat},{lng}"
