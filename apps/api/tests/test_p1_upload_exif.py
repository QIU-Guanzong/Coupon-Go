"""P1-6 tests: review-image uploads strip EXIF before landing on disk.

JPEG APP1 (Exif), PNG eXIf and WebP EXIF chunks are dropped
losslessly — the remaining image bytes are untouched. GIF carries no
EXIF and passes through. Files that carry no EXIF segment upload
unchanged (the legacy fake-PNG test in test_line_f still passes).
"""
import os
import struct
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

MARKER = b"SECRET-GPS-MARKER-1234"

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    seed(db)
    db.close()

    def _get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _register_and_login(client, email):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": "Test",
        "role": "customer"}).status_code == 200
    code = client.get("/api/v3/_dev/otp", params={"email": email}).json()["code"]
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _jpeg_with_exif():
    app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    exif = b"Exif\x00\x00" + MARKER + b"\x00" * 10
    out = b"\xff\xd8"
    out += b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0
    out += b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    out += b"\xff\xd9"
    return out


def _png_with_exif():
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", 0))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"eXIf", MARKER + b"\x00" * 8)
            + chunk(b"IEND", b""))


def _webp_with_exif():
    def chunk(fourcc, data):
        pad = b"\x00" if len(data) % 2 else b""
        return fourcc + struct.pack("<I", len(data)) + data + pad
    vp8x = b"\x00" * 10
    body = b"WEBP" + chunk(b"VP8X", vp8x) + chunk(b"EXIF", MARKER + b"\x00" * 6)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _upload_and_fetch(client, h, filename, data, content_type):
    r = client.post("/api/v3/uploads/review-images",
                    files=[("files", (filename, data, content_type))],
                    headers=h)
    assert r.status_code == 201, r.text
    path = r.json()["images"][0]
    stored = client.get(path)
    assert stored.status_code == 200, stored.text
    return stored.content


def test_jpeg_exif_stripped_but_jfif_kept(client):
    h = _register_and_login(client, "p1-exif-jpeg@example.com")
    stored = _upload_and_fetch(client, h, "a.jpg", _jpeg_with_exif(),
                               "image/jpeg")
    assert MARKER not in stored
    assert b"Exif" not in stored
    assert b"JFIF" in stored  # non-EXIF segments untouched
    assert stored[:2] == b"\xff\xd8" and stored[-2:] == b"\xff\xd9"


def test_png_exif_chunk_stripped(client):
    h = _register_and_login(client, "p1-exif-png@example.com")
    stored = _upload_and_fetch(client, h, "a.png", _png_with_exif(),
                               "image/png")
    assert MARKER not in stored
    assert b"eXIf" not in stored
    assert stored.startswith(b"\x89PNG\r\n\x1a\n")


def test_webp_exif_chunk_stripped(client):
    h = _register_and_login(client, "p1-exif-webp@example.com")
    stored = _upload_and_fetch(client, h, "a.webp", _webp_with_exif(),
                               "image/webp")
    assert MARKER not in stored
    assert b"EXIF" not in stored
    assert stored[:4] == b"RIFF" and stored[8:12] == b"WEBP"


def test_gif_without_exif_passes_through(client):
    h = _register_and_login(client, "p1-exif-gif@example.com")
    gif = (b"GIF89a" + b"\x01\x00\x01\x00\x80\x00\x00"
           + b"\x00\x00\x00\xff\xff\xff" + b",\x00\x00\x00\x00\x01\x00\x01\x00"
           + b"\x00\x02\x02\x4c\x01\x00;")
    stored = _upload_and_fetch(client, h, "a.gif", gif, "image/gif")
    assert stored == gif
