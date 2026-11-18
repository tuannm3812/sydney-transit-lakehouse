from __future__ import annotations

import base64
import hashlib
import io
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from transit_lakehouse.offload import core

NOW = datetime(2026, 11, 20, 5, 30, tzinfo=UTC)


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts = 0
        self.corrupt = False

    def put_object(self, Bucket, Key, Body, ChecksumSHA256, Metadata):  # noqa: N803
        data = Body.read()
        digest = base64.b64encode(hashlib.sha256(data).digest()).decode()
        assert digest == ChecksumSHA256
        self.objects[Key] = b"x" + data if self.corrupt else data
        self.puts += 1

    def head_object(self, Bucket, Key, ChecksumMode):  # noqa: N803
        data = self.objects[Key]  # KeyError plays "404"
        digest = hashlib.sha256(data).digest()
        return {
            "ContentLength": len(data),
            "ChecksumSHA256": base64.b64encode(digest).decode(),
        }


def _land(root: Path, feed: str, date: str, hour: str, n: int = 2):
    d = root / "raw" / f"feed={feed}" / f"date={date}" / f"hour={hour}"
    d.mkdir(parents=True)
    for i in range(n):
        (d / f"snap{i}.pb.gz").write_bytes(f"{feed}{hour}{i}".encode())
    m = root / "manifest" / f"feed={feed}" / f"date={date}"
    m.mkdir(parents=True, exist_ok=True)
    (m / f"hour={hour}.jsonl").write_text('{"outcome": "new"}\n')
    return d


def test_uploads_closed_hours_only_and_once(tmp_path: Path) -> None:
    _land(tmp_path, "buses_vehiclepos", "2026-11-20", "04")
    _land(tmp_path, "buses_vehiclepos", "2026-11-20", "05")  # still open
    s3 = FakeS3()
    res = core.upload(tmp_path, s3, "b", NOW, log=lambda _: None)
    assert res.uploaded == 2 and res.failed == 0
    assert set(s3.objects) == {
        "raw/feed=buses_vehiclepos/date=2026-11-20/hour=04.tar",
        "manifest/feed=buses_vehiclepos/date=2026-11-20/hour=04.jsonl",
    }
    key = "raw/feed=buses_vehiclepos/date=2026-11-20/hour=04.tar"
    with tarfile.open(fileobj=io.BytesIO(s3.objects[key])) as tar:
        assert tar.getnames() == ["snap0.pb.gz", "snap1.pb.gz"]
    again = core.upload(tmp_path, s3, "b", NOW, log=lambda _: None)
    assert again.uploaded == 0 and again.skipped == 2 and s3.puts == 2


def test_tar_is_deterministic(tmp_path: Path) -> None:
    d = _land(tmp_path, "f", "2026-11-20", "01")
    part = core.Partition("raw", "f", "2026-11-20", "01", d)
    a, b = tmp_path / "a.tar", tmp_path / "b.tar"
    core.build_tar(part, a)
    (d / "snap0.pb.gz").touch()  # new mtime, same bytes
    core.build_tar(part, b)
    assert a.read_bytes() == b.read_bytes()


def test_mismatch_fails_without_marker(tmp_path: Path) -> None:
    _land(tmp_path, "f", "2026-11-20", "01")
    s3 = FakeS3()
    s3.corrupt = True
    res = core.upload(tmp_path, s3, "b", NOW, log=lambda _: None)
    assert res.failed == 2 and res.uploaded == 0
    assert not (tmp_path / core.STATE_DIR / "raw").exists()


def test_prune_deletes_only_verified_old_raw(tmp_path: Path) -> None:
    old = _land(tmp_path, "f", "2026-11-16", "01")
    recent = _land(tmp_path, "f", "2026-11-19", "23")
    s3 = FakeS3()
    core.upload(tmp_path, s3, "b", NOW, log=lambda _: None)
    keep = datetime(2026, 11, 20, tzinfo=UTC) - datetime(
        2026, 11, 17, tzinfo=UTC
    )
    res = core.prune(tmp_path, s3, "b", NOW, keep, log=lambda _: None)
    assert res.uploaded == 1
    assert not old.exists() and recent.exists()
    manifests = list((tmp_path / "manifest").rglob("*.jsonl"))
    assert len(manifests) == 2  # manifests are never pruned


def test_prune_keeps_hour_when_s3_copy_missing(tmp_path: Path) -> None:
    old = _land(tmp_path, "f", "2026-11-16", "01")
    s3 = FakeS3()
    core.upload(tmp_path, s3, "b", NOW, log=lambda _: None)
    s3.objects.clear()
    keep = NOW - datetime(2026, 11, 18, tzinfo=UTC)
    res = core.prune(tmp_path, s3, "b", NOW, keep, log=lambda _: None)
    assert res.failed == 1 and old.exists()


def test_prune_keeps_hour_with_late_file(tmp_path: Path) -> None:
    old = _land(tmp_path, "f", "2026-11-16", "01")
    s3 = FakeS3()
    core.upload(tmp_path, s3, "b", NOW, log=lambda _: None)
    (old / "late.pb.gz").write_bytes(b"late")
    keep = NOW - datetime(2026, 11, 18, tzinfo=UTC)
    res = core.prune(tmp_path, s3, "b", NOW, keep, log=lambda _: None)
    assert res.failed == 1 and (old / "late.pb.gz").exists()


def test_round_trip_against_moto(tmp_path: Path, monkeypatch) -> None:
    """The real boto3 calls, against an in-process S3."""
    import pytest

    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    old = _land(tmp_path, "f", "2026-11-16", "01")
    with moto.mock_aws():
        s3 = boto3.client("s3", region_name="ap-southeast-2")
        s3.create_bucket(
            Bucket="raw-bucket",
            CreateBucketConfiguration={"LocationConstraint": "ap-southeast-2"},
        )
        up = core.upload(tmp_path, s3, "raw-bucket", NOW, log=lambda _: None)
        assert up.uploaded == 2 and up.failed == 0
        keep = NOW - datetime(2026, 11, 18, tzinfo=UTC)
        pr = core.prune(
            tmp_path, s3, "raw-bucket", NOW, keep, log=lambda _: None
        )
        assert pr.uploaded == 1 and not old.exists()
