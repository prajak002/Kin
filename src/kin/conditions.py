"""Today's weather and air quality for a town, with advice for an older adult.

Open-Meteo (free, no key, open data): geocoding, forecast and air quality.
Thresholds follow common public-health guidance for older people: heat risk from
a "feels like" 35°C, cold risk at 5°C, unhealthy air from US AQI 150, high UV from 8.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
AIR = "https://air-quality-api.open-meteo.com/v1/air-quality"

HOT_FEELS_LIKE = 35
COLD_MIN = 5
UNHEALTHY_AQI = 150
HIGH_UV = 8


def advice_for(today: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    if (feels := today.get("feels_like_max_c")) is not None and feels >= HOT_FEELS_LIKE:
        out.append({"risk": "heat", "say": f"It will feel like {feels:.0f}°C. Drink water often and stay indoors in the afternoon."})
    if (low := today.get("min_c")) is not None and low <= COLD_MIN:
        out.append({"risk": "cold", "say": f"It drops to {low:.0f}°C. Keep warm, especially at night."})
    if (aqi := today.get("us_aqi")) is not None and aqi >= UNHEALTHY_AQI:
        out.append({"risk": "air", "say": f"Air quality is poor (AQI {aqi:.0f}). Keep windows closed and avoid walks outside."})
    if (uv := today.get("uv_max")) is not None and uv >= HIGH_UV:
        out.append({"risk": "uv", "say": f"The sun is strong (UV {uv:.0f}). Avoid going out at midday."})
    if (rain := today.get("rain_chance_pct")) is not None and rain >= 70:
        out.append({"risk": "rain", "say": "Rain is likely. Take care on wet floors and steps."})
    return out


async def local_conditions(place: str, http: httpx.AsyncClient | None = None) -> dict[str, Any]:
    own = http is None
    http = http or httpx.AsyncClient(timeout=10)
    try:
        geo = (await http.get(GEOCODE, params={"name": place, "count": 1})).json().get("results") or []
        if not geo:
            raise ValueError(f"Couldn't find '{place}'.")
        lat, lon, name = geo[0]["latitude"], geo[0]["longitude"], geo[0]["name"]
        forecast, air = await asyncio.gather(
            http.get(FORECAST, params={
                "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m",
                "daily": "temperature_2m_max,temperature_2m_min,apparent_temperature_max,precipitation_probability_max,uv_index_max",
            }),
            http.get(AIR, params={"latitude": lat, "longitude": lon, "current": "us_aqi,pm2_5"}),
        )
        f, a = forecast.json(), air.json()
        daily = {k: (v[0] if v else None) for k, v in f.get("daily", {}).items() if k != "time"}
        today = {
            "place": name,
            "now_c": f.get("current", {}).get("temperature_2m"),
            "feels_like_now_c": f.get("current", {}).get("apparent_temperature"),
            "humidity_pct": f.get("current", {}).get("relative_humidity_2m"),
            "max_c": daily.get("temperature_2m_max"),
            "min_c": daily.get("temperature_2m_min"),
            "feels_like_max_c": daily.get("apparent_temperature_max"),
            "rain_chance_pct": daily.get("precipitation_probability_max"),
            "uv_max": daily.get("uv_index_max"),
            "us_aqi": a.get("current", {}).get("us_aqi"),
            "pm2_5": a.get("current", {}).get("pm2_5"),
        }
        return {**today, "advice": advice_for(today), "source": "Open-Meteo"}
    finally:
        if own:
            await http.aclose()
