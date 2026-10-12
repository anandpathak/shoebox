from __future__ import annotations

import copy

import pytest

# Synthetic sidecar (no real personal data) shaped like what `photoctl archive` writes.
SIDECAR = {
    "schema_version": 1,
    "photo_id": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "media_type": "photo",
    "keys": {
        "raw": "raw/2025/2025-08/ABCDEFGHIJKLMNOPQRSTUVWXYZ.heic",
        "compressed": "compressed/2025/2025-08/ABCDEFGHIJKLMNOPQRSTUVWXYZ.jpg",
        "poster": None,
        "thumb": "thumb/2025/2025-08/ABCDEFGHIJKLMNOPQRSTUVWXYZ.jpg",
        "meta": "meta/2025/2025-08/ABCDEFGHIJKLMNOPQRSTUVWXYZ.json",
        "companions": [],
    },
    "file": {"sha256": "aa" * 32, "orig_name": "IMG_0001.HEIC", "orig_ext": "heic", "bytes_raw": 2_000_000,
             "width": 4032, "height": 3024, "uti": "public.heic"},
    "taken_at": "2025-08-15T11:52:05+05:30",
    "source": {"type": "applephotos", "ref": "00000000-1111-2222-3333-444444444444"},
    "apple": {
        "title": "Sunset walk", "description": None, "favorite": True, "hidden": False, "ismovie": False,
        "keywords": ["Holiday"], "labels": ["Beach", "Sea", "Sky"], "albums": ["Goa 2025"],
        "persons": ["Asha", "_UNKNOWN_"],
        "latitude": 14.5, "longitude": 74.3,
        "exif_info": {"camera_make": "Apple", "camera_model": "iPhone 15", "duration": None, "tzname": "GMT+0530"},
        "place": {"name": "Gokarna, Karnataka", "address": {"city": "Gokarna", "state_province": "Karnataka",
                                                            "country": "India", "sub_locality": "Kudle"},
                  "names": {"city": ["Gokarna"]}},
    },
}


@pytest.fixture
def sidecar():
    return copy.deepcopy(SIDECAR)


@pytest.fixture(autouse=True)
def photoctl_home(tmp_path, monkeypatch):
    home = tmp_path / "photoctl-home"
    monkeypatch.setenv("PHOTOCTL_HOME", str(home))
    monkeypatch.setenv("PHOTOCTL_TOKEN_STORE", "file")   # never touch the real Keychain in tests
    monkeypatch.setenv("PHOTOCTL_ANALYSIS", "0")         # no background analysis (or model downloads) unless a test asks
    return home


@pytest.fixture
def api(tmp_path):
    """(TestClient from this Mac, token, app). Requests come from 127.0.0.1 to http://127.0.0.1:8765."""
    from fastapi.testclient import TestClient

    from photoctl.server.app import create_app
    from photoctl.server.config import Settings

    app = create_app(Settings(library=tmp_path / "library", allowed_logins=["me@example.com"]))
    client = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
    return client, app.state.token, app


@pytest.fixture
def device():
    from photoctl.device import load_device

    return load_device()


@pytest.fixture
def bucket(monkeypatch):
    from moto import mock_aws

    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        import boto3

        from photoctl.s3 import CLIENT_CONFIG, Bucket

        client = boto3.client("s3", region_name="us-east-1", config=CLIENT_CONFIG)
        client.create_bucket(Bucket="test-archive")
        yield Bucket("test-archive", client=client)


def make_jpeg(path, size=(1600, 1200), color=(200, 120, 60)):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=85)
    return path

