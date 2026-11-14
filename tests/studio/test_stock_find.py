"""image_find: stock photos (Pexels, Unsplash) first, licence record per picture, people never generated.

No test touches the network: the HTTP callables are fakes, and the generator is an injected stub.
"""

import io
import json

import pytest
from PIL import Image

from funkbox.studio import stock, tools
from funkbox.studio.images import ImageResult

PEXELS = {
    "photos": [
        {
            "id": 1,
            "url": "https://www.pexels.com/photo/kettle-1/",
            "photographer": "Ana Lee",
            "width": 4000,
            "height": 3000,
            "alt": "kettle on a table",
            "src": {
                "large2x": "https://images.pexels.com/1-large2x.jpeg",
                "original": "https://images.pexels.com/1.jpeg",
            },
        }
    ]
}
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


def jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 80, 20)).save(buf, "JPEG")
    return buf.getvalue()


class Fake:
    """Answers search and download calls from canned payloads; records every call."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def json(self, url, headers=None):
        self.calls.append(("json", url, headers or {}))
        for needle, payload in self.answers.items():
            if needle in url:
                return payload
        return {}

    def bytes(self, url, headers=None):
        self.calls.append(("bytes", url, headers or {}))
        return jpeg_bytes()


class Boom:
    """A dependency that must not be used in this test."""

    def __call__(self, *args, **kwargs):
        raise AssertionError("network or generator must not be called here")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_HOME", str(tmp_path))
    return tmp_path


def inputs(home):
    return home / "inputs"


# ------------------------------------------------------------------ people rule
def test_people_words_force_the_people_flag_even_when_the_caller_says_no():
    assert stock.people_flag("a chef kneading dough", people=False) is True
    assert stock.people_flag("a family dinner") is True
    assert stock.people_flag("a red kettle") is False
    assert stock.people_flag("a red kettle", people=True) is True


# ------------------------------------------------------------------ records
def photo(source="unsplash"):
    return stock.Photo(
        source=source,
        photo_id="abc",
        source_url="https://unsplash.com/photos/abc",
        author="Ben Ox",
        image_url="https://images.unsplash.com/abc",
        width=3000,
        height=2000,
        alt="a red kettle",
    )


def test_record_validation_refuses_missing_licence_credit_or_people_flag():
    good = stock.make_record(photo(), retrieved_at="2026-11-12", contains_people=False, query="kettle")
    assert stock.validate_record(good)["credit_line"] == "Photo by Ben Ox on Unsplash"

    for missing in ("licence", "credit_line", "contains_people", "source"):
        bad = dict(good)
        del bad[missing]
        with pytest.raises(stock.StockError, match=missing):
            stock.validate_record(bad)

    empty_credit = dict(good, credit_line="")
    with pytest.raises(stock.StockError, match="credit_line"):
        stock.validate_record(empty_credit)


def test_parsers_read_pexels_and_unsplash_payloads():
    p = stock.parse_pexels(PEXELS)[0]
    assert (p.source, p.author) == ("pexels", "Ana Lee")
    assert p.image_url.endswith("1-large2x.jpeg")
    assert stock.credit_for(p) == "Photo by Ana Lee on Pexels"

    u = stock.parse_unsplash(UNSPLASH)[0]
    assert (u.source, u.author) == ("unsplash", "Ben Ox")
    assert u.download_location == "https://api.unsplash.com/photos/abc/download"
    assert stock.credit_for(u) == "Photo by Ben Ox on Unsplash"


# ------------------------------------------------------------------ the rule
def test_no_keys_means_no_network_no_generation_and_no_file(home):
    r = stock.find("a chef", env={}, http_json=Boom(), http_bytes=Boom(), generate=Boom())
    assert "NOT CONFIGURED" in r.text and "PEXELS_API_KEY" in r.text
    assert not inputs(home).exists() or not any(inputs(home).iterdir())


def test_people_never_fall_back_to_fastsd(home):
    fake = Fake({"pexels.com": {"photos": []}, "unsplash.com/search": {"results": []}})
    r = stock.find(
        "a family at dinner",
        env={"PEXELS_API_KEY": "k", "UNSPLASH_ACCESS_KEY": "u"},
        fallback="fastsd_scene",
        http_json=fake.json,
        http_bytes=fake.bytes,
        generate=Boom(),
    )
    assert r.text.startswith("NO MATCH")
    assert "generate" not in r.text.lower() or "never" in r.text.lower()


def test_a_non_people_miss_generates_only_when_asked_and_is_tagged_generated(home):
    fake = Fake({"pexels.com": {"photos": []}, "unsplash.com/search": {"results": []}})
    made = []

    def gen(prompt, aspect, name):
        made.append((prompt, aspect))
        path = inputs(home) / (stock.safe_stem(name) + ".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (512, 512), (9, 9, 9)).save(path)
        return ImageResult(f"saved {path}")

    stock.find(
        "a red kettle",
        env={"PEXELS_API_KEY": "k"},
        fallback="fastsd_scene",
        name="kettle-gen",
        http_json=fake.json,
        http_bytes=fake.bytes,
        generate=gen,
    )
    assert made == [("a red kettle", "square")]
    rec = json.loads((inputs(home) / "kettle-gen.credit.json").read_text(encoding="utf-8"))
    assert rec["source"] == "fastsd" and rec["licence"] == "generated" and rec["contains_people"] is False


def test_a_miss_without_fallback_says_so_and_generates_nothing(home):
    fake = Fake({"pexels.com": {"photos": []}})
    r = stock.find(
        "a red kettle", env={"PEXELS_API_KEY": "k"}, http_json=fake.json, http_bytes=fake.bytes, generate=Boom()
    )
    assert r.text.startswith("NO MATCH")


def test_a_hit_downloads_saves_the_file_and_records_the_credit(home):
    fake = Fake({"unsplash.com/search/photos": UNSPLASH, "/download": {"url": "x"}})
    r = stock.find(
        "a red kettle",
        env={"UNSPLASH_ACCESS_KEY": "u"},
        name="kettle",
        http_json=fake.json,
        http_bytes=fake.bytes,
        generate=Boom(),
    )
    assert (inputs(home) / "kettle.jpg").exists()
    rec = json.loads((inputs(home) / "kettle.credit.json").read_text(encoding="utf-8"))
    assert rec["source"] == "unsplash" and rec["credit_line"] == "Photo by Ben Ox on Unsplash"
    assert rec["contains_people"] is False and rec["release_status"] == "none"
    assert "Photo by Ben Ox on Unsplash" in r.text
    tracked = [c for c in fake.calls if c[0] == "json" and c[1].endswith("/download")]
    assert tracked and tracked[0][2].get("Authorization") == "Client-ID u"


def test_pexels_is_tried_before_unsplash(home):
    fake = Fake({"pexels.com": PEXELS, "unsplash.com/search": UNSPLASH})
    stock.find(
        "a red kettle",
        env={"PEXELS_API_KEY": "k", "UNSPLASH_ACCESS_KEY": "u"},
        name="kettle",
        http_json=fake.json,
        http_bytes=fake.bytes,
        generate=Boom(),
    )
    rec = json.loads((inputs(home) / "kettle.credit.json").read_text(encoding="utf-8"))
    assert rec["source"] == "pexels"
    assert not any("unsplash.com/search" in c[1] for c in fake.calls)


def test_bad_arguments_are_refused(home):
    with pytest.raises(ValueError):
        stock.find("   ", env={"PEXELS_API_KEY": "k"}, http_json=Boom(), http_bytes=Boom())
    with pytest.raises(ValueError):
        stock.find("kettle", orientation="cinema", env={"PEXELS_API_KEY": "k"}, http_json=Boom(), http_bytes=Boom())
    with pytest.raises(ValueError):
        stock.find("kettle", fallback="dalle", env={"PEXELS_API_KEY": "k"}, http_json=Boom(), http_bytes=Boom())


# ------------------------------------------------------------------ MCP surface
def test_image_find_is_an_mcp_tool_and_image_generate_points_to_it():
    assert tools.image_find in tools.TOOLS
    assert "image_find" in (tools.image_generate.__doc__ or "")
    assert "not for pictures of people" in (tools.image_generate.__doc__ or "").lower()
