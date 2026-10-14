import gc
from argparse import Namespace
from dataclasses import replace

import pytest
from google.api_core.exceptions import PreconditionFailed

from ainews import cli, db
from ainews.cloud import StateConflict, pull, push, push_private


class FakeBlob:
    def __init__(self, store, name):
        self.store, self.name = store, name

    @property
    def generation(self):
        return self.store.objects.get(self.name, (None, 0))[1]

    def download_to_filename(self, path, if_generation_match=None):
        data, gen = self.store.objects[self.name]
        assert if_generation_match in (None, gen)
        with open(path, "wb") as f:
            f.write(data)

    def upload_from_filename(self, path, if_generation_match=None):
        if if_generation_match is not None and if_generation_match != self.generation:
            raise PreconditionFailed("generation changed")
        with open(path, "rb") as f:
            self.store.counter += 1
            self.store.objects[self.name] = (f.read(), self.store.counter)


class FakeBucket:
    name = "test-bucket"

    def __init__(self):
        self.objects, self.counter = {}, 100

    def blob(self, name):
        return FakeBlob(self, name)

    def get_blob(self, name):
        return FakeBlob(self, name) if name in self.objects else None


def _settings(settings, tmp_path):
    return replace(settings, profile_path=tmp_path / "private" / "profile.md", fit_path=tmp_path / "private" / "skills.yaml",
                   openings_path=tmp_path / "private" / "openings.yaml")


def test_push_then_pull_round_trip_with_private_files(settings, tmp_path):
    s, bucket = _settings(settings, tmp_path), FakeBucket()
    conn = db.connect(s.db_path)
    conn.execute("INSERT INTO meta VALUES ('note', 'from this run')")
    conn.commit()                                       # still in the WAL file, not the main one
    s.fit_path.parent.mkdir(parents=True)
    s.fit_path.write_text("have: [Python]\n", encoding="utf-8")
    assert push_private(s, bucket) == ["skills.yaml"]
    gen = push(s, bucket, 0)
    assert gen == bucket.objects["ainews.sqlite"][1]

    elsewhere = replace(s, db_path=tmp_path / "cloud" / "ainews.sqlite", fit_path=tmp_path / "cloud" / "skills.yaml")
    assert pull(elsewhere, bucket) == gen
    assert db.connect(elsewhere.db_path).execute("SELECT value FROM meta WHERE key = 'note'").fetchone()[0] == "from this run"
    assert elsewhere.fit_path.read_text(encoding="utf-8") == "have: [Python]\n"


def test_push_refuses_when_another_run_saved_first(settings, tmp_path):
    s, bucket = _settings(settings, tmp_path), FakeBucket()
    db.connect(s.db_path)
    first = push(s, bucket, 0)
    push(s, bucket, first)                              # a newer run saved its copy
    with pytest.raises(StateConflict, match="not saved"):
        push(s, bucket, first)                          # this run started from the older one
    with pytest.raises(StateConflict):
        push(s, bucket, 0)                              # "none yet" no longer holds either


def test_commands_sync_with_the_bucket_when_deployed(settings, tmp_path, monkeypatch):
    bucket = FakeBucket()
    monkeypatch.setattr("ainews.cloud.bucket_for", lambda settings, name=None: bucket)
    monkeypatch.setenv("AINEWS_STATE_BUCKET", "gs://test-bucket")
    monkeypatch.setenv("AINEWS_DB", str(tmp_path / "run" / "ainews.sqlite"))
    monkeypatch.setenv("AINEWS_LLM_ENABLED", "false")
    cli.main(["budget"])                                # nothing in the bucket yet: starts empty, saves the result
    assert bucket.objects["ainews.sqlite"][1] == 101
    gc.collect()                                        # a new process in the cloud: no connection left open
    cli.main(["budget"])
    assert bucket.objects["ainews.sqlite"][1] == 102    # the next run started from it and saved again
    cli.main(["sources"])                               # never touches the database
    assert bucket.objects["ainews.sqlite"][1] == 102


def test_state_push_wont_replace_the_cloud_copy_by_accident(settings, tmp_path, monkeypatch):
    bucket = FakeBucket()
    monkeypatch.setattr("ainews.cloud.bucket_for", lambda settings, name=None: bucket)
    db.connect(settings.db_path)
    args = Namespace(action="push", private=False, force=False, bucket="b")
    cli.cmd_state(args, settings)
    with pytest.raises(SystemExit, match="already holds a database"):
        cli.cmd_state(args, settings)
    cli.cmd_state(Namespace(action="push", private=False, force=True, bucket="b"), settings)
    assert bucket.objects["ainews.sqlite"][1] == 102


def test_state_pull_keeps_the_local_database_whole(settings, tmp_path, monkeypatch):
    bucket = FakeBucket()
    monkeypatch.setattr("ainews.cloud.bucket_for", lambda settings, name=None: bucket)
    conn = db.connect(settings.db_path)
    push(settings, bucket, 0)                            # the cloud copy, without the row below
    conn.execute("INSERT INTO meta VALUES ('note', 'only local')")
    conn.commit()                                       # sits in the WAL file
    conn.close()
    cli.cmd_state(Namespace(action="pull", private=False, force=False, bucket="b"), settings)
    kept = next(settings.db_path.parent.glob("ainews.local-*.sqlite"))
    assert db.connect(kept).execute("SELECT value FROM meta WHERE key = 'note'").fetchone()[0] == "only local"
    assert db.connect(settings.db_path).execute("SELECT value FROM meta WHERE key = 'note'").fetchone() is None
