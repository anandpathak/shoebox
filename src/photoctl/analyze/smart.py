"""Plain-English search: "kids playing in snow", "Asha at the beach in 2024".

Dates, "videos", and people's names in the query become filters (people come from the catalog); the
rest is matched by meaning against photo vectors (SigLIP 2). Keyword matches (tags, captions, places)
are boosted, so exact hits still come first.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Callable

import numpy as np

from ..catalog.query import parse_query, search, tokens

MIN_PROBABILITY = 0.001       # SigLIP's calibrated "this text describes the image" probability
KEYWORD_BOOST = 0.02          # added to the cosine similarity for a keyword match
WEAK_RESULTS = 12             # nothing clears the bar: show this many closest matches, flagged as weak


def names_in(conn: sqlite3.Connection, text: str) -> tuple[list[str], str]:
    """People named in the query (longest names first) and the query without them."""
    names = sorted({r[0] for r in conn.execute("SELECT name FROM people WHERE name IS NOT NULL AND merged_into IS NULL")
                    if r[0] and r[0] != "_UNKNOWN_"}, key=len, reverse=True)
    found, rest = [], " " + text + " "
    for n in names:
        pat = re.compile(r"(?<!\w)" + re.escape(n) + r"(?!\w)", re.IGNORECASE)
        if pat.search(rest):
            found.append(n)
            rest = pat.sub(" ", rest)
    return found, " ".join(rest.split())


def smart_search(conn: sqlite3.Connection, q: str, *, embed_text: Callable[[str], np.ndarray],
                 probability: Callable[[np.ndarray], np.ndarray], index, limit: int = 120, **filters) -> dict:
    people, rest = names_in(conn, q)
    parsed = parse_query(rest)
    free = " ".join(parsed["terms"])
    person = " ".join(filter(None, [filters.pop("person", None), *people])) or None
    base = dict(filters, person=person, date_from=filters.get("date_from") or parsed["date_from"],
                date_to=filters.get("date_to") or parsed["date_to"], media_type=filters.get("media_type") or parsed["media_type"])
    month = parsed["month"]
    if not free:          # only names / dates: an ordinary search does it
        res = search(conn, (month and _month_word(month)) or None, **base, limit=limit)
        return {**res, "mode": "filters", "people": people}
    candidates = {r["photo_id"]: r for r in search(conn, (month and _month_word(month)) or None, **base,
                                                    limit=1_000_000)["items"]}
    keyword = {r["photo_id"] for r in search(conn, " ".join([free] + ([_month_word(month)] if month else [])),
                                             **base, limit=5000)["items"]}
    scored = index.scores(embed_text(free), only=set(candidates))
    if not scored:
        res = search(conn, q, **filters, limit=limit)
        return {**res, "mode": "keywords", "people": people}
    pids = [p for p, _ in scored]
    cos = np.array([s for _, s in scored], np.float32)
    prob = probability(cos)
    ranked = sorted(((cos[i] + (KEYWORD_BOOST if pids[i] in keyword else 0.0), prob[i], pids[i])
                     for i in range(len(pids))), reverse=True)
    good = [r for r in ranked if r[1] >= MIN_PROBABILITY or r[2] in keyword]
    weak = not good
    chosen = (good or ranked[:WEAK_RESULTS])[:limit]
    # keyword matches without a vector yet (not analysed) go after the ranked ones
    seen = {p for _, _, p in chosen}
    extra = [p for p in keyword if p not in seen and p in candidates][:max(limit - len(chosen), 0)]
    items = [{**candidates[p], "score": round(float(c), 4)} for c, _, p in chosen] + [candidates[p] for p in extra]
    return {"items": items, "next": None, "query": parsed, "mode": "smart", "people": people,
            "free_text": free, "weak": weak}


def _month_word(month: str) -> str:
    return ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
            "november", "december"][int(month) - 1]


__all__ = ["smart_search", "names_in", "tokens"]
