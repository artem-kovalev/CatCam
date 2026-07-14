"""Storage management for recorded event clips.

Owns three directories driven by `RecordingConfig`:
- `storage_dir` — finished clips awaiting/after delivery.
- `pending_dir` — clips whose delivery failed, kept for retry.
- a `tmp` directory (derived, sibling of `storage_dir` — see `tmp_dir()`) for
  in-progress recordings; `recorder.py` writes here, then hands the finished
  path to `StorageManager.save_temp()`.

All filesystem operations are best-effort: a read-only filesystem or a full
disk is logged and swallowed rather than crashing the calling process, since
a storage hiccup shouldn't take down motion detection or delivery.
"""

import logging
from pathlib import Path
from typing import List

from .config import RecordingConfig

logger = logging.getLogger("catcam.storage")


class StorageManager:
    """Manages the lifecycle of recorded clips on disk."""

    def __init__(self, config: RecordingConfig):
        self._config = config
        self.storage_dir = Path(config.storage_dir)
        self.pending_dir = Path(config.pending_dir)

    def tmp_dir(self) -> Path:
        """Directory for in-progress recordings.

        Not a separate config field — derived as a sibling of `storage_dir`
        (e.g. `storage/recordings` -> `storage/tmp`) so it moves along with
        `storage_dir` without needing its own YAML key.
        """
        return self.storage_dir.parent / "tmp"

    def _ensure_dir(self, path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as exc:
            logger.error("Could not create directory '%s': %s", path, exc)
            return False

    def save_temp(self, src_path: Path) -> Path:
        """Move a finished temp clip into `storage_dir`. Returns the new path.

        On failure, logs and returns `src_path` unchanged (the clip stays in
        `tmp/`, which is safer than losing track of it).
        """
        if not self._ensure_dir(self.storage_dir):
            return src_path
        dest = self.storage_dir / src_path.name
        try:
            src_path.rename(dest)
        except OSError as exc:
            logger.error("Could not move '%s' to '%s': %s", src_path, dest, exc)
            return src_path
        logger.info("Saved clip to '%s'", dest)
        return dest

    def mark_delivered(self, path: Path) -> None:
        """Delete a successfully-delivered clip's file."""
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error("Could not delete delivered clip '%s': %s", path, exc)
            return
        logger.info("Deleted delivered clip '%s'", path)

    def mark_failed(self, path: Path) -> Path:
        """Move a clip whose delivery failed into `pending_dir` for retry.

        Returns the new path, or the original path unchanged if the move
        failed (logged either way).
        """
        if not self._ensure_dir(self.pending_dir):
            return path
        dest = self.pending_dir / path.name
        try:
            if path != dest:
                path.rename(dest)
        except OSError as exc:
            logger.error("Could not move '%s' to pending '%s': %s", path, dest, exc)
            return path
        logger.info("Moved undelivered clip to pending: '%s'", dest)
        return dest

    def list_pending(self) -> List[Path]:
        """Files in `pending_dir`, oldest first, for the retry queue to consume."""
        if not self.pending_dir.is_dir():
            return []
        try:
            files = [p for p in self.pending_dir.iterdir() if p.is_file()]
        except OSError as exc:
            logger.error("Could not list pending directory '%s': %s", self.pending_dir, exc)
            return []
        return sorted(files, key=lambda p: self._safe_mtime(p))

    def _safe_mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _dir_size_bytes(self, directory: Path) -> int:
        if not directory.is_dir():
            return 0
        total = 0
        try:
            for path in directory.rglob("*"):
                if path.is_file():
                    try:
                        total += path.stat().st_size
                    except OSError:
                        continue
        except OSError as exc:
            logger.error("Could not measure size of '%s': %s", directory, exc)
        return total

    def disk_usage_mb(self) -> float:
        """Combined size of `storage_dir` + `pending_dir`, in MB.

        Public accessor (added in task 9) for `/status` (task 8) and
        `health.py`'s aggregated status - same summation `enforce_quota()`
        already uses internally.
        """
        total_bytes = self._dir_size_bytes(self.storage_dir) + self._dir_size_bytes(
            self.pending_dir
        )
        return total_bytes / (1024 * 1024)

    def enforce_quota(self) -> None:
        """Delete oldest pending clips first until under `disk_quota_mb`.

        The quota covers `storage_dir` + `pending_dir` combined (the space
        CatCam itself is responsible for) — not whole-filesystem free space
        (`shutil.disk_usage` measures the filesystem, not these directories,
        so it's the wrong primitive for a directory-scoped quota; deviates
        from task5.md's original Steps text, see task5.md's Result).
        """
        quota_bytes = self._config.disk_quota_mb * 1024 * 1024
        total_bytes = self._dir_size_bytes(self.storage_dir) + self._dir_size_bytes(
            self.pending_dir
        )
        if total_bytes <= quota_bytes:
            return

        pending = self.list_pending()
        for path in pending:
            if total_bytes <= quota_bytes:
                break
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError as exc:
                logger.error("Could not delete '%s' while enforcing quota: %s", path, exc)
                continue
            total_bytes -= size
            logger.warning(
                "Deleted pending clip '%s' (%.1f MB) to stay under disk quota (%d MB)",
                path,
                size / (1024 * 1024),
                self._config.disk_quota_mb,
            )

        if total_bytes > quota_bytes:
            logger.error(
                "Still over disk quota (%d MB) after deleting all pending clips; "
                "%.1f MB used by storage_dir alone",
                self._config.disk_quota_mb,
                total_bytes / (1024 * 1024),
            )
