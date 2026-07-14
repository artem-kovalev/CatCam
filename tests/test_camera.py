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
