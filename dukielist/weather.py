"""Previsão diária da Open-Meteo, sem dependências ou credenciais extras."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class WeatherError(Exception):
    """Falha recuperável ao consultar ou interpretar a previsão."""


class WeatherConnectionError(WeatherError):
    """Falha de rede ou indisponibilidade de um serviço externo."""


@dataclass(frozen=True)
class WeatherLocation:
    name: str
    region: str
    latitude: float
    longitude: float
    timezone: str


@dataclass(frozen=True)
class ForecastDay:
    day: date
    minimum_c: float
    maximum_c: float
    rain_mm: float | None
    rain_probability: int | None
    weather_code: int | None


@dataclass(frozen=True)
class WeatherForecast:
    location: str
    days: tuple[ForecastDay, ...]
    automatic: bool = False


def _read_json(base_url: str, params: dict[str, object]) -> dict:
    query = urlencode(params)
    request = Request(
        f"{base_url}?{query}" if query else base_url,
        headers={"User-Agent": "DukieList/1.3 (weather forecast)"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            result = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise WeatherConnectionError(
            "Sem conexão ou serviço indisponível. Verifique a internet e tente novamente."
        ) from exc
    except ValueError as exc:
        raise WeatherError("O serviço retornou uma resposta inválida.") from exc
    if not isinstance(result, dict) or result.get("error"):
        raise WeatherError("A API de previsão retornou uma resposta inválida.")
    return result


def _number(value: object, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise WeatherError("A API de previsão retornou dados incompletos.")
    return float(value)


def _coordinate(value: object, minimum: float, maximum: float) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError
        return number
    except (TypeError, ValueError) as exc:
        raise WeatherError("A localização retornada pelo serviço é inválida.") from exc


def detect_location() -> WeatherLocation:
    """Obtém cidade aproximada pelo IP público, sem armazenar o endereço IP."""
    result = _read_json("https://get.geojs.io/v1/ip/geo.json", {})
    name = result.get("city")
    region = result.get("region") or result.get("country")
    timezone = result.get("timezone") or "auto"
    if not isinstance(name, str) or not name.strip():
        raise WeatherError("Não foi possível identificar sua cidade pelo IP.")
    return WeatherLocation(
        name.strip(), str(region or "").strip(),
        _coordinate(result.get("latitude"), -90, 90),
        _coordinate(result.get("longitude"), -180, 180),
        str(timezone),
    )


def fetch_forecast(city: str) -> WeatherForecast:
    """Busca hoje e os próximos três dias para uma cidade informada pelo usuário."""
    city = city.strip()
    if len(city) < 2:
        raise WeatherError("Informe uma cidade com pelo menos 2 caracteres.")

    places = _read_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": city, "count": 1, "language": "pt", "format": "json"},
    ).get("results")
    if not isinstance(places, list) or not places:
        raise WeatherError("Cidade não encontrada. Tente 'Cidade, Estado' ou 'Cidade, País'.")
    place = places[0]
    try:
        name = place["name"]
        timezone = place["timezone"]
        if not isinstance(name, str) or not name.strip() or not isinstance(timezone, str) or not timezone:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherError("A localização retornada pela API é inválida.") from exc

    location = WeatherLocation(
        name.strip(), str(place.get("admin1") or place.get("country") or ""),
        _coordinate(place.get("latitude"), -90, 90),
        _coordinate(place.get("longitude"), -180, 180),
        timezone,
    )
    return fetch_forecast_for_location(location)


def fetch_local_forecast() -> WeatherForecast:
    return fetch_forecast_for_location(detect_location(), automatic=True)


def fetch_forecast_for_location(
    location: WeatherLocation, *, automatic: bool = False
) -> WeatherForecast:

    result = _read_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "daily": "temperature_2m_max,temperature_2m_min,rain_sum,showers_sum,precipitation_probability_max,weather_code",
            "timezone": location.timezone,
            "forecast_days": 4,
        },
    )
    daily = result.get("daily")
    if not isinstance(daily, dict):
        raise WeatherError("A API de previsão retornou dados incompletos.")
    fields = ("time", "temperature_2m_min", "temperature_2m_max", "rain_sum", "showers_sum",
              "precipitation_probability_max", "weather_code")
    if any(not isinstance(daily.get(field), list) or len(daily[field]) < 4 for field in fields):
        raise WeatherError("A API de previsão retornou menos de quatro dias.")

    days = []
    for index in range(4):
        try:
            day = date.fromisoformat(daily["time"][index])
            minimum = _number(daily["temperature_2m_min"][index])
            maximum = _number(daily["temperature_2m_max"][index])
            rain = _number(daily["rain_sum"][index], optional=True)
            showers = _number(daily["showers_sum"][index], optional=True)
            probability = _number(daily["precipitation_probability_max"][index], optional=True)
            code = _number(daily["weather_code"][index], optional=True)
            if (rain is not None and rain < 0) or (showers is not None and showers < 0):
                raise ValueError
            if probability is not None and not 0 <= probability <= 100:
                raise ValueError
        except (TypeError, ValueError, IndexError) as exc:
            raise WeatherError("A API de previsão retornou dados inválidos.") from exc
        days.append(ForecastDay(
            day, minimum, maximum, None if rain is None or showers is None else rain + showers,
            None if probability is None else round(probability),
            None if code is None else int(code),
        ))
    return WeatherForecast(
        ", ".join(part for part in (location.name, location.region) if part),
        tuple(days),
        automatic,
    )


def weather_description(code: int | None) -> str:
    if code is None:
        return "Condição indisponível"
    if code == 0:
        return "Céu limpo"
    if code in (1, 2):
        return "Parcialmente nublado"
    if code == 3:
        return "Nublado"
    if code in (45, 48):
        return "Nevoeiro"
    if code in (51, 53, 55, 56, 57):
        return "Garoa"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "Chuva"
    if code in (71, 73, 75, 77, 85, 86):
        return "Neve"
    if code in (95, 96, 99):
        return "Trovoadas"
    return "Condição variável"
