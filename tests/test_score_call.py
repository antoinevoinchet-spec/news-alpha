"""Appel au modèle : version d'API 2023-06-01 et température 0 (décision C20)."""

from types import SimpleNamespace

from news_alpha import score


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = SimpleNamespace(type="text", text="{}")
        usage = SimpleNamespace(input_tokens=10, output_tokens=2)
        return SimpleNamespace(content=[text], stop_reason="end_turn", usage=usage)


def test_scoring_call_sends_temperature_zero_and_nothing_else_than_the_prompt():
    messages = FakeMessages()
    score.score_filing(SimpleNamespace(messages=messages), "PROMPT")
    [call] = messages.calls
    assert call["extra_body"] == {"temperature": 0}
    assert call["model"] == "claude-haiku-4-5-20251001"
    assert call["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert "tools" not in call and "system" not in call


def test_client_is_pinned_to_the_api_version_that_accepts_temperature(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert score.make_client().default_headers["anthropic-version"] == "2023-06-01"
