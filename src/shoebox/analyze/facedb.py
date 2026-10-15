"""Faces found on this Mac, grouped into likely-same-person clusters (~/.shoebox/faces.sqlite).

A face is 'new' (in an unnamed cluster), 'auto' (matched to a named person automatically), 'confirmed'
(named by the user, or learnt from Apple Photos) or 'ignored'. Naming a cluster turns its faces into
`media.person.add` ops (source "face"), which is what makes the person searchable everywhere.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

import numpy as np

from .. import paths

AUTO_MATCH = 0.50        # cosine similarity to a named person's face to tag automatically
JOIN_CLUSTER = 0.42      # … to group unnamed faces together (SFace's own same-person threshold is 0.363)
MARGIN = 0.05            # the best person has to beat the runner-up by this much
LIVE = "add_op NOT IN (SELECT add_op FROM tombstones)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyzed (
  photo_id TEXT PRIMARY KEY, version INTEGER NOT NULL, analyzed_at TEXT NOT NULL,
  faces INTEGER NOT NULL DEFAULT 0, labels TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS faces (
  face_id INTEGER PRIMARY KEY AUTOINCREMENT, photo_id TEXT NOT NULL,
  x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL, score REAL NOT NULL,
  emb BLOB NOT NULL, cluster INTEGER, person_id TEXT,
  status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'auto', 'confirmed', 'ignored')));
CREATE INDEX IF NOT EXISTS faces_photo ON faces(photo_id);
CREATE INDEX IF NOT EXISTS faces_cluster ON faces(cluster);
CREATE INDEX IF NOT EXISTS faces_person ON faces(person_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
MAX_ATTEMPTS = 3        # a photo that fails analysis is tried again on later passes, this many times in all


def open_faces(path=None) -> sqlite3.Connection:
    path = path or paths.home() / "faces.sqlite"
    new = not os.path.exists(path)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    if new:
        os.chmod(path, 0o600)        # biometric data: readable by this user only
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    if "attempts" not in {r[1] for r in conn.execute("PRAGMA table_info(analyzed)")}:
        conn.execute("ALTER TABLE analyzed ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1")
    return conn


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _emb(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _next_cluster(fdb: sqlite3.Connection) -> int:
    row = fdb.execute("SELECT value FROM meta WHERE key = 'next_cluster'").fetchone()
    n = int(row[0]) if row else 1
    fdb.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('next_cluster', ?)", (str(n + 1),))
    return n


def add_faces(fdb: sqlite3.Connection, photo_id: str, found) -> list[int]:
    ids = []
    for f in found:
        cur = fdb.execute("INSERT INTO faces (photo_id, x, y, w, h, score, emb) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (photo_id, *f.box, f.score, f.embedding.astype(np.float32).tobytes()))
        ids.append(cur.lastrowid)
    return ids


# ---------------------------------------------------------------- matching

class Known:
    """Fingerprints of faces with a name (confirmed or auto), by surviving person id."""

    def __init__(self, fdb: sqlite3.Connection, catalog: sqlite3.Connection):
        from ..catalog.store import resolve_person

        rows = fdb.execute("SELECT person_id, emb FROM faces WHERE status IN ('confirmed', 'auto') "
                           "AND person_id IS NOT NULL").fetchall()
        self.people = [resolve_person(catalog, r["person_id"]) for r in rows]
        self.matrix = np.stack([_emb(r["emb"]) for r in rows]) if rows else np.zeros((0, 128), np.float32)

    def best(self, emb: np.ndarray) -> tuple[str | None, float]:
        if not len(self.people):
            return None, 0.0
        sims = self.matrix @ emb
        by_person: dict[str, float] = {}
        for p, s in zip(self.people, sims):
            by_person[p] = max(by_person.get(p, -1.0), float(s))
        ranked = sorted(by_person.items(), key=lambda kv: -kv[1])
        top, score = ranked[0]
        if len(ranked) > 1 and score - ranked[1][1] < MARGIN:
            return None, score           # too close between two people: let the user decide
        return top, score

    def add(self, person: str, emb: np.ndarray) -> None:
        self.people.append(person)
        self.matrix = np.vstack([self.matrix, emb[None, :]])


def _photo_has_person(catalog, photo_id: str, person: str) -> bool:
    from ..catalog.store import resolve_person

    rows = catalog.execute(f"SELECT person_id FROM media_people WHERE photo_id = ? AND {LIVE}", (photo_id,))
    return any(resolve_person(catalog, r[0]) == person for r in rows)


def tag_ops(catalog, pairs: list[tuple[str, str]]) -> list[tuple[str, dict]]:
    """media.person.add ops (source face) for (photo, person) pairs that aren't on the photo yet."""
    seen, out = set(), []
    for photo_id, person in pairs:
        if (photo_id, person) in seen or _photo_has_person(catalog, photo_id, person):
            continue
        seen.add((photo_id, person))
        out.append(("media.person.add", {"photo_id": photo_id, "person_id": person, "source": "face"}))
    return out


