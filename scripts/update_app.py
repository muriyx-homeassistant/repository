#!/usr/bin/env python3
"""Publish Home Assistant App metadata into a thin catalog repository."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

FILES_TO_COPY = (
    "config.yaml",
    "README.md",
    "DOCS.md",
    "CHANGELOG.md",
    "icon.png",
    "logo.png",
    "apparmor.txt",
)
DIRS_TO_COPY = ("translations",)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--image", required=True)
    return parser.parse_args()


def validate_scalar(name: str, value: str) -> None:
    if not value or "\n" in value or "\r" in value:
        raise SystemExit(f"Invalid {name}")


def yaml_scalar(text: str, key: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*['\"]?([^'\"\n#]+)['\"]?\s*(?:#.*)?$", text)
    return match.group(1).strip() if match else None


def replace_top_level(text: str, key: str, value: str, *, required: bool) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(key)}:\s*.*$")
    replacement = f'{key}: "{value}"'
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    if required:
        raise SystemExit(f"Source config.yaml does not contain top-level '{key}'")
    if not text.endswith("\n"):
        text += "\n"
    return text + replacement + "\n"


def main() -> None:
    args = parse_args()
    validate_scalar("slug", args.slug)
    validate_scalar("version", args.version)
    validate_scalar("image", args.image)

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.slug):
        raise SystemExit("Invalid app slug")

    source = args.source.resolve()
    catalog = args.catalog.resolve()
    config = source / "config.yaml"
    if not source.is_dir() or not config.is_file():
        raise SystemExit(f"App source directory is invalid: {source}")

    source_config = config.read_text(encoding="utf-8")
    source_slug = yaml_scalar(source_config, "slug")
    if source_slug != args.slug:
        raise SystemExit(
            f"Payload slug '{args.slug}' does not match source config slug '{source_slug}'"
        )

    destination = catalog / args.slug
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    for name in FILES_TO_COPY:
        src = source / name
        if name == "README.md" and not src.is_file():
            for parent in (source, *source.parents):
                if (parent / ".git").exists():
                    src = parent / name
                    break
        if src.is_file():
            shutil.copy2(src, destination / name)

    for name in DIRS_TO_COPY:
        src = source / name
        if src.is_dir():
            shutil.copytree(src, destination / name)

    published_config = (destination / "config.yaml").read_text(encoding="utf-8")
    published_config = replace_top_level(
        published_config, "version", args.version, required=True
    )
    published_config = replace_top_level(
        published_config, "image", args.image, required=False
    )
    (destination / "config.yaml").write_text(published_config, encoding="utf-8")


if __name__ == "__main__":
    main()
