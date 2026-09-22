"""Consulta de previsão sem bloquear a interface do terminal."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from .weather import WeatherError, WeatherForecast, fetch_forecast, fetch_local_forecast, weather_description


def render_forecast(forecast: WeatherForecast) -> Text:
    text = Text()
    weekdays = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
    for index, item in enumerate(forecast.days):
        label = "Hoje" if index == 0 else weekdays[item.day.weekday()]
        rain = "—" if item.rain_mm is None else f"{item.rain_mm:.1f}".replace(".", ",")
        chance = "—" if item.rain_probability is None else f"{item.rain_probability}%"
        text.append(f"{label:<4}  {item.day:%d/%m}  ", style="#00e5ff bold")
        text.append(f"{item.minimum_c:.0f}–{item.maximum_c:.0f} °C  ", style="#dbe8ff")
        text.append(f"Chuva: {rain} mm", style="#38bdf8 bold" if item.rain_mm else "#9bb2d6")
        text.append(f"  ·  {chance} de chance\n", style="#b8d7f7")
        text.append(f"        {weather_description(item.weather_code)}\n", style="#9bb2d6")
    return text


def render_compact_forecast(forecast: WeatherForecast, index: int) -> Text:
    item = forecast.days[index]
    label = "Hoje" if index == 0 else ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")[item.day.weekday()]
    city = forecast.location.split(", ", 1)[0][:18]
    rain = "—" if item.rain_mm is None else f"{item.rain_mm:.1f}".replace(".", ",")
    chance = "—" if item.rain_probability is None else f"{item.rain_probability}%"
    text = Text(justify="center")
    text.append(f"{city} · {label} {item.day:%d/%m}\n", style="#00e5ff bold")
    text.append(f"{item.minimum_c:.0f}–{item.maximum_c:.0f}°  ", style="#dbe8ff")
    text.append(f"Chuva {rain} mm", style="#38bdf8 bold")
    text.append(f"  {chance}", style="#b8d7f7")
    return text


class WeatherScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Fechar"),
    ]

    def __init__(
        self,
        *,
        initial_forecast: WeatherForecast | None = None,
        on_forecast: Callable[[WeatherForecast], None] | None = None,
    ) -> None:
        super().__init__()
        self.initial_forecast = initial_forecast
        self.on_forecast = on_forecast

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static("☁  PREVISÃO DO TEMPO", classes="modal-title"),
            Static("Digite uma cidade; acrescente estado ou país se necessário.", classes="modal-subtitle"),
            Input(placeholder="Ex.: Maringá, Paraná", id="weather-city"),
            Horizontal(
                Button("Buscar / atualizar", id="weather-search", classes="primary-action"),
                Button("Minha localização", id="weather-local", classes="secondary-action"),
                Button("Fechar", id="weather-close", classes="secondary-action"),
                id="weather-actions",
            ),
            Static("Informe uma cidade para ver hoje e mais três dias.", id="weather-status"),
            Static("", id="weather-location"),
            Static("", id="weather-results"),
            Static("Dados: Open-Meteo (open-meteo.com) · localização aproximada: GeoJS (geojs.io).", id="weather-source"),
            id="weather-card",
        )

    def on_mount(self) -> None:
        city = (
            self.initial_forecast.location
            if self.initial_forecast else self.app.storage.get_setting("weather_city")
        )
        field = self.query_one("#weather-city", Input)
        field.focus()
        if city:
            field.value = city
        if self.initial_forecast:
            self._show_forecast(self.initial_forecast)

    def _show_forecast(self, forecast: WeatherForecast) -> None:
        source = "Localização aproximada pelo IP" if forecast.automatic else "Cidade selecionada"
        self.query_one("#weather-status", Static).update(f"{source} · hoje + próximos 3 dias")
        self.query_one("#weather-location", Static).update(forecast.location)
        self.query_one("#weather-results", Static).update(render_forecast(forecast))

    def _start_search(self, city: str) -> None:
        city = city.strip()
        if len(city) < 2:
            self.query_one("#weather-status", Static).update("Informe uma cidade com pelo menos 2 caracteres.")
            return
        self.query_one("#weather-status", Static).update("Consultando previsão…")
        self.query_one("#weather-location", Static).update("")
        self.query_one("#weather-results", Static).update("")
        self.run_worker(self._load(city), group="weather", exclusive=True)

    def _start_local_search(self) -> None:
        self.query_one("#weather-status", Static).update("Detectando localização aproximada…")
        self.query_one("#weather-location", Static).update("")
        self.query_one("#weather-results", Static).update("")
        self.run_worker(self._load(None), group="weather", exclusive=True)

    async def _load(self, city: str | None) -> None:
        try:
            if city is None:
                forecast = await asyncio.to_thread(fetch_local_forecast)
            else:
                forecast = await asyncio.to_thread(fetch_forecast, city)
        except WeatherError as exc:
            self.query_one("#weather-status", Static).update(str(exc))
            self.notify(str(exc), title="Previsão do tempo", severity="warning", timeout=8)
            return
        if city is not None:
            self.app.storage.set_setting("weather_city", city)
        else:
            self.query_one("#weather-city", Input).value = forecast.location
        self._show_forecast(forecast)
        if self.on_forecast:
            self.on_forecast(forecast)

    def action_search(self) -> None:
        self._start_search(self.query_one("#weather-city", Input).value)

    def action_close(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "weather-city":
            self.action_search()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "weather-search":
            self.action_search()
        elif event.button.id == "weather-local":
            self._start_local_search()
        else:
            self.action_close()
