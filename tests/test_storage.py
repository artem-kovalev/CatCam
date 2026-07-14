import os
import time
from pathlib import Path

from catcam.config import RecordingConfig
from catcam.storage import StorageManager


def _config(tmp_path: Path, disk_quota_mb: float = 2048) -> RecordingConfig:
    return RecordingConfig(
        clip_duration_seconds=10,
        pre_roll_seconds=2,
        max_clip_size_mb=50,
        storage_dir=str(tmp_path / "recordings"),
        pending_dir=str(tmp_path / "pending"),
        disk_quota_mb=disk_quota_mb,
    )


def _write_file(path: Path, size_bytes: int, mtime_offset: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size_bytes)
    if mtime_offset:
        now = time.time()
        os.utime(path, (now + mtime_offset, now + mtime_offset))
    return path


def test_tmp_dir_is_sibling_of_storage_dir(tmp_path):
    manager = StorageManager(_config(tmp_path))
    assert manager.tmp_dir() == tmp_path / "tmp"


def test_save_temp_moves_file_and_creates_storage_dir(tmp_path):
    manager = StorageManager(_config(tmp_path))
    src = _write_file(tmp_path / "tmp" / "clip.mp4", 100)

    dest = manager.save_temp(src)

    assert dest == tmp_path / "recordings" / "clip.mp4"
    assert dest.exists()
    assert not src.exists()


def test_mark_delivered_deletes_file(tmp_path):
    manager = StorageManager(_config(tmp_path))
    clip = _write_file(tmp_path / "recordings" / "clip.mp4", 100)

    manager.mark_delivered(clip)

    assert not clip.exists()


def test_mark_failed_moves_file_into_pending(tmp_path):
    manager = StorageManager(_config(tmp_path))
    clip = _write_file(tmp_path / "recordings" / "clip.mp4", 100)

    dest = manager.mark_failed(clip)

    assert dest == tmp_path / "pending" / "clip.mp4"
    assert dest.exists()
    assert not clip.exists()


def test_list_pending_returns_oldest_first(tmp_path):
    manager = StorageManager(_config(tmp_path))
    oldest = _write_file(tmp_path / "pending" / "a.mp4", 10, mtime_offset=-100)
    middle = _write_file(tmp_path / "pending" / "b.mp4", 10, mtime_offset=-50)
    newest = _write_file(tmp_path / "pending" / "c.mp4", 10, mtime_offset=0)

    result = manager.list_pending()

    assert result == [oldest, middle, newest]


def test_list_pending_empty_when_dir_missing(tmp_path):
    manager = StorageManager(_config(tmp_path))
    assert manager.list_pending() == []


def test_enforce_quota_deletes_oldest_pending_first(tmp_path):
    one_mb = 1024 * 1024
    manager = StorageManager(_config(tmp_path, disk_quota_mb=2))
    oldest = _write_file(tmp_path / "pending" / "old.mp4", one_mb, mtime_offset=-100)
    newest = _write_file(tmp_path / "pending" / "new.mp4", one_mb, mtime_offset=0)
    _write_file(tmp_path / "recordings" / "current.mp4", one_mb)

    manager.enforce_quota()

    assert not oldest.exists()
    assert newest.exists()


def test_enforce_quota_noop_under_quota(tmp_path):
    manager = StorageManager(_config(tmp_path, disk_quota_mb=2048))
    clip = _write_file(tmp_path / "pending" / "clip.mp4", 1024)

    manager.enforce_quota()

    assert clip.exists()


def test_mark_delivered_handles_missing_file_gracefully(tmp_path):
    manager = StorageManager(_config(tmp_path))
    missing = tmp_path / "recordings" / "does-not-exist.mp4"

    manager.mark_delivered(missing)  # must not raise


def test_unlink_error_during_quota_enforcement_is_logged_not_raised(tmp_path, monkeypatch):
    one_mb = 1024 * 1024
    manager = StorageManager(_config(tmp_path, disk_quota_mb=1))
    _write_file(tmp_path / "pending" / "a.mp4", one_mb * 2)

    original_unlink = Path.unlink

    def failing_unlink(self, *args, **kwargs):
        if self.name == "a.mp4":
            raise OSError("disk full / read-only filesystem")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    manager.enforce_quota()  # must not raise
