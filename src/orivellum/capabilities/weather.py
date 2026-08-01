"""Weather tool — returns current conditions using Open-Meteo (no API key required).

Flow:
  1. Geocode the location via Open-Meteo's free geocoding API
  2. Fetch current weather from Open-Meteo's forecast API
  3. Format a human-readable markdown weather card

Falls back gracefully to a helpful error message.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger("orivellum.weather")

_GEO_API     = "https://geocoding-api.open-meteo.com/v1/search"
_WEATHER_API = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT     = 10

_WMO_CODES: dict[int, str] = {
    0: "☀️ Clear sky", 1: "🌤️ Mainly clear", 2: "⛅ Partly cloudy", 3: "☁️ Overcast",
    45: "🌫️ Fog", 48: "🌫️ Icy fog", 51: "🌦️ Light drizzle", 53: "🌦️ Drizzle",
    55: "🌧️ Heavy drizzle", 61: "🌧️ Slight rain", 63: "🌧️ Moderate rain",
    65: "🌧️ Heavy rain", 71: "🌨️ Slight snow", 73: "🌨️ Moderate snow",
    75: "❄️ Heavy snow", 77: "🌨️ Snow grains", 80: "🌦️ Slight showers",
    81: "🌧️ Moderate showers", 82: "⛈️ Violent showers", 85: "🌨️ Slight snow showers",
    86: "🌨️ Heavy snow showers", 95: "⛈️ Thunderstorm", 96: "⛈️ Thunderstorm + hail",
    99: "⛈️ Thunderstorm + heavy hail",
}


def _fetch_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        full_url,
        headers={"User-Agent": "Orivellum/1.0 (local AI assistant)"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


_US_STATES_FULL = (
    "Alabama","Alaska","Arizona","Arkansas","California","Colorado","Connecticut",
    "Delaware","Florida","Georgia","Hawaii","Idaho","Illinois","Indiana","Iowa",
    "Kansas","Kentucky","Louisiana","Maine","Maryland","Massachusetts","Michigan",
    "Minnesota","Mississippi","Missouri","Montana","Nebraska","Nevada",
    "New Hampshire","New Jersey","New Mexico","New York","North Carolina",
    "North Dakota","Ohio","Oklahoma","Oregon","Pennsylvania","Rhode Island",
    "South Carolina","South Dakota","Tennessee","Texas","Utah","Vermont",
    "Virginia","Washington","West Virginia","Wisconsin","Wyoming",
)

def _geocode(location: str) -> dict[str, Any] | None:
    """Try several progressively simpler variants of *location* to handle
    compound strings like 'Mystic CT', 'Mystic Connecticut', 'Paris, France'."""
    import re as _re

    def _try(name: str) -> dict[str, Any] | None:
        name = name.strip().rstrip(",.").strip()
        if not name:
            return None
        try:
            data = _fetch_json(_GEO_API, {"name": name, "count": 1,
                                          "language": "en", "format": "json"})
            results = data.get("results", [])
            return results[0] if results else None
        except Exception as exc:
            logger.warning("Geocoding failed for %r: %s", name, exc)
            return None

    # 1. As-is
    result = _try(location)
    if result:
        return result

    # 2. Strip trailing US state abbreviation ("Mystic CT" → "Mystic")
    abbr_stripped = _re.sub(
        r",?\s+\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|"
        r"ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|"
        r"SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|USA|US)\b\.?$",
        "", location, flags=_re.IGNORECASE,
    ).strip()
    if abbr_stripped and abbr_stripped.lower() != location.lower():
        result = _try(abbr_stripped)
        if result:
            return result

    # 3. Strip trailing full US state name ("Mystic Connecticut" → "Mystic")
    for state in _US_STATES_FULL:
        pat = _re.compile(r",?\s+" + _re.escape(state) + r"\s*$", _re.IGNORECASE)
        stripped2 = pat.sub("", location).strip()
        if stripped2 and stripped2.lower() != location.lower():
            result = _try(stripped2)
            if result:
                return result
            break   # only attempt one state removal

    # 4. Everything before the first comma ("London, Ontario" → "London")
    comma_part = location.split(",")[0].strip()
    if comma_part and comma_part.lower() != location.lower():
        result = _try(comma_part)
        if result:
            return result

    # 5. First word only ("Mystic Connecticut" → "Mystic")
    first_word = location.split()[0].rstrip(",.")
    if len(first_word) > 2 and first_word.lower() not in (
        location.lower(), comma_part.lower()
    ):
        result = _try(first_word)
        if result:
            return result

    return None


def _wind_direction(deg: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(deg / 45) % 8]


def get_weather(location: str) -> str:
    """Fetch current weather for *location* and return a markdown-formatted card.

    Never raises — returns a user-visible error string on failure.
    """
    if not location or location.lower() in ("null", "none", "unknown"):
        return (
            "📍 **Weather**\n\n"
            "I couldn't determine the location you're asking about. "
            "Please specify a city, e.g. *\"What's the weather in London?\"*"
        )

    geo = _geocode(location)
    if not geo:
        return (
            f"📍 **Weather for {location}**\n\n"
            "I couldn't find that location. Please try a more specific city name."
        )

    lat  = geo["latitude"]
    lon  = geo["longitude"]
    name = geo.get("name", location)
    country = geo.get("country_code", "").upper()
    display = f"{name}, {country}" if country else name

    try:
        data = _fetch_json(_WEATHER_API, {
            "latitude": lat,
            "longitude": lon,
            "current_weather": "true",
            "hourly": "relativehumidity_2m,apparent_temperature",
            "forecast_days": 1,
            "timezone": "auto",
        })
    except Exception as exc:
        logger.warning("Weather fetch failed for %r: %s", location, exc)
        return (
            f"📍 **Weather for {display}**\n\n"
            "Could not retrieve weather data right now. "
            "Please try again in a moment."
        )

    cw = data.get("current_weather", {})
    temp     = cw.get("temperature")
    windspd  = cw.get("windspeed")
    winddir  = cw.get("winddirection")
    wmo      = cw.get("weathercode", 0)
    condition = _WMO_CODES.get(int(wmo), "Unknown")

    # Get humidity from hourly (first hour)
    humidity = None
    try:
        humidity_arr = data.get("hourly", {}).get("relativehumidity_2m", [])
        if humidity_arr:
            humidity = humidity_arr[0]
    except Exception:
        pass

    # Apparent temperature (feels like)
    feels_like = None
    try:
        fl_arr = data.get("hourly", {}).get("apparent_temperature", [])
        if fl_arr:
            feels_like = fl_arr[0]
    except Exception:
        pass

    lines = [f"📍 **Weather — {display}**\n", f"{condition}"]
    if temp is not None:
        feels_str = f" (feels like {feels_like:.0f}°C)" if feels_like is not None else ""
        lines.append(f"🌡️ **Temperature:** {temp:.0f}°C{feels_str}")
    if humidity is not None:
        lines.append(f"💧 **Humidity:** {humidity}%")
    if windspd is not None:
        dir_str = f" {_wind_direction(winddir)}" if winddir is not None else ""
        lines.append(f"💨 **Wind:** {windspd:.0f} km/h{dir_str}")
    lines.append(f"\n*Data from [Open-Meteo](https://open-meteo.com/) · {lat:.2f}°N, {lon:.2f}°E*")

    return "\n".join(lines)
