"""Pixabay as a stock source: keyed by PIXABAY_API_KEY, last in the order, never leaks its key.

Offline: fake HTTP, no network. The key is only ever a value in a fake environment dict.
"""

import io
import json

import pytest
from PIL import Image

from funkbox.studio import stock

PIXABAY = {
    "total": 1,
    "hits": [
        {
            "id": 555,
            "pageURL": "https://pixabay.com/photos/kettle-555/",
            "user": "Mira Sol",
            "imageWidth": 4000,
            "imageHeight": 3000,
            "tags": "kettle, tea",
            "webformatURL": "https://pixabay.com/get/555_640.jpg",
            "largeImageURL": "https://pixabay.com/get/555_1280.jpg",
        }
    ],
}
EMPTY = {"total": 0, "hits": []}
FAKE_KEY = "pbk-fake-0001"


class Fake:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def json(self, url, headers=None):
        self.calls.append((url, headers or {}))
        for needle, payload in self.answers.items():
            if needle in url:
                return payload
        return {}

    def bytes(self, url, headers=None):
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (10, 120, 200)).save(buf, "JPEG")
        return buf.getvalue()


class Boom:
    def __call__(self, *a, **k):
        raise AssertionError("must not be called")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_HOME", str(tmp_path))
    return tmp_path


def test_parse_pixabay_reads_hits_and_the_large_image():
    p = stock.parse_pixabay(PIXABAY)[0]
    assert (p.source, p.photo_id, p.author) == ("pixabay", "555", "Mira Sol")
    assert p.image_url.endswith("555_1280.jpg")
    assert p.source_url == "https://pixabay.com/photos/kettle-555/"
    assert stock.credit_for(p) == "Image by Mira Sol on Pixabay"


def test_pixabay_record_needs_its_credit_line():
    p = stock.parse_pixabay(PIXABAY)[0]
    rec = stock.make_record(p, retrieved_at="2026-11-12", contains_people=False, query="kettle")
    assert stock.validate_record(rec)["licence"] == "pixabay"
    with pytest.raises(stock.StockError, match="credit_line"):
        stock.validate_record(dict(rec, credit_line=""))


def test_pixabay_is_used_only_after_pexels_and_unsplash_have_nothing(home):
    env = {"PEXELS_API_KEY": "p", "UNSPLASH_ACCESS_KEY": "u", "PIXABAY_API_KEY": FAKE_KEY}
    fake = Fake({"pexels.com": {"photos": []}, "unsplash.com/search": {"results": []}, "pixabay.com/api": PIXABAY})
    stock.find("a red kettle", env=env, name="kettle", http_json=fake.json, http_bytes=fake.bytes, generate=Boom())
    rec = json.loads((home / "inputs" / "kettle.credit.json").read_text(encoding="utf-8"))
    assert rec["source"] == "pixabay" and rec["credit_line"] == "Image by Mira Sol on Pixabay"


def test_pixabay_alone_works_and_sends_the_key_as_a_query_parameter(home):
    fake = Fake({"pixabay.com/api": PIXABAY})
    stock.find("a red kettle", env={"PIXABAY_API_KEY": FAKE_KEY}, name="k", http_json=fake.json, http_bytes=fake.bytes)
    url, headers = fake.calls[0]
    assert url.startswith("https://pixabay.com/api/?") and f"key={FAKE_KEY}" in url
    assert "image_type=photo" in url and "safesearch=true" in url
    assert FAKE_KEY not in json.dumps(headers)


def test_a_failed_request_never_shows_the_key(home):
    def broken(url, headers=None):
        raise OSError(f"connection reset for {url}")

    with pytest.raises(stock.StockError) as err:
        stock.find("a red kettle", env={"PIXABAY_API_KEY": FAKE_KEY}, http_json=broken, http_bytes=Boom())
    assert FAKE_KEY not in str(err.value)
    assert "pixabay" in str(err.value).lower()


def test_no_pixabay_key_means_no_pixabay_call(home):
    fake = Fake({})
    r = stock.find("a red kettle", env={}, http_json=fake.json, http_bytes=fake.bytes, generate=Boom())
    assert "NOT CONFIGURED" in r.text and "PIXABAY_API_KEY" in r.text
    assert fake.calls == []


def test_pixabay_orientation_mapping():
    assert stock.PIXABAY_ORIENTATION == {"landscape": "horizontal", "portrait": "vertical", "square": "all"}
