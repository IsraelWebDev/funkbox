"""Unsplash (and Pixabay) local copies expire after 24 hours; the tool removes them on its next run.

Offline: fake HTTP, temp folder. Pexels copies are not expired (no cache rule in its terms).
"""

import json
from datetime import date, timedelta

import pytest
from PIL import Image

from funkbox.studio import stock

UNSPLASH = {
    "results": [
        {
            "id": "abc",
            "width": 3000,
            "height": 2000,
            "alt_description": "a red kettle",
            "links": {
                "html": "https://unsplash.com/photos/abc",
                "download_location": "https://api.unsplash.com/photos/abc/download",
            },
            "urls": {"regular": "https://images.unsplash.com/abc?w=1080"},
            "user": {"name": "Ben Ox", "links": {"html": "https://unsplash.com/@ben"}},
        }
    ]
}


def jpeg() -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 80, 20)).save(buf, "JPEG")
    return buf.getvalue()


class Fake:
    def json(self, url, headers=None):
        if "unsplash.com/search" in url:
            return UNSPLASH
        return {}

    def bytes(self, url, headers=None):
        return jpeg()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_HOME", str(tmp_path))
    return tmp_path


def record(source, retrieved, **extra):
    base = {
        "id": f"{source}-1",
        "source": source,
        "source_url": "https://example.test/1",
        "retrieved_at": retrieved,
        "author": "A",
        "licence": source,
        "licence_name": "x",
        "credit_line": "Photo by A on Unsplash",
        "contains_people": False,
        "release_status": "none",
        "download_tracking_url": "",
        "cache_until": "",
        "query": "q",
    }
    base.update(extra)
    return base


def test_unsplash_and_pixabay_records_carry_an_expiry_one_day_after_retrieval():
    today = date(2026, 11, 12)
    p = stock.Photo(source="unsplash", photo_id="1", source_url="u", author="A", image_url="i", width=1, height=1)
    rec = stock.make_record(p, retrieved_at=today.isoformat(), contains_people=False, query="q")
    assert rec["expires_at"] == (today + timedelta(days=1)).isoformat()

    pe = stock.Photo(source="pexels", photo_id="2", source_url="u", author="A", image_url="i", width=1, height=1)
    assert stock.make_record(pe, retrieved_at=today.isoformat(), contains_people=False, query="q")["expires_at"] == ""


def test_validation_refuses_an_unsplash_record_without_an_expiry():
    rec = record("unsplash", "2026-11-12", expires_at="")
    with pytest.raises(stock.StockError, match="expires_at"):
        stock.validate_record(rec)


def test_purge_removes_expired_unsplash_copies_and_keeps_the_rest(home):
    inputs = home / "inputs"
    inputs.mkdir(parents=True)
    old = date(2026, 11, 10)
    today = date(2026, 11, 12)
    for name, src, expires in [
        ("old-u", "unsplash", (old + timedelta(days=1)).isoformat()),
        ("fresh-u", "unsplash", (today + timedelta(days=1)).isoformat()),
        ("px-old", "pixabay", (old + timedelta(days=1)).isoformat()),
        ("pexels-old", "pexels", ""),
    ]:
        (inputs / f"{name}.jpg").write_bytes(jpeg())
        (inputs / f"{name}.credit.json").write_text(
            json.dumps(record(src, old.isoformat(), expires_at=expires)), encoding="utf-8"
        )
    removed = stock.purge_expired(inputs, today)
    names = {p.name for p in removed}
    assert {"old-u.jpg", "old-u.credit.json", "px-old.jpg", "px-old.credit.json"} <= names
    assert (inputs / "fresh-u.jpg").exists() and (inputs / "pexels-old.jpg").exists()
    assert not (inputs / "old-u.jpg").exists()


def test_a_find_run_purges_expired_copies_first(home):
    inputs = home / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "stale.jpg").write_bytes(jpeg())
    (inputs / "stale.credit.json").write_text(
        json.dumps(record("unsplash", "2026-11-01", expires_at="2026-11-02")), encoding="utf-8"
    )
    stock.find(
        "a red kettle", env={"UNSPLASH_ACCESS_KEY": "u"}, name="kettle", http_json=Fake().json, http_bytes=Fake().bytes
    )
    assert not (inputs / "stale.jpg").exists() and not (inputs / "stale.credit.json").exists()
    rec = json.loads((inputs / "kettle.credit.json").read_text(encoding="utf-8"))
    assert rec["expires_at"] and rec["source"] == "unsplash"
