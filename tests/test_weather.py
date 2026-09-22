import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

from dukielist.storage import SQLiteStorage
from dukielist.weather import (
    WeatherConnectionError, WeatherError, detect_location, fetch_forecast,
    fetch_local_forecast,
)
from dukielist.weather_screens import render_compact_forecast, render_forecast


PLACE = {
    "results": [{
        "name": "Curitiba", "admin1": "Paraná", "latitude": -25.43,
        "longitude": -49.27, "timezone": "America/Sao_Paulo",
    }]
}
DAILY = {
    "daily": {
        "time": ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"],
        "temperature_2m_min": [12, 13, 14, 15],
        "temperature_2m_max": [20, 21, 22, 23],
        "rain_sum": [0, 2.5, 11.2, None],
        "showers_sum": [0, 0.5, 1.0, None],
        "precipitation_probability_max": [5, 70, 90, None],
        "weather_code": [0, 61, 95, None],
    }
}
GEOJS = {
    "city": "Maringá", "region": "Paraná", "latitude": "-23.42",
    "longitude": "-51.93", "timezone": "America/Sao_Paulo",
}


class WeatherTest(unittest.TestCase):
    @patch("dukielist.weather._read_json", side_effect=[PLACE, DAILY])
    def test_four_days_and_rain_in_mm(self, read_json):
        forecast = fetch_forecast("Curitiba, Paraná")
        self.assertEqual(forecast.location, "Curitiba, Paraná")
        self.assertEqual(len(forecast.days), 4)
        self.assertEqual(forecast.days[3].day, date(2026, 9, 24))
        self.assertEqual(forecast.days[2].rain_mm, 12.2)
        self.assertIsNone(forecast.days[3].rain_mm)
        self.assertEqual(read_json.call_args_list[1].args[1]["forecast_days"], 4)
        self.assertIn("rain_sum", read_json.call_args_list[1].args[1]["daily"])
        self.assertIn("showers_sum", read_json.call_args_list[1].args[1]["daily"])
        rendered = render_forecast(forecast).plain
        self.assertIn("12,2 mm", rendered)
        self.assertIn("— mm", rendered)
        self.assertIn("90% de chance", rendered)
        self.assertIn("12,2 mm", render_compact_forecast(forecast, 2).plain)

    @patch("dukielist.weather._read_json", side_effect=[GEOJS, DAILY])
    def test_auto_location_uses_ip_coordinates_and_four_days(self, read_json):
        forecast = fetch_local_forecast()
        self.assertTrue(forecast.automatic)
        self.assertEqual(forecast.location, "Maringá, Paraná")
        self.assertEqual(len(forecast.days), 4)
        self.assertEqual(read_json.call_args_list[0].args[0],
                         "https://get.geojs.io/v1/ip/geo.json")
        self.assertEqual(read_json.call_args_list[1].args[1]["latitude"], -23.42)

    @patch("dukielist.weather._read_json", return_value={"city": "", "latitude": "1"})
    def test_auto_location_without_city_is_rejected(self, _read_json):
        with self.assertRaisesRegex(WeatherError, "identificar sua cidade"):
            detect_location()

    @patch("dukielist.weather.urlopen", side_effect=URLError("offline"))
    def test_network_failure_has_actionable_error(self, _urlopen):
        with self.assertRaisesRegex(WeatherConnectionError, "Verifique a internet"):
            detect_location()

    @patch("dukielist.weather._read_json", return_value={"results": []})
    def test_city_not_found(self, _read_json):
        with self.assertRaisesRegex(WeatherError, "Cidade não encontrada"):
            fetch_forecast("Cidade inexistente")

    @patch("dukielist.weather._read_json", side_effect=[PLACE, {"daily": {}}])
    def test_incomplete_forecast_is_not_shown_as_zero_rain(self, _read_json):
        with self.assertRaisesRegex(WeatherError, "menos de quatro dias"):
            fetch_forecast("Curitiba")

    def test_city_preference_is_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.db"
            storage = SQLiteStorage(path)
            self.assertIsNone(storage.get_setting("weather_city"))
            storage.set_setting("weather_city", "Curitiba, Paraná")
            self.assertEqual(SQLiteStorage(path).get_setting("weather_city"), "Curitiba, Paraná")
            storage.set_setting("weather_city", "São Paulo")
            self.assertEqual(storage.get_setting("weather_city"), "São Paulo")