def learn_from_photo_names(fdb, catalog, photo_id: str) -> bool:
    """One face and one named person on the photo (e.g. from Apple Photos): that's who it is."""
    from ..catalog.store import UNNAMED_PERSON, resolve_person

    faces = fdb.execute("SELECT face_id FROM faces WHERE photo_id = ? AND status != 'ignored'", (photo_id,)).fetchall()
    people = {resolve_person(catalog, r[0]) for r in catalog.execute(
        f"SELECT m.person_id FROM media_people m JOIN people p USING (person_id) WHERE m.photo_id = ? AND m.{LIVE} "
        "AND p.name IS NOT NULL AND p.name != ?", (photo_id, UNNAMED_PERSON))}
    if len(faces) == 1 and len(people) == 1:
        fdb.execute("UPDATE faces SET status = 'confirmed', person_id = ?, cluster = NULL WHERE face_id = ?",
                    (people.pop(), faces[0]["face_id"]))
        return True
    return False


def assign(fdb, catalog, face_ids: list[int] | None = None) -> list[tuple[str, dict]]:
    """Match 'new' faces to named people (→ 'auto') or group them into clusters. Returns ops to emit."""
    known = Known(fdb, catalog)
    q = "SELECT face_id, photo_id, emb, cluster FROM faces WHERE status = 'new'"
    rows = fdb.execute(q + (f" AND face_id IN ({','.join('?' * len(face_ids))})" if face_ids else ""),
                       face_ids or []).fetchall()
    clustered = fdb.execute("SELECT face_id, cluster, emb FROM faces WHERE status = 'new' AND cluster IS NOT NULL").fetchall()
    c_ids = [r["cluster"] for r in clustered]
    c_face = [r["face_id"] for r in clustered]
    c_mat = np.stack([_emb(r["emb"]) for r in clustered]) if clustered else np.zeros((0, 128), np.float32)
    pairs = []
    for r in rows:
        emb = _emb(r["emb"])
        person, score = known.best(emb)
        if person and score >= AUTO_MATCH:
            fdb.execute("UPDATE faces SET status = 'auto', person_id = ?, cluster = NULL WHERE face_id = ?",
                        (person, r["face_id"]))
            pairs.append((r["photo_id"], person))
            continue
        if r["cluster"] is not None:
            continue
        cluster = None
        if len(c_ids):
            sims = c_mat @ emb
            for i in np.argsort(-sims):
                if c_face[i] == r["face_id"]:
                    continue
                if sims[i] >= JOIN_CLUSTER:
                    cluster = c_ids[i]
                break
        cluster = cluster or _next_cluster(fdb)
        fdb.execute("UPDATE faces SET cluster = ? WHERE face_id = ?", (cluster, r["face_id"]))
        c_ids.append(cluster)
        c_face.append(r["face_id"])
        c_mat = np.vstack([c_mat, emb[None, :]])
    fdb.commit()
    return tag_ops(catalog, pairs)


# ---------------------------------------------------------------- naming

