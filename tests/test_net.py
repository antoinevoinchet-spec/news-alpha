"""Couche réseau : les coupures et lenteurs passagères sont relancées, pas fatales."""

import http.client

import pytest

from news_alpha import net


class FakeResponse:
    headers: dict = {}

    def read(self) -> bytes:
        return b"ok"

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


@pytest.mark.parametrize("failure", [TimeoutError("The read operation timed out"),
                                     ConnectionResetError("reset by peer")])
def test_transient_network_failure_is_retried(monkeypatch, failure):
    attempts = []

    def flaky_urlopen(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise failure
        return FakeResponse()

    monkeypatch.setattr(net.urllib.request, "urlopen", flaky_urlopen)
    monkeypatch.setattr(net.time, "sleep", lambda seconds: None)
    assert net.get("https://example.test/x", {}, bucket="test", min_interval=0) == b"ok"
    assert len(attempts) == 2


def test_persistent_failure_is_raised_after_retries(monkeypatch):
    def dead_urlopen(request, timeout):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(net.urllib.request, "urlopen", dead_urlopen)
    monkeypatch.setattr(net.time, "sleep", lambda seconds: None)
    with pytest.raises(TimeoutError):
        net.get("https://example.test/x", {}, bucket="test", min_interval=0)


def test_truncated_cache_file_is_refetched_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(net, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(net.urllib.request, "urlopen", lambda request, timeout: FakeResponse())
    url = "https://example.test/cached"
    net.get(url, {}, bucket="test", min_interval=0, cache_ok=lambda body: True)
    [cached] = (tmp_path / "test").glob("*.gz")
    cached.write_bytes(cached.read_bytes()[:5])  # arrêt pendant l'écriture
    assert net.get(url, {}, bucket="test", min_interval=0, cache_ok=lambda body: True) == b"ok"
    assert not list((tmp_path / "test").glob("*.tmp"))


def test_sec_outage_of_several_minutes_is_waited_out_and_logged(monkeypatch, capsys):
    # Cas réels du 25/10/2026 : 4 coupures de suite en ~14 s, puis des 503 pendant plus de 2 min.
    attempts, waits = [], []

    def outage(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) <= 8:
            raise http.client.IncompleteRead(b"partial")
        return FakeResponse()

    monkeypatch.setattr(net.urllib.request, "urlopen", outage)
    monkeypatch.setattr(net.time, "sleep", waits.append)
    assert net.get("https://example.test/x", {}, bucket="test", min_interval=0) == b"ok"
    assert len(attempts) == 9 and sum(waits) >= 500
    assert capsys.readouterr().err.count("nouvel essai") == 8
