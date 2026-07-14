"""Resolução reproduzível de locatários, devedores, emissores e ativos.

Correspondências exatas por identificador regulatório podem ser aceitas
automaticamente. Correspondências por nome são apenas propostas até atingirem
um limiar alto e, mesmo assim, preservam nome original, algoritmo e evidência.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Iterable


LEGAL_SUFFIXES = {
    "sa", "s a", "ltda", "eireli", "spe", "fii", "fundo", "investimento",
    "imobiliario", "imobiliaria", "brasil", "holding", "participacoes", "s", "a",
}


def digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalize_entity_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    trimmed = [token for token in tokens if token not in LEGAL_SUFFIXES]
    return " ".join(trimmed or tokens)


@dataclass(frozen=True)
class EntityMatch:
    canonical_id: int | None
    canonical_name: str | None
    confidence: float
    method: str
    status: str


def match_entity(
    raw_name: object,
    candidates: Iterable[dict],
    *,
    raw_identifier: object = None,
    auto_accept_name: float = .96,
    proposal_threshold: float = .82,
) -> EntityMatch:
    identifier = digits(raw_identifier)
    normalized = normalize_entity_name(raw_name)
    best: tuple[float, dict] | None = None
    for candidate in candidates:
        candidate_identifier = digits(candidate.get("legal_identifier"))
        if identifier and candidate_identifier and identifier == candidate_identifier:
            return EntityMatch(int(candidate["id"]), str(candidate.get("canonical_name")),
                               1.0, "exact_identifier", "accepted")
        candidate_name = normalize_entity_name(candidate.get("canonical_name"))
        if not normalized or not candidate_name:
            continue
        similarity = SequenceMatcher(None, normalized, candidate_name).ratio()
        token_a, token_b = set(normalized.split()), set(candidate_name.split())
        union = token_a | token_b
        token_score = len(token_a & token_b) / len(union) if union else 0.0
        score = .65 * similarity + .35 * token_score
        if best is None or score > best[0]:
            best = score, candidate
    if best is None or best[0] < proposal_threshold:
        return EntityMatch(None, None, 0.0 if best is None else best[0],
                           "unresolved_name", "unresolved")
    score, candidate = best
    status = "accepted" if score >= auto_accept_name else "proposed"
    return EntityMatch(int(candidate["id"]), str(candidate.get("canonical_name")),
                       float(score), "normalized_name", status)
