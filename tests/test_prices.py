"""Prix Alpaca : flux SIP uniquement, jamais de plage dans le futur."""

from datetime import date

import pytest

from news_alpha import net, prices

YESTERDAY = date(2026, 10, 23)


@pytest.fixture
def alpaca(monkeypatch):
    """Faux client Alpaca : enregistre les URL demandées et renvoie une barre."""
    monkeypatch.setenv("ALPACA_API_KEY", "test")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test")
    monkeypatch.setattr(prices, "last_complete_session_day", lambda: YESTERDAY)
    urls = []

    def fake_get(url, headers, bucket, min_interval, cache_ok=None):
        urls.append(url)
        return b'{"bars":[{"t":"2026-10-23T04:00:00Z","o":1,"h":1,"l":1,"c":1,"v":1}]}'

    monkeypatch.setattr(net, "get", fake_get)
    return urls


def test_range_ending_in_the_future_is_capped_to_yesterday(alpaca):
    bars, source = prices.daily_bars("IWM", date(2026, 9, 1), date(2026, 12, 29), "all")
    assert "end=2026-10-23" in alpaca[0] and "feed=sip" in alpaca[0]
    assert source == "alpaca:sip" and len(bars) == 1


def test_range_entirely_in_the_future_makes_no_request(alpaca):
    assert prices.daily_bars("IWM", date(2026, 11, 2), date(2026, 12, 29), "all") == ([], "alpaca:sip")
    assert alpaca == []


def test_sip_refusal_is_an_error_never_a_silent_iex_fallback(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test")
    urls = []

    def refuse(url, headers, bucket, min_interval, cache_ok=None):
        urls.append(url)
        raise net.HttpError(url, 403, '{"message":"subscription does not permit querying recent SIP data"}')

    monkeypatch.setattr(net, "get", refuse)
    with pytest.raises(net.HttpError):
        prices.daily_bars("IWM", date(2025, 9, 1), date(2025, 10, 1), "all")
    assert all("feed=iex" not in url for url in urls)
