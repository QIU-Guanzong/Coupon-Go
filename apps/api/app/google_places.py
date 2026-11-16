"""Google Places client (thin wrapper over the Places Web API).

The original app calls:
  place/autocomplete/json, place/details/json, place/nearbysearch/json
under https://maps.googleapis.com/maps/api (REVERSE.md §2.1).
"""
import httpx

from . import config

BASE = "https://maps.googleapis.com/maps/api"


class GoogleNotConfigured(Exception):
    pass


def _key() -> str:
    if not config.GOOGLE_API_KEY:
        raise GoogleNotConfigured("GOOGLE_API_KEY 未設定")
    return config.GOOGLE_API_KEY


def autocomplete(input_text: str, language: str = "zh-HK"):
    return _get("place/autocomplete/json", {"input": input_text, "language": language})


def place_details(place_id: str, language: str = "zh-HK"):
    return _get(
        "place/details/json",
        {"place_id": place_id, "language": language,
         "fields": "place_id,name,formatted_address,geometry,types"},
    )


def nearby_search(lat: float, lng: float, radius: int = 1000, keyword: str = ""):
    params = {"location": f"{lat},{lng}", "radius": radius, "language": "zh-HK"}
    if keyword:
        params["keyword"] = keyword
    return _get("place/nearbysearch/json", params)


def _get(path: str, params: dict):
    params = {**params, "key": _key()}
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{BASE}/{path}", params=params)
    resp.raise_for_status()
    return resp.json()
