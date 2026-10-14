"""Running in the cloud: the SQLite database and your private files live in a Cloud Storage bucket.

A Cloud Run job has no disk that outlives it. With AINEWS_STATE_BUCKET set (the deployed jobs set it),
every command first downloads the database and the private files, works on the local copy, and uploads
the database when it ends, only if no other run uploaded one in between (a generation precondition), so
two runs can never overwrite each other's work. The bucket keeps earlier versions for 30 days.

Locally, `ainews state pull` fetches the cloud copy to look at, and `ainews state push` sends yours up
(the first time, and after you edit your private files)."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

DB_OBJECT = "ainews.sqlite"
PRIVATE_PREFIX = "private/"


class StateConflict(RuntimeError):
    """Another run uploaded the database since this one downloaded it."""


def bucket_for(settings, name: str | None = None):
    from google.cloud import storage

    name = (name or settings.state_bucket).removeprefix("gs://")
    return storage.Client(project=settings.gcp_project_id or None).bucket(name)


def private_files(settings) -> dict[str, Path]:
    """The private files the app reads, by their name in the bucket."""
    return {"profile.md": settings.profile_path, "skills.yaml": settings.fit_path, "openings.yaml": settings.openings_path}


def copy_db(src_path: Path, dst_path: Path) -> None:
    """A consistent copy of a live SQLite database, its WAL folded in: one self-contained file."""
    src, dst = sqlite3.connect(src_path), sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def pull(settings, bucket, *, private: bool = True) -> int:
    """Downloads the database (and the private files); returns its generation, 0 when there is none yet.
    Run it before anything opens the local database: one command per process, as the deployed jobs do."""
    generation = 0
    blob = bucket.get_blob(DB_OBJECT)
    if blob is not None:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        for leftover in (Path(f"{settings.db_path}-wal"), Path(f"{settings.db_path}-shm")):
            leftover.unlink(missing_ok=True)   # from another database; SQLite would replay it into this one
        blob.download_to_filename(str(settings.db_path), if_generation_match=blob.generation)
        generation = blob.generation
    if private:
        for name, path in private_files(settings).items():
            pblob = bucket.get_blob(PRIVATE_PREFIX + name)
            if pblob is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                pblob.download_to_filename(str(path))
    return generation


def push(settings, bucket, generation: int) -> int:
    """Uploads a consistent copy of the database if the bucket still holds `generation` (0: none yet);
    returns the new generation. Raises StateConflict when another run got there first."""
    from google.api_core.exceptions import PreconditionFailed

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / DB_OBJECT
        copy_db(settings.db_path, copy)
        blob = bucket.blob(DB_OBJECT)
        try:
            blob.upload_from_filename(str(copy), if_generation_match=generation)
        except PreconditionFailed as exc:
            raise StateConflict(f"another run uploaded gs://{bucket.name}/{DB_OBJECT} since this one started;"
                                " this run's changes were not saved") from exc
    return blob.generation


def push_private(settings, bucket) -> list[str]:
    """Uploads the private files you have; returns their names."""
    sent = []
    for name, path in private_files(settings).items():
        if path.exists():
            bucket.blob(PRIVATE_PREFIX + name).upload_from_filename(str(path))
            sent.append(name)
    return sent


def current_generation(bucket) -> int:
    blob = bucket.get_blob(DB_OBJECT)
    return blob.generation if blob is not None else 0
