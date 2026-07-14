import io
import json
import urllib.error
from unittest.mock import patch

from catcam.stream_health import check_mediamtx_path


def _fake_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _Resp(body)


@patch("catcam.stream_health.urllib.request.urlopen")
def test_path_ready(mock_urlopen):
    mock_urlopen.return_value = _fake_response({"name": "cam", "ready": True})

    health = check_mediamtx_path("http://127.0.0.1:9997", "cam")

    assert health.api_reachable is True
    assert health.path_ready is True
    assert health.error is None


@patch("catcam.stream_health.urllib.request.urlopen")
def test_path_not_ready(mock_urlopen):
    mock_urlopen.return_value = _fake_response({"name": "cam", "ready": False})

    health = check_mediamtx_path("http://127.0.0.1:9997", "cam")

    assert health.api_reachable is True
    assert health.path_ready is False


@patch("catcam.stream_health.urllib.request.urlopen")
def test_api_unreachable(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

    health = check_mediamtx_path("http://127.0.0.1:9997", "cam")

    assert health.api_reachable is False
    assert health.path_ready is False
    assert health.error is not None


@patch("catcam.stream_health.urllib.request.urlopen")
def test_malformed_response(mock_urlopen):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    mock_urlopen.return_value = _Resp(b"not json")

    health = check_mediamtx_path("http://127.0.0.1:9997", "cam")

    assert health.api_reachable is True
    assert health.path_ready is False
    assert health.error is not None
