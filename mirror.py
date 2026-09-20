#!/usr/bin/env python3
"""Build a static GitHub Pages mirror of the public portfolio."""

from __future__ import annotations

import html
import os
import re
import subprocess
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse


SOURCE = os.environ.get(
    "PORTFOLIO_SOURCE",
    "https://fedor-kharlamov-portfolio.kharlamov-f29.chatgpt.site",
).rstrip("/")
PUBLIC_URL = os.environ.get(
    "PUBLIC_URL", "https://kharlamov-portfolio.github.io"
).rstrip("/")
OUTPUT = Path(os.environ.get("OUTPUT_DIR", "_site"))
USER_AGENT = "Mozilla/5.0 (compatible; portfolio-static-mirror/1.0)"

SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.I | re.S)
MODULE_LINK_RE = re.compile(
    r"<link\b[^>]*\brel=[\"'](?:modulepreload|prefetch)[\"'][^>]*>", re.I
)
CODEX_META_RE = re.compile(r"<meta\b[^>]*\bname=[\"']codex-preview[\"'][^>]*>", re.I)
HREF_RE = re.compile(r"\bhref=[\"']([^\"']+)[\"']", re.I)
SRC_RE = re.compile(r"\bsrc=[\"']([^\"']+)[\"']", re.I)
SRCSET_RE = re.compile(r"\bsrcset=[\"']([^\"']+)[\"']", re.I)
CSS_URL_RE = re.compile(r"url\(\s*([\"']?)([^\"')]+)\1\s*\)", re.I)


def fetch(url: str) -> bytes:
    result = subprocess.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--retry",
            "8",
            "--retry-all-errors",
            "--retry-delay",
            "2",
            "--connect-timeout",
            "20",
            "--max-time",
            "120",
            "--user-agent",
            USER_AGENT,
            url,
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Could not fetch {url}: {message}")
    return result.stdout


def clean_path(value: str) -> str | None:
    if not value or value.startswith(("#", "data:", "mailto:", "tel:", "javascript:")):
        return None
    parsed = urlparse(urljoin(SOURCE + "/", html.unescape(value)))
    if parsed.netloc != urlparse(SOURCE).netloc:
        return None
    return parsed.path or "/"


def route_output(path: str) -> Path:
    if path == "/":
        return OUTPUT / "index.html"
    return OUTPUT / path.lstrip("/") / "index.html"


def asset_output(path: str) -> Path:
    return OUTPUT / path.lstrip("/")


def is_page_route(path: str) -> bool:
    return path == "/" or path == "/resume" or path.startswith("/projects/")


def sanitize_html(document: str) -> str:
    document = SCRIPT_RE.sub("", document)
    document = MODULE_LINK_RE.sub("", document)
    document = CODEX_META_RE.sub("", document)
    document = document.replace(SOURCE, PUBLIC_URL)
    document = re.sub(
        r"</head>",
        f'<link rel="canonical" href="{PUBLIC_URL}"></head>',
        document,
        count=1,
        flags=re.I,
    )
    return document


def extract_page_routes(document: str) -> set[str]:
    routes: set[str] = set()
    for value in HREF_RE.findall(document):
        path = clean_path(value)
        if path and is_page_route(path):
            routes.add(path.rstrip("/") or "/")
    return routes


def extract_assets(document: str) -> set[str]:
    assets: set[str] = set()
    for value in HREF_RE.findall(document) + SRC_RE.findall(document):
        path = clean_path(value)
        if path and not is_page_route(path) and not path.startswith("/api/"):
            assets.add(path)
    for srcset in SRCSET_RE.findall(document):
        for item in srcset.split(","):
            path = clean_path(item.strip().split(" ", 1)[0])
            if path and not is_page_route(path):
                assets.add(path)
    return assets


def save_asset(path: str, queued: deque[str], seen: set[str]) -> None:
    url = SOURCE + path
    destination = asset_output(path)
    try:
        data = fetch(url)
    except RuntimeError:
        if not destination.exists():
            raise
        print(f"Keeping existing asset after a temporary fetch failure: {path}")
        data = destination.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    if path.endswith(".css"):
        css = data.decode("utf-8")
        for _, value in CSS_URL_RE.findall(css):
            nested = clean_path(urljoin(url, value))
            if nested and nested not in seen:
                queued.append(nested)


def build() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / ".nojekyll").write_text("", encoding="utf-8")

    page_queue: deque[str] = deque(["/"])
    seen_pages: set[str] = set()
    asset_queue: deque[str] = deque()
    seen_assets: set[str] = set()

    while page_queue:
        route = page_queue.popleft()
        if route in seen_pages:
            continue
        raw = fetch(SOURCE + route)
        document = raw.decode("utf-8")
        for discovered in extract_page_routes(document):
            if discovered not in seen_pages:
                page_queue.append(discovered)
        cleaned = sanitize_html(document)
        for asset in extract_assets(cleaned):
            if asset not in seen_assets:
                asset_queue.append(asset)
        destination = route_output(route)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(cleaned, encoding="utf-8")
        seen_pages.add(route)

    while asset_queue:
        asset = asset_queue.popleft()
        if asset in seen_assets:
            continue
        save_asset(asset, asset_queue, seen_assets)
        seen_assets.add(asset)

    print(f"Built {len(seen_pages)} pages and {len(seen_assets)} assets in {OUTPUT}")


if __name__ == "__main__":
    build()
