from brief.consensus import consensus_weather, feels_by_period
from brief.formatter import clothing_tip, feels_line, rain_section
from brief.models import WeatherReading
from brief.sources.weather import parse_open_meteo, parse_weatherapi

PERIODS = {"mañana": (6, 12), "tarde": (12, 18), "noche": (18, 24)}
TH = {"temp_c": 3.0, "rain_pct": 25.0}
ALERTS = {"feels_cold_c": 12, "feels_mild_c": 17, "feels_warm_c": 22}


def _r(src, feels):
    return WeatherReading(src, hourly_feels=feels)


def test_feels_period_median_of_min_and_max_across_sources():
    a = _r("A", {6: 10, 9: 14, 13: 15, 16: 18, 20: 12, 23: 13})
    b = _r("B", {6: 11, 9: 16, 13: 16, 16: 19, 20: 11, 23: 15})
    c = _r("C", {6: 12, 9: 15, 13: 14, 16: 17, 20: 13, 23: 14})
    got = feels_by_period([a, b, c], PERIODS)
    assert (got["mañana"].low, got["mañana"].high) == (11, 15)
    assert (got["tarde"].low, got["tarde"].high) == (15, 18)
    assert (got["noche"].low, got["noche"].high) == (12, 14)
    assert got["mañana"].n == 3


def _summary(feels, now_hour=None):
    r = WeatherReading(
        "A",
        temp=15,
        tmin=11,
        tmax=19,
        hourly_feels=feels,
        hourly_pop={8: 30, 14: 80, 20: 60},
    )
    s = consensus_weather([r], TH, PERIODS, now_hour=now_hour)
    assert s is not None
    return s


def test_feels_line_format():
    s = _summary({6: 11, 9: 15, 13: 15, 16: 18, 20: 12, 23: 14})
    assert feels_line(s) == "Sensación térmica: mañana 11-15 °C, tarde 15-18 °C, noche 12-14 °C"


def test_past_periods_marked_ya_paso_outside_morning_window():
    from brief.main import parse_as_of

    feels = {6: 11, 9: 15, 13: 15, 16: 18, 20: 12, 23: 14}
    morning = _summary(feels, now_hour=parse_as_of("08:00"))
    assert "ya pasó" not in (feels_line(morning) or "")
    assert "ya pasó" not in (rain_section(morning, {}) or "")

    afternoon = _summary(feels, now_hour=parse_as_of("15:00"))
    assert feels_line(afternoon) == "Sensación térmica: mañana ya pasó, tarde 15-18 °C, noche 12-14 °C"
    rain_aft = rain_section(afternoon, {}) or ""
    assert "mañana ya pasó, tarde 80 %, noche 60 %" in rain_aft

    night = _summary(feels, now_hour=parse_as_of("22:00"))
    assert feels_line(night) == "Sensación térmica: mañana ya pasó, tarde ya pasó, noche 12-14 °C"
    rain_night = rain_section(night, {}) or ""
    assert "mañana ya pasó, tarde ya pasó, noche 60 %" in rain_night
    assert "Lluvia probable en la noche (60 %)" in rain_night


def test_no_feels_data_omits_line():
    assert feels_line(_summary({})) is None


def test_clothing_thresholds():
    assert "abrigo" in clothing_tip(_summary({6: 9, 13: 15}), ALERTS)
    assert "chaqueta ligera" in clothing_tip(_summary({6: 13, 13: 16}), ALERTS)
    assert "ropa fresca" in clothing_tip(_summary({6: 18, 13: 24}), ALERTS)
    assert "abrigo" in clothing_tip(_summary({6: 9, 13: 25}), ALERTS)


def test_parse_open_meteo_and_weatherapi_fill_hourly_feels():
    times = [f"2026-10-25T{h:02d}:00" for h in range(24)]
    data = {
        "hourly": {
            "time": times,
            "temperature_2m_m1": [14.0] * 24,
            "apparent_temperature_m1": [float(h) for h in range(24)],
        },
        "current": {},
    }
    r = parse_open_meteo(data, {"temperature": ["m1"]}, 8)[0]
    assert r.hourly_feels[7] == 7.0
    wa = {
        "current": {"temp_c": 15, "feelslike_c": 14},
        "forecast": {
            "forecastday": [
                {"day": {}, "hour": [{"time": "2026-10-25 09:00", "chance_of_rain": 10, "feelslike_c": 13.5}]}
            ]
        },
    }
    assert parse_weatherapi(wa).hourly_feels == {9: 13.5}
