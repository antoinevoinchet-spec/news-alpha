"""Étape 4 : scoring LLM d'un 8-K, sortie validée contre le schéma du SPEC §7."""

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from news_alpha import ROOT

PROMPT_VERSION = "v2"
MODEL = "claude-haiku-4-5-20251001"  # fixe la fenêtre anti look-ahead (SPEC §6)
PROMPT_PATH = ROOT / "prompts" / f"score_8k_{PROMPT_VERSION}.md"
# Empreintes inscrites au SPEC : le prompt (audit C18) et le module d'analyse (décision M7)
# ne servent que s'ils sont identiques à leur version gelée.
ANALYSIS_SHA256 = "01ffb5d33915023f159e8666d93a3bd91817041d3003f20110c36f946c878136"
PROMPT_SHA256 = "7611eddae367c6d65aeeadc2d873e5290a370bb9ecb2b0caade9a5908a1d2691"
# Dernière version d'API qui accepte `temperature` ; le SDK 2.x envoie 2026-10-21, qui la refuse (audit C20).
API_VERSION = "2023-06-01"
TEMPERATURE = 0
MAX_TOKENS = 1024
USD_PER_MTOK_INPUT, USD_PER_MTOK_OUTPUT = 1.0, 5.0  # tarif Claude Haiku 4.5
CODE_FENCE = re.compile(r"```json\n(.*)\n```", re.S)
FILING_TAG = re.compile(r"<(?=/?filing\b)", re.I)  # '</filing>' du dépôt -> '&lt;/filing>'
PRICE_LINES_MARK = "<!-- Price lines"

TEMPLATE_TEXT = PROMPT_PATH.read_text()  # lu une fois : le fichier ne peut plus changer en cours d'exécution
TEMPLATE_SHA256 = hashlib.sha256(TEMPLATE_TEXT.encode()).hexdigest()


def _split_template(text: str) -> tuple[str, dict[str, str]]:
    """(message envoyé, formulations de la ligne de prix) : les deux vivent dans prompts/."""
    body, _, variants = text.partition(PRICE_LINES_MARK)
    lines = variants.splitlines()[1:]  # la suite de la ligne de commentaire
    return body, dict(line.split(" = ", 1) for line in lines if line.strip())


TEMPLATE, PRICE_LINES = _split_template(TEMPLATE_TEXT)


class Score8K(BaseModel):
    """Schéma du SPEC §7 : clé absente, clé en trop, mauvais type ou hors bornes = rejet."""

    model_config = ConfigDict(extra="forbid", strict=True)

    event_type: Literal[
        "earnings", "guidance", "contract", "m&a", "management", "financing", "legal", "other"
    ]
    direction: int = Field(ge=-2, le=2)
    magnitude: int = Field(ge=0, le=3)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    priced_in: float = Field(ge=0, le=1, allow_inf_nan=False)
    horizon_days: int = Field(ge=1)
    key_facts: list[str] = Field(max_length=3)
    rationale: str = Field(pattern=r"\S")  # au moins un caractère visible

    @property
    def composite(self) -> float:
        return self.direction * self.magnitude * self.confidence * (1 - self.priced_in)


class DuplicateKey(ValueError):
    pass


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if duplicated:
        raise DuplicateKey(", ".join(duplicated))
    return dict(pairs)


def validate_output(raw: str) -> tuple[Score8K | None, str | None]:
    """Valide la réponse sans jamais en corriger le contenu.

    Seuls les espaces de bord et un unique emballage ```json ... ``` autour de toute
    la réponse sont retirés (décision du 25/10/2026, DECISIONS.md). Une clé répétée
    est un rejet : choisir l'une des valeurs serait corriger la réponse (audit C17).
    """
    text = raw.strip()
    fenced = CODE_FENCE.fullmatch(text)
    payload = fenced.group(1) if fenced else text
    try:
        json.loads(payload, object_pairs_hook=_no_duplicate_keys)
    except DuplicateKey as err:
        return None, f"json: clé dupliquée ({err})"
    except ValueError:
        pass  # JSON invalide : pydantic donne le motif ci-dessous
    try:
        return Score8K.model_validate_json(payload), None
    except ValidationError as err:
        first = err.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "json"
        return None, f"{location}: {first['msg']}"


def price_line(price_move: float | None, timing: str | None) -> str:
    """Formulation de la ligne de prix selon que le marché a pu réagir au dépôt (D35)."""
    if price_move is None or timing is None:
        return PRICE_LINES["unavailable"]
    return PRICE_LINES[timing].format(move=f"{price_move:+.2%}")


def build_prompt(filing_text: str, price_move: float | None, timing: str | None) -> str:
    """Le prompt ne reçoit que le texte du dépôt et le mouvement de cours (SPEC §6)."""
    contained = FILING_TAG.sub("&lt;", filing_text)  # le texte ne peut pas fermer sa balise (audit C19)
    return (TEMPLATE.replace("{{PRICE_MOVE}}", price_line(price_move, timing))
            .replace("{{FILING_TEXT}}", contained))


def make_client() -> anthropic.Anthropic:
    """Client fixé sur API_VERSION : l'en-tête personnalisé remplace celui du SDK."""
    return anthropic.Anthropic(default_headers={"anthropic-version": API_VERSION})


@dataclass
class ScoreResult:
    raw_output: str
    score: Score8K | None
    reject_reason: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float


def score_filing(client: anthropic.Anthropic, prompt: str) -> ScoreResult:
    """Un appel au modèle, sans outil ; les erreurs d'API remontent à l'appelant."""
    started = time.monotonic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        extra_body={"temperature": TEMPERATURE},  # le SDK 2.x n'a plus ce paramètre
    )
    latency = time.monotonic() - started
    raw = "".join(block.text for block in response.content if block.type == "text")
    score, reason = validate_output(raw)
    if response.stop_reason != "end_turn":
        score, reason = None, f"stop_reason: {response.stop_reason}"
    usage = response.usage
    cost = (
        usage.input_tokens * USD_PER_MTOK_INPUT + usage.output_tokens * USD_PER_MTOK_OUTPUT
    ) / 1e6
    return ScoreResult(raw, score, reason, usage.input_tokens, usage.output_tokens, cost, latency)