def person_for_name(catalog, name: str) -> tuple[str, list[tuple[str, dict]]]:
    """An existing person with this name (case-insensitive), or a new one."""
    from ..catalog.store import person_id, resolve_person

    name = " ".join(name.split())
    row = catalog.execute("SELECT person_id FROM people WHERE lower(name) = lower(?) AND merged_into IS NULL "
                          "ORDER BY person_id LIMIT 1", (name,)).fetchone()
    if row:
        return resolve_person(catalog, row[0]), []
    pid = person_id(name)
    if catalog.execute("SELECT 1 FROM people WHERE person_id = ?", (pid,)).fetchone():
        return resolve_person(catalog, pid), [("person.set", {"person_id": pid, "field": "name", "value": name})]
    return pid, [("person.create", {"person_id": pid, "name": name})]


def name_faces(fdb, catalog, face_ids: list[int], name: str) -> tuple[str, list[tuple[str, dict]]]:
    """These faces are `name`. Returns (person_id, ops); other unnamed faces are then re-matched."""
    person, ops = person_for_name(catalog, name)
    if not face_ids:
        return person, ops
    marks = ",".join("?" * len(face_ids))
    fdb.execute(f"UPDATE faces SET status = 'confirmed', person_id = ?, cluster = NULL WHERE face_id IN ({marks})",
                (person, *face_ids))
    photos = [r[0] for r in fdb.execute(f"SELECT DISTINCT photo_id FROM faces WHERE face_id IN ({marks})", face_ids)]
    fdb.commit()
    return person, ops + tag_ops(catalog, [(p, person) for p in photos])


def rematch(fdb, catalog) -> list[tuple[str, dict]]:
    """After naming someone, unnamed faces that look like them are tagged too."""
    known = Known(fdb, catalog)
    pairs = []
    for r in fdb.execute("SELECT face_id, photo_id, emb FROM faces WHERE status = 'new'").fetchall():
        person, score = known.best(_emb(r["emb"]))
        if person and score >= AUTO_MATCH:
            fdb.execute("UPDATE faces SET status = 'auto', person_id = ?, cluster = NULL WHERE face_id = ?",
                        (person, r["face_id"]))
            pairs.append((r["photo_id"], person))
    fdb.commit()
    return tag_ops(catalog, pairs)


def ignore_faces(fdb, face_ids: list[int]) -> None:
    fdb.execute(f"UPDATE faces SET status = 'ignored', cluster = NULL WHERE face_id IN ({','.join('?' * len(face_ids))})",
                face_ids)
    fdb.commit()


def unname_face(fdb, catalog, face_id: int) -> list[tuple[str, dict]]:
    """'That's not them': the face goes back to unnamed; the automatic tag is removed from its photo."""
    r = fdb.execute("SELECT photo_id, person_id, status FROM faces WHERE face_id = ?", (face_id,)).fetchone()
    if r is None or r["person_id"] is None:
        return []
    fdb.execute("UPDATE faces SET status = 'new', person_id = NULL, cluster = ? WHERE face_id = ?",
                (_next_cluster(fdb), face_id))
    fdb.commit()
    adds = [a for (a,) in catalog.execute(f"SELECT add_op FROM media_people WHERE photo_id = ? AND person_id = ? "
                                          f"AND source = 'face' AND {LIVE}", (r["photo_id"], r["person_id"]))]
    return [("media.person.remove", {"photo_id": r["photo_id"], "person_id": r["person_id"],
                                     "observed_add_ops": adds})] if adds else []


def clusters(fdb, *, limit: int = 50, min_size: int = 1) -> list[dict]:
    """Unnamed groups of faces, biggest first."""
    rows = fdb.execute("SELECT cluster, COUNT(*) AS n, COUNT(DISTINCT photo_id) AS photos FROM faces "
                       "WHERE status = 'new' AND cluster IS NOT NULL GROUP BY cluster HAVING n >= ? "
                       "ORDER BY n DESC, cluster LIMIT ?", (min_size, limit)).fetchall()
    out = []
    for r in rows:
        faces = [f[0] for f in fdb.execute("SELECT face_id FROM faces WHERE cluster = ? AND status = 'new' "
                                           "ORDER BY score DESC LIMIT 8", (r["cluster"],))]
        out.append({"cluster": r["cluster"], "faces": r["n"], "photos": r["photos"], "sample": faces})
    return out
