"""Trainer discovery and the pre-filled contact message.

Two modes:
  * offline (in person): trainers of the right gender within SEARCH_RADIUS_KM
    of the typed location (unchanged behaviour: geocode, bounding box, Haversine).
  * online: location is irrelevant. Every active trainer of the right gender
    is listed, with pagination, and no coordinates are needed.
"""
import logging
import re
from math import atan2, cos, radians, sin, sqrt

import requests

from models import Trainer, db

log = logging.getLogger(__name__)

SEARCH_RADIUS_KM = 5
ONLINE_PAGE_SIZE = 12
MODES = ("online", "offline")
_GEOCODER_URL = "https://nominatim.openstreetmap.org/search"
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class GeocodingError(Exception):
    """The location service could not be reached or gave an unusable answer."""


def get_coordinates(location):
    """(latitude, longitude) for a place in Bengaluru, None if not found.

    Raises GeocodingError if the geocoding service fails, so callers can tell
    "no such place" apart from "service down".
    """
    params = {"q": f"{location}, Bengaluru, Karnataka, India", "format": "json", "limit": 1}
    try:
        response = requests.get(_GEOCODER_URL, params=params,
                                headers={"User-Agent": "Train2Conquer/1.0"}, timeout=10)
        response.raise_for_status()
        data = response.json()
        if not data:
            return None
        return float(data[0]["lat"]), float(data[0]["lon"])
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        log.warning("Geocoding failed: %s", type(exc).__name__)
        raise GeocodingError from exc


def calculate_distance(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (Haversine formula)."""
    earth_radius_km = 6371
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return earth_radius_km * 2 * atan2(sqrt(a), sqrt(1 - a))


def find_nearby_trainers(location, gender, radius_km=SEARCH_RADIUS_KM):
    """Active trainers of `gender` within `radius_km` of `location`, nearest first.

    A cheap bounding-box query in the database narrows the candidates; the exact
    circular radius is then checked with Haversine. Each returned trainer gets a
    `distance_km` attribute for display. Returns [] if the place is not found.
    """
    coordinates = get_coordinates(location)
    if not coordinates:
        return []
    user_lat, user_lon = coordinates
    lat_delta = radius_km / 111.0                      # 1 degree of latitude ~ 111 km
    lon_delta = radius_km / (111.0 * max(cos(radians(user_lat)), 0.01))
    candidates = db.session.execute(db.select(Trainer).where(
        Trainer.is_active.is_(True),
        Trainer.gender == gender,
        Trainer.latitude.between(user_lat - lat_delta, user_lat + lat_delta),
        Trainer.longitude.between(user_lon - lon_delta, user_lon + lon_delta),
    )).scalars().all()
    nearby = []
    for trainer in candidates:
        distance = calculate_distance(user_lat, user_lon, trainer.latitude, trainer.longitude)
        if distance <= radius_km:
            trainer.distance_km = round(distance, 1)
            nearby.append(trainer)
    return sorted(nearby, key=lambda t: t.distance_km)


def find_online_trainers(gender, page=1, per_page=ONLINE_PAGE_SIZE):
    """Every active trainer of `gender`, wherever they are, as a pagination object."""
    query = (db.select(Trainer).where(Trainer.is_active.is_(True), Trainer.gender == gender)
             .order_by(Trainer.name, Trainer.id))
    return db.paginate(query, page=max(page, 1), per_page=per_page, error_out=False)


def get_contactable_trainer(trainer_id):
    """The active trainer with this id, or None. Always loaded from the database."""
    if not trainer_id:
        return None
    trainer = db.session.get(Trainer, trainer_id)
    return trainer if trainer and trainer.is_active else None


def _single_line(text):
    return " ".join(_CONTROL_CHARS.sub(" ", text or "").split())


def build_contact_message(trainer_name, trainer_location=None, online=False):
    """Friendly first message to a trainer (the user can edit it before sending).

    Online: the location sentence is left out completely.
    """
    name = _single_line(trainer_name) or "there"
    place = _single_line(trainer_location)
    lines = [f"Hello {name},", "",
             "I came across your trainer profile on Train2Conquer and I'm interested in working with you."]
    if place and not online:
        lines.append(f"I found your profile through {place} and would like to know more about your training services.")
    else:
        lines.append("I would like to know more about your training services.")
    lines += ["", "Please let me know how we can get started.", "", "Thank you!"]
    return "\n".join(lines)
