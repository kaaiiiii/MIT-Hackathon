from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True, slots=True)
class SpokenResponseCheck:
    valid: bool
    reason: str | None = None


def validate_spoken_response(
    response: str,
    previous_buyer_turn: str | None = None,
    recent_buyer_turns: list[str] | None = None,
    allow_question_repair: bool = False,
) -> SpokenResponseCheck:
    response = response.strip()
    if len(response.split()) > 45:
        return SpokenResponseCheck(False, "Response is longer than 45 words.")
    if response.count("?") > 1:
        return SpokenResponseCheck(False, "Response contains more than one question.")

    robotic_openers = (
        "understood.",
        "got it.",
        "thank you for clarifying.",
        "thanks for clarifying.",
    )
    if response.casefold().startswith(robotic_openers):
        return SpokenResponseCheck(False, "Response uses a robotic acknowledgment opener.")

    formal_phrases = (
        "can you walk me through",
        "once you have those details",
    )
    lowered = response.casefold()
    if any(phrase in lowered for phrase in formal_phrases):
        return SpokenResponseCheck(False, "Response uses formal template language.")

    if previous_buyer_turn:
        current = _normalize(response)
        previous = _normalize(previous_buyer_turn)
        if current == previous:
            return SpokenResponseCheck(False, "Response repeats the previous buyer turn.")
        if SequenceMatcher(None, current, previous).ratio() >= 0.88:
            return SpokenResponseCheck(False, "Response closely repeats the previous buyer turn.")

    history = recent_buyer_turns or []
    if previous_buyer_turn and previous_buyer_turn not in history:
        history = [*history, previous_buyer_turn]
    if not allow_question_repair and any(
        _repeats_question(response, prior) for prior in history
    ):
        return SpokenResponseCheck(False, "Response repeats a recent buyer question.")

    return SpokenResponseCheck(True)


def previous_buyer_turn(transcript: list) -> str | None:
    return next(
        (event.text for event in reversed(transcript) if event.speaker == "agent"),
        None,
    )


def recent_buyer_turns(transcript: list, limit: int = 6) -> list[str]:
    turns = [event.text for event in transcript if event.speaker == "agent"]
    return turns[-limit:]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", text.casefold())).strip()


def _repeats_question(current_turn: str, prior_turn: str) -> bool:
    for current in _questions(current_turn):
        for prior in _questions(prior_turn):
            if SequenceMatcher(None, current, prior).ratio() >= 0.82:
                return True
            current_terms = _question_terms(current)
            prior_terms = _question_terms(prior)
            if len(current_terms) >= 2 and len(prior_terms) >= 2:
                overlap = len(current_terms & prior_terms) / min(
                    len(current_terms), len(prior_terms)
                )
                if overlap >= 0.8:
                    return True
    return False


def _questions(text: str) -> list[str]:
    return [
        _normalize(part)
        for part in re.findall(r"(?:^|[.!])\s*([^?]+\?)", text)
        if _normalize(part)
    ]


def _question_terms(question: str) -> set[str]:
    stop_words = {
        "a",
        "an",
        "and",
        "are",
        "can",
        "could",
        "do",
        "for",
        "how",
        "is",
        "me",
        "please",
        "the",
        "to",
        "what",
        "would",
        "you",
        "your",
    }
    return {word for word in _normalize(question).split() if word not in stop_words}
