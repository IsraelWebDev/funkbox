"""image_find: a licensed stock photo first (Pexels, then Unsplash), a credit record beside every picture.

The rule (decided by Zvi, 2026-11-12): stock first, almost always. A picture with people comes from stock or not
at all: it is never generated, because FastSD draws faces badly. A picture without people may fall back to FastSD,
but only when the caller asks for ``fallback="fastsd_scene"``. Every saved picture gets ``inputs/<name>.credit.json``
with the same fields the hosted site uses (see claude/stock-sourcing-adapted.md in the Funkbox project).

Keys come from the environment (``PEXELS_API_KEY``, ``UNSPLASH_ACCESS_KEY``), are never logged and never written to
a file. With no key set, nothing is sent anywhere. The HTTP calls and the generator are injectable, so the tests run
offline.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from PIL import Image as PILImage

from funkbox import config
from funkbox.studio import images
from funkbox.studio.sdcore import safe_name

SOURCES = ("pexels", "unsplash", "cc0_site", "pixabay", "local_upload", "fastsd")
LICENCES = {
    "pexels": "Pexels Licence",
    "unsplash": "Unsplash Licence",
    "cc0": "CC0 1.0 Public Domain Dedication",
    "pixabay": "Pixabay Content Licence",
    "owner": "Owner's own material",
    "generated": "Stability AI Community License (SD-Turbo)",
}
RELEASES = ("none", "stock_licence_only", "model_release_on_file")
CREDIT_REQUIRED = ("unsplash", "pixabay")  # the licence (or the API's terms) asks for a visible credit
REQUIRED = (
    "id",
    "source",
    "source_url",
    "retrieved_at",
    "author",
    "licence",
    "licence_name",
    "credit_line",
    "contains_people",
    "release_status",
)
ORIENTATION_ASPECT = {"landscape": "landscape", "portrait": "portrait", "square": "square"}
UNSPLASH_ORIENTATION = {"landscape": "landscape", "portrait": "portrait", "square": "squarish"}
FALLBACKS = ("none", "fastsd_scene")
KEYS = (("pexels", "PEXELS_API_KEY"), ("unsplash", "UNSPLASH_ACCESS_KEY"), ("pixabay", "PIXABAY_API_KEY"))
PIXABAY_ORIENTATION = {"landscape": "horizontal", "portrait": "vertical", "square": "all"}

# Words that mean a picture shows people. A keyword check, not a face detector: it errs toward "people".
PEOPLE_RX = re.compile(
    r"\b(people|person|persons|men|man|women|woman|boy|boys|girl|girls|child|children|kid|kids|baby|babies|"
    r"family|families|friend|friends|couple|crowd|team|chef|baker|worker|workers|customer|customers|student|"
    r"students|teacher|doctor|nurse|farmer|model|mother|father|grandma|grandmother|grandfather|guy|lady|"
    r"portrait|face|faces|selfie|hands)\b",
    re.I,
)

NOT_CONFIGURED = (
    "NOT CONFIGURED: no stock key is set. Set PEXELS_API_KEY (free, pexels.com/api), UNSPLASH_ACCESS_KEY "
    "(free, unsplash.com/developers) or PIXABAY_API_KEY (free, pixabay.com/api/docs). Nothing was searched or made. "
    "People pictures are never generated."
)


class StockError(ValueError):
    """A record that does not meet the credit rules, or a bad request."""


@dataclass
class Photo:
    """One search result, before it is downloaded."""

    source: str
    photo_id: str
    source_url: str
    author: str
    image_url: str
    width: int
    height: int
    alt: str = ""
    download_location: str = ""  # Unsplash only: called when the photo is taken


def people_flag(query: str, people: bool | None = None) -> bool:
    """True when the caller says so or the query names people. The caller cannot switch the check off."""
    if people is None:
        return bool(PEOPLE_RX.search(query))
    return bool(people) or bool(PEOPLE_RX.search(query))


def credit_for(p: Photo) -> str:
    verb = "Image" if p.source == "pixabay" else "Photo"
    return f"{verb} by {p.author} on {p.source.capitalize()}"


def make_record(p: Photo, *, retrieved_at: str, contains_people: bool, query: str) -> dict:
    return {
        "id": f"{p.source}-{p.photo_id}",
        "source": p.source,
        "source_url": p.source_url,
        "retrieved_at": retrieved_at,
        "author": p.author,
        "licence": p.source,
        "licence_name": LICENCES[p.source],
        "credit_line": credit_for(p),
        "contains_people": bool(contains_people),
        "release_status": "stock_licence_only" if contains_people else "none",
        "download_tracking_url": p.download_location,
        "cache_until": "",
        "query": query,
    }


def generated_record(stem: str, prompt: str, retrieved_at: str) -> dict:
    return {
        "id": f"fastsd-{stem}",
        "source": "fastsd",
        "source_url": "",
        "retrieved_at": retrieved_at,
        "author": "funkbox (FastSD, SD-Turbo)",
        "licence": "generated",
        "licence_name": LICENCES["generated"],
        "credit_line": "",
        "contains_people": False,  # the fallback never makes a picture with people
        "release_status": "none",
        "download_tracking_url": "",
        "cache_until": "",
        "query": prompt,
    }


def validate_record(rec: dict) -> dict:
    missing = [k for k in REQUIRED if k not in rec]
    if missing:
        raise StockError(f"record missing {', '.join(missing)}")
    if rec["source"] not in SOURCES:
        raise StockError(f"unknown source {rec['source']!r}")
    if rec["licence"] not in LICENCES:
        raise StockError(f"unknown licence {rec['licence']!r}")
    if rec["release_status"] not in RELEASES:
        raise StockError(f"unknown release_status {rec['release_status']!r}")
    if not isinstance(rec["contains_people"], bool):
        raise StockError("contains_people must be true or false")
    if (rec["source"] in CREDIT_REQUIRED or rec["licence"] in CREDIT_REQUIRED) and not str(rec["credit_line"]).strip():
        raise StockError(f"credit_line is required for {rec['source']}")
    return rec


def parse_pexels(payload: dict) -> list[Photo]:
    out = []
    for ph in payload.get("photos", []) or []:
        src = ph.get("src") or {}
        url = src.get("large2x") or src.get("original") or ""
        if not url:
            continue
        out.append(
            Photo(
                source="pexels",
                photo_id=str(ph.get("id", "")),
                source_url=str(ph.get("url", "")),
                author=str(ph.get("photographer", "") or "Unknown photographer"),
                image_url=url,
                width=int(ph.get("width", 0) or 0),
                height=int(ph.get("height", 0) or 0),
                alt=str(ph.get("alt", "") or ""),
            )
        )
    return out


def parse_unsplash(payload: dict) -> list[Photo]:
    out = []
    for ph in payload.get("results", []) or []:
        urls = ph.get("urls") or {}
        links = ph.get("links") or {}
        url = urls.get("regular") or ""
        if not url:
            continue
        out.append(
            Photo(
                source="unsplash",
                photo_id=str(ph.get("id", "")),
                source_url=str(links.get("html", "")),
                author=str((ph.get("user") or {}).get("name", "") or "Unknown photographer"),
                image_url=url,
                width=int(ph.get("width", 0) or 0),
                height=int(ph.get("height", 0) or 0),
                alt=str(ph.get("alt_description", "") or ""),
                download_location=str(links.get("download_location", "") or ""),
            )
        )
    return out


def parse_pixabay(payload: dict) -> list[Photo]:
    out = []
    for ph in payload.get("hits", []) or []:
        url = ph.get("largeImageURL") or ph.get("webformatURL") or ""
        if not url:
            continue
        out.append(
            Photo(
                source="pixabay",
                photo_id=str(ph.get("id", "")),
                source_url=str(ph.get("pageURL", "")),
                author=str(ph.get("user", "") or "Unknown photographer"),
                image_url=url,
                width=int(ph.get("imageWidth", 0) or 0),
                height=int(ph.get("imageHeight", 0) or 0),
                alt=str(ph.get("tags", "") or ""),
            )
        )
    return out


def configured(env) -> list[tuple[str, str]]:
    return [(src, key) for src, key in KEYS if env.get(key)]


def search(query: str, orientation: str, env, http_json: Callable) -> list[Photo]:
    """Pexels, then Unsplash, then Pixabay: the first provider with results wins.

    A failed request raises StockError naming the provider only. The URL carries the Pixabay key, so the
    original exception is never passed on.
    """
    for src, key in configured(env):
        try:
            if src == "pexels":
                qs = urllib.parse.urlencode({"query": query, "per_page": 5, "orientation": orientation})
                payload = http_json(f"https://api.pexels.com/v1/search?{qs}", {"Authorization": env[key]})
                found = parse_pexels(payload)
            elif src == "unsplash":
                qs = urllib.parse.urlencode(
                    {"query": query, "per_page": 5, "orientation": UNSPLASH_ORIENTATION[orientation]}
                )
                headers = {"Authorization": f"Client-ID {env[key]}", "Accept-Version": "v1"}
                found = parse_unsplash(http_json(f"https://api.unsplash.com/search/photos?{qs}", headers))
            else:
                qs = urllib.parse.urlencode(
                    {
                        "key": env[key],
                        "q": query,
                        "image_type": "photo",
                        "per_page": 5,
                        "orientation": PIXABAY_ORIENTATION[orientation],
                        "safesearch": "true",
                    }
                )
                found = parse_pixabay(http_json(f"https://pixabay.com/api/?{qs}", None))
        except StockError:
            raise
        except Exception:  # noqa: BLE001 - the message must not carry a URL or a key
            raise StockError(f"{src} search failed; no picture was saved") from None
        if found:
            return found
    return []


def _write_record(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _take(p: Photo, query: str, want_people: bool, stem: str, env, http_bytes: Callable, http_json: Callable):
    inputs = config.get().inputs
    dest = inputs / f"{stem}.jpg"
    rec = validate_record(
        make_record(p, retrieved_at=date.today().isoformat(), contains_people=want_people, query=query)
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(http_bytes(p.image_url, None))
    _write_record(dest.with_suffix(".credit.json"), rec)
    if p.download_location and "UNSPLASH_ACCESS_KEY" in env:
        # Unsplash's API guidelines ask for this call when a photo is taken.
        http_json(p.download_location, {"Authorization": f"Client-ID {env['UNSPLASH_ACCESS_KEY']}"})
    text = "\n".join(
        [
            f"saved {dest}",
            f"image path: {dest.relative_to(config.get().home).as_posix() if dest.is_relative_to(config.get().home) else dest}",
            f"{rec['credit_line']} ({rec['licence_name']})",
            f"contains people: {'yes' if want_people else 'no'}; release: {rec['release_status']}",
            f"record: {dest.with_suffix('.credit.json')}",
        ]
    )
    return images.ImageResult(text, PILImage.open(dest))


def _generated(prompt: str, orientation: str, stem: str, generate: Callable):
    res = generate(prompt, ORIENTATION_ASPECT[orientation], stem)
    path = config.get().inputs / f"{stem}.png"
    if not path.exists():
        return res  # FastSD answered with a warning (a size that still needs compiling); nothing was saved
    rec = validate_record(generated_record(stem, prompt, date.today().isoformat()))
    _write_record(path.with_suffix(".credit.json"), rec)
    return images.ImageResult(
        f"{res.text}\nlicence: generated (not stock); record: {path.with_suffix('.credit.json')}",
        res.image,
    )


def _generate_default(prompt: str, aspect: str, name: str):
    return images.generate(prompt, aspect=aspect, name=name)


def _get_json(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "funkbox", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_bytes(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "funkbox", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def safe_stem(name: str) -> str:
    return safe_name(name, "stock")


def find(
    query: str,
    *,
    people: bool | None = None,
    orientation: str = "square",
    fallback: str = "none",
    name: str = "",
    env=None,
    http_json: Callable | None = None,
    http_bytes: Callable | None = None,
    generate: Callable | None = None,
) -> images.ImageResult:
    """A licensed stock photo for ``query``, saved to inputs/ with its credit record.

    People pictures: stock or nothing. Other pictures: stock, then FastSD only if ``fallback="fastsd_scene"``.
    """
    q = (query or "").strip()
    if not q:
        raise StockError("query is empty")
    if orientation not in ORIENTATION_ASPECT:
        raise StockError(f"orientation must be one of {', '.join(ORIENTATION_ASPECT)}")
    if fallback not in FALLBACKS:
        raise StockError(f"fallback must be one of {', '.join(FALLBACKS)}")
    env = os.environ if env is None else env
    http_json = http_json or _get_json
    http_bytes = http_bytes or _get_bytes
    generate = generate or _generate_default

    want_people = people_flag(q, people)
    stem = safe_name(name, "stock-" + hashlib.sha256(q.encode("utf-8")).hexdigest()[:6])
    keys = configured(env)

    if keys:
        found = search(q, orientation, env, http_json)
        if found:
            return _take(found[0], q, want_people, stem, env, http_bytes, http_json)
    if want_people:
        if not keys:
            return images.ImageResult(NOT_CONFIGURED)
        return images.ImageResult(
            f"NO MATCH: no stock photo for {q!r}. Try other words. People pictures are never generated."
        )
    if fallback == "fastsd_scene":
        return _generated(q, orientation, stem, generate)
    if not keys:
        return images.ImageResult(NOT_CONFIGURED)
    return images.ImageResult(f"NO MATCH: no stock photo for {q!r}. Try other words, or ask for fallback=fastsd_scene.")
