"""Which labels become searchable tags, plus everyday words Vision doesn't use (landscape, people…)."""

from __future__ import annotations

MIN_CONFIDENCE = 0.3
MAX_TAGS = 12
# Too vague to be worth a tag on their own.
SKIP = {"structure", "material", "textile", "liquid", "water_body", "conveyance", "consumable", "art", "games",
        "machine", "equipment", "tool", "container", "decoration", "office_supplies", "people"}
# Everyday words → the Vision labels that imply them.
DERIVED = {
    "landscape": {"mountain", "hill", "canyon", "cliff", "desert", "land", "grass", "forest", "rice_field", "farm",
                  "lake", "waterfall", "shore", "volcano", "glacier", "sky", "sunset_sunrise", "wetland", "island"},
    "nature": {"mountain", "forest", "tree", "flower", "plant", "waterfall", "river", "lake", "grass", "garden",
               "wetland"},
    "sunset": {"sunset_sunrise"},
    "night": {"night_sky"},
    "city": {"cityscape", "skyscraper", "street", "building"},
    "food": {"food", "dessert", "baked_goods", "fruit", "vegetable", "drink", "seafood", "plate"},
    "pet": {"dog", "cat"},
    "animal": {"animal", "mammal", "bird", "dog", "cat", "insect", "reptile"},
    "water": {"ocean", "lake", "river", "waterfall", "pool", "shore", "beach"},
    "document": {"document", "receipt", "newspaper"},
}


def friendly(identifier: str) -> str:
    return identifier.replace("_", " ").strip()


def pick(labels: list[tuple[str, float]], *, faces: int = 0, face_area: float = 0.0,
         min_confidence: float = MIN_CONFIDENCE) -> list[str]:
    """Tags for a photo from Vision's labels and the faces found in it (all lower case, no duplicates)."""
    strong = [l for l, c in labels if c >= min_confidence and l not in SKIP][:MAX_TAGS]
    out = [friendly(l) for l in strong]
    present = set(strong)
    for word, sources in DERIVED.items():
        if present & sources:
            if word == "landscape" and (faces >= 2 or face_area > 0.01):
                continue                      # someone clearly in the picture: a people photo, not scenery
            out.append(word)
    if faces:
        out.append("people")
        if faces == 1 and face_area > 0.04:
            out.append("portrait")
        if faces >= 3:
            out.append("group")
    return list(dict.fromkeys(t.lower() for t in out))
