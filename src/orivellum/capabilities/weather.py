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


def _geocode(location: str) -> dict[str, Any] | None:
    try:
        data = _fetch_json(_GEO_API, {"name": location, "count": 1, "language": "en", "format": "json"})
        results = data.get("results", [])
        return results[0] if results else None
    except Exception as exc:
        logger.warning("Geocoding failed for %r: %s", location, exc)
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
