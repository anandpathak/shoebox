"""Place names from GPS coordinates, offline: the nearest town, its region and country.

Apple Photos names places for photos it manages; everything else (phone uploads, backups, photos Apple
hasn't named) gets a name from GeoNames (geonames.org, CC BY 4.0): ~170,000 towns of 1,000+ people,
downloaded once (~11 MB) into ~/.shoebox/models/geonames. Lookups never leave the Mac.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import paths

BASE = "https://download.geonames.org/export/dump/"
MAX_KM = 150             # farther than this from any town (at sea, wilderness): no name
log = logging.getLogger(__name__)


@dataclass
class Index:
    xyz: np.ndarray              # (N, 3) unit vectors, float32
    names: list[str]
    regions: list[str]
    countries: list[str]
    population: np.ndarray | None = None

    @staticmethod
    def build(rows: list[tuple]) -> "Index":
        """rows: (lat, lon, town, region, country[, population])."""
        lat = np.radians(np.array([r[0] for r in rows], np.float64))
        lon = np.radians(np.array([r[1] for r in rows], np.float64))
        xyz = np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], 1).astype(np.float32)
        pop = np.array([(r[5] if len(r) > 5 else 0) or 0 for r in rows], np.float32)
        return Index(xyz, [r[2] for r in rows], [r[3] for r in rows], [r[4] for r in rows], pop)

    @staticmethod
    def extent_km(population: float) -> float:
        """Roughly how far a place reaches from its centre: ~2 km for a town, ~20 km for a megacity."""
        return 1.5 * (max(population, 1.0) / 1e4) ** 0.4

    def locate(self, lat: float, lon: float) -> tuple[int, int, float]:
        """(nearest place, the biggest place whose extent covers the point, km to the nearest)."""
        la, lo = math.radians(lat), math.radians(lon)
        q = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)], np.float64)
        dots = self.xyz @ q.astype(np.float32)
        near = np.argpartition(-dots, 60)[:60] if len(dots) > 60 else np.arange(len(dots))
        best, best_score, best_km, city, city_pop = int(near[0]), float("inf"), 0.0, -1, -1.0
        for i in near:
            chord = float(np.linalg.norm(self.xyz[i].astype(np.float64) - q))
            km = 2 * 6371.0 * math.asin(min(1.0, chord / 2))      # precise for short distances too
            pop = float(self.population[i]) if self.population is not None else 0.0
            score = max(0.0, km - self.extent_km(pop)) + km * 1e-3   # inside a place counts as being there
            if score < best_score:
                best, best_score, best_km = int(i), score, km
            if km <= self.extent_km(pop) and pop > city_pop:
                city, city_pop = int(i), pop
        return best, (city if city >= 0 else best), best_km

    def nearest(self, lat: float, lon: float) -> tuple[int, float]:
        la, lo = math.radians(lat), math.radians(lon)
        q = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)], np.float32)
        dots = self.xyz @ q
        near = np.argpartition(-dots, min(30, len(dots) - 1))[:30] if len(dots) > 30 else np.arange(len(dots))
        best, best_score, best_km = int(near[0]), float("inf"), 0.0
        for i in near:
            chord = float(np.linalg.norm(self.xyz[i].astype(np.float64) - q.astype(np.float64)))
            km = 2 * 6371.0 * math.asin(min(1.0, chord / 2))      # precise for short distances too
            pop = float(self.population[i]) if self.population is not None else 0.0
            score = max(0.0, km - self.extent_km(pop)) + km * 1e-3   # inside a city counts as being there
            if score < best_score:
                best, best_score, best_km = int(i), score, km
        return best, best_km


def _folder() -> Path:
    d = paths.home() / "models" / "geonames"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fetch(name: str) -> bytes:
    with urllib.request.urlopen(BASE + name, timeout=120) as r:
        return r.read()


def _download_and_build(folder: Path) -> Path:
    countries = {}
    for line in _fetch("countryInfo.txt").decode().splitlines():
        if line and not line.startswith("#"):
            f = line.split("\t")
            countries[f[0]] = f[4]
    regions = {}
    for line in _fetch("admin1CodesASCII.txt").decode().splitlines():
        f = line.split("\t")
        if len(f) > 1:
            regions[f[0]] = f[1]
    rows = []
    with zipfile.ZipFile(io.BytesIO(_fetch("cities1000.zip"))) as z:
        for line in z.read("cities1000.txt").decode().splitlines():
            f = line.split("\t")
            if len(f) > 10 and f[7] != "PPLX":     # skip neighbourhoods: "San Francisco", not "Mission District"
                rows.append((float(f[4]), float(f[5]), f[1], regions.get(f"{f[8]}.{f[10]}", ""), countries.get(f[8], f[8]),
                             int(f[14] or 0)))
    idx = Index.build(rows)
    np.save(folder / "xyz.npy", idx.xyz)
    np.save(folder / "population.npy", idx.population)
    tmp = folder / "names.json.tmp"
    tmp.write_text(json.dumps({"names": idx.names, "regions": idx.regions, "countries": idx.countries,
                               "attribution": "GeoNames (geonames.org), CC BY 4.0"}))
    os.replace(tmp, folder / "names.json")
    return folder


_index: Index | None = None
_lock = threading.Lock()
_failed = False


def use_index(index: Index | None) -> None:
    """Tests: use this index instead of the downloaded one."""
    global _index, _failed
    _index, _failed = index, False


def index() -> Index | None:
    """The place index, downloading it on first use. None when it can't be had (offline): try again later."""
    global _index, _failed
    if _index is not None:
        return _index
    if os.environ.get("SHOEBOX_PLACES") == "0":
        return None
    with _lock:
        if _index is not None or _failed:
            return _index
        folder = _folder()
        try:
            if not (folder / "names.json").exists():
                _download_and_build(folder)
            names = json.loads((folder / "names.json").read_text())
            pop = np.load(folder / "population.npy") if (folder / "population.npy").exists() else None
            _index = Index(np.load(folder / "xyz.npy"), names["names"], names["regions"], names["countries"], pop)
        except Exception as e:  # noqa: BLE001 — no network yet: photos keep their coordinates, names come later
            log.warning("place names unavailable: %s", e)
            _failed = True
        return _index


