from unittest.mock import patch

import pytest

from catcam.camera import (
    CameraBusyError,
    CameraError,
    CameraLock,
    CsiCameraBackend,
    UsbCameraBackend,
    _split_mjpeg_frames,
    create_camera,
)
from catcam.config import CameraConfig


def _camera_config(camera_type: str) -> CameraConfig:
    return CameraConfig(type=camera_type, device="/dev/video0", resolution=[640, 480], framerate=15)


def test_create_camera_selects_csi_backend():
    backend = create_camera(_camera_config("csi"))
    assert isinstance(backend, CsiCameraBackend)


def test_create_camera_selects_usb_backend():
    backend = create_camera(_camera_config("usb"))
    assert isinstance(backend, UsbCameraBackend)


def test_create_camera_unknown_type_raises():
    # config.py's own validation rejects this at load time; create_camera
    # defends independently against a raw/unvalidated CameraConfig instance.
    with pytest.raises(CameraError):
        create_camera(_camera_config("thermal"))


def test_camera_lock_contention(tmp_path):
    lock_path = tmp_path / "camera.lock"
    first = CameraLock(str(lock_path))
    second = CameraLock(str(lock_path))

    first.acquire()
    try:
        with pytest.raises(CameraBusyError):
            second.acquire()
    finally:
        first.release()

    # Once released, a fresh acquire on the same path succeeds again.
    third = CameraLock(str(lock_path))
    third.acquire()
    third.release()


def test_camera_lock_reacquire_is_idempotent(tmp_path):
    lock_path = tmp_path / "camera.lock"
    lock = CameraLock(str(lock_path))
    lock.acquire()
    lock.acquire()  # no-op, must not raise or deadlock
    lock.release()


def test_constructing_backend_does_not_touch_default_lock_path():
    """Regression test: merely constructing a CameraBackend (e.g. for an
    is_available()-only health check, as health.py/scripts/diagnose.sh do)
    must not have any filesystem side effect. CameraLock used to eagerly
    resolve/create the default /run/catcam directory in __init__, so a
    health check run by an unprivileged interactive user could leave that
    directory owned by the wrong user - permanently locking the real
    catcam-publisher.service (which runs as the `catcam` user) out with a
    PermissionError the next time it tried to acquire the lock for real.
    """
    with patch("catcam.camera._default_lock_path") as mock_default_path:
        create_camera(_camera_config("csi")).is_available()
        create_camera(_camera_config("usb")).is_available()
        mock_default_path.assert_not_called()


def test_camera_lock_resolves_default_path_lazily_on_acquire(tmp_path):
    fake_path = tmp_path / "camera.lock"
    with patch("catcam.camera._default_lock_path", return_value=fake_path) as mock_default_path:
        lock = CameraLock()
        mock_default_path.assert_not_called()
        lock.acquire()
        mock_default_path.assert_called_once()
        lock.release()


@pytest.mark.parametrize(
    "buffer,expected_frames,expected_leftover",
    [
        (b"", [], b""),
        (b"garbage-with-no-marker", [], b""),
        (b"\xff\xd8AAA\xff\xd9", [b"\xff\xd8AAA\xff\xd9"], b""),
        (
            b"\xff\xd8AAA\xff\xd9\xff\xd8BBB\xff\xd9",
            [b"\xff\xd8AAA\xff\xd9", b"\xff\xd8BBB\xff\xd9"],
            b"",
        ),
        (
            b"junk\xff\xd8AAA\xff\xd9\xff\xd8partial",
            [b"\xff\xd8AAA\xff\xd9"],
            b"\xff\xd8partial",
        ),
    ],
)
def test_split_mjpeg_frames(buffer, expected_frames, expected_leftover):
    frames, leftover = _split_mjpeg_frames(buffer)
    assert frames == expected_frames
    assert leftover == expected_leftover
