"""Natural-language timer commands, e.g. "45 min science on photosynthesis".

The regex parser handles the vast majority of inputs for free; the AI is only
asked when it finds neither a duration nor a topic.
"""

import re
from dataclasses import dataclass

DEFAULT_MINUTES = 25

_DURATION = re.compile(
    r"""
    (?:(?P<h>\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b\s*(?:(?P<hm>\d+)\s*(?:m|min|mins|minute|minutes)?\b)?)
    |(?:(?P<m>\d+)\s*(?:m|min|mins|minute|minutes)\b)
    |(?:\b(?P<bare>\d{1,3})\b)
    """,
    re.IGNORECASE | re.VERBOSE,
)
_HALF_HOUR = re.compile(r"\b(?:half\s+an?\s+hour|half\s+hour)\b", re.IGNORECASE)
_AN_HOUR = re.compile(r"\b(?:an|one)\s+hour\b", re.IGNORECASE)
_LABEL = re.compile(
    r"\b(?:on|about|re|covering|for)\s+(?P<label>.+)$|:\s*(?P<label2>.+)$",
    re.IGNORECASE,
)
_FILLER = re.compile(
    r"\b(?:study|studying|focus|focusing|session|pomodoro|timer|start|do|of|for|a|an|the|min|mins|minutes|"
    r"minute|hours?|hrs?|please|lets|let's|me)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Parsed:
    minutes: int | None
    topic_index: int | None
    label: str | None


def _minutes(text: str) -> tuple[int | None, str]:
    if m := _HALF_HOUR.search(text):
        return 30, text[: m.start()] + text[m.end() :]
    if m := _AN_HOUR.search(text):
        return 60, text[: m.start()] + text[m.end() :]
    m = _DURATION.search(text)
    if not m:
        return None, text
    if m.group("h"):
        minutes = round(float(m.group("h")) * 60) + int(m.group("hm") or 0)
    else:
        minutes = int(m.group("m") or m.group("bare"))
    rest = text[: m.start()] + text[m.end() :]
    return (minutes if 1 <= minutes <= 720 else None), rest


def _topic(text: str, topics: list[str]) -> tuple[int | None, str]:
    lowered = text.lower()
    # Exact name, longest first ("Organic Chemistry" before "Chemistry").
    for i in sorted(range(len(topics)), key=lambda i: -len(topics[i])):
        name = topics[i].lower()
        m = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", lowered)
        if m:
            return i, text[: m.start()] + text[m.end() :]
    # Prefix match on words of 4+ chars ("calc" -> "Calculus", "bio" needs 3+ only if exact start).
    for word in re.findall(r"[a-z][a-z'-]{2,}", lowered):
        for i, name in enumerate(topics):
            for part in name.lower().split():
                if len(word) >= 3 and part.startswith(word) and (len(word) >= 4 or len(part) <= 5):
                    return i, re.sub(
                        rf"(?<!\w){re.escape(word)}(?!\w)",
                        "",
                        text,
                        count=1,
                        flags=re.IGNORECASE,
                    )
    return None, text


def regex_parse(text: str, topics: list[str]) -> Parsed:
    minutes, rest = _minutes(text)
    label = None
    if m := _LABEL.search(rest):
        label = (m.group("label") or m.group("label2") or "").strip(" .,!") or None
        rest = rest[: m.start()]
    topic_index, rest = _topic(rest, topics)
    if topic_index is None and label:
        # "45 min on calculus" -> the label may actually be a topic
        topic_index, leftover = _topic(label, topics)
        if topic_index is not None:
            label = leftover.strip(" .,!") or None
    if label is None:
        leftover = " ".join(_FILLER.sub(" ", rest).split()).strip(" .,!")
        label = leftover or None
    return Parsed(minutes, topic_index, label[:80] if label else None)