def lookup(lat: float | None, lon: float | None) -> dict | None:
    """{"name": "Gokarna, Karnataka", "address": {...}, "source": "geonames"} or None."""
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    idx = index()
    if idx is None:
        return None
    i, c, km = idx.locate(lat, lon)
    if km > MAX_KM:
        return None
    local, city, region, country = idx.names[i], idx.names[c], idx.regions[c] or idx.regions[i], idx.countries[c]
    if local == city:
        name, sub = ", ".join(x for x in (city, region) if x), None
    else:                                        # "City of Westminster, London": district within a big city
        name, sub = f"{local}, {city}", local
    return {"name": name, "source": "geonames", "distance_km": round(km, 1),
            "address": {"city": city, "sub_locality": sub, "state_province": region or None, "country": country}}


def fill_place(record: dict) -> bool:
    """Give a record a place name from its coordinates if it has none. True if it changed."""
    apple = record.setdefault("apple", {}) if isinstance(record.get("apple"), dict) or "apple" not in record else None
    if apple is None:
        return False
    place = apple.get("place") or {}
    has_name = place.get("name") or (place.get("address") or {}).get("city") or (place.get("address") or {}).get("country")
    if has_name:
        return False
    found = lookup(apple.get("latitude"), apple.get("longitude"))
    if not found:
        return False
    apple["place"] = found
    return True


def backfill(conn, library: Path, device_id: str, *, bucket=None, limit: int | None = None, stop=None,
             on_item=None) -> dict:
    """Name the places of items that have coordinates but no place: update the sidecar (and its copy in S3
    when a bucket is given) and the catalog (media.upsert, which only fills fields it has)."""
    from .catalog.ops import Emitter

    if index() is None:
        return {"named": 0, "skipped": 0, "unavailable": True}
    rows = conn.execute("SELECT photo_id, meta_key, lat, lon FROM media WHERE deleted = 0 AND lat IS NOT NULL "
                        "AND place_city IS NULL AND place_country IS NULL AND place_name IS NULL").fetchall()[:limit]
    emitter, counts = Emitter(conn, device_id), {"named": 0, "skipped": 0}
    for r in rows:
        if stop is not None and stop.is_set():
            break
        meta = Path(library) / r["meta_key"]
        try:
            record = json.loads(meta.read_text())
        except (FileNotFoundError, ValueError):
            counts["skipped"] += 1            # archived on another Mac: that Mac names it
            continue
        apple = record.setdefault("apple", {})
        apple.setdefault("latitude", r["lat"])
        apple.setdefault("longitude", r["lon"])
        if not fill_place(record):
            counts["skipped"] += 1
            continue
        text = json.dumps(record, indent=2, ensure_ascii=False)
        tmp = meta.with_suffix(".json.tmp")
        tmp.write_text(text)
        os.replace(tmp, meta)
        if bucket is not None:
            bucket.put_bytes(r["meta_key"], text.encode(), content_type="application/json")
        emitter.emit("media.upsert", {"record": record})
        counts["named"] += 1
        if on_item:
            on_item(r["photo_id"])
    return counts
