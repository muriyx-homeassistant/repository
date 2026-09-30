#!/usr/bin/env python3
"""Publish Home Assistant App metadata into a thin catalog repository."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

FILES_TO_COPY = (
    "config.yaml",
    "README.md",
    "DOCS.md",
    "icon.png",
    "logo.png",
    "apparmor.txt",
)
DIRS_TO_COPY = ("translations",)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--source-repository", required=True)
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


def git_output(source: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source), *arguments], encoding="utf-8"
    ).strip()


def release_notes(repository: str, version: str) -> str:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "home-assistant-app-catalog",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"

    tags = (version,) if version.startswith("v") else (version, f"v{version}")
    for tag in tags:
        request = Request(
            f"https://api.github.com/repos/{repository}/releases/tags/{quote(tag, safe='')}",
            headers=headers,
        )
        try:
            with urlopen(request, timeout=30) as response:
                release = json.load(response)
        except HTTPError as error:
            error.close()
            if error.code == 404:
                continue
            raise SystemExit(f"Cannot fetch release {repository}@{tag}: {error}") from error
        except (URLError, TimeoutError) as error:
            raise SystemExit(f"Cannot fetch release {repository}@{tag}: {error}") from error

        notes = (release.get("body") or "").strip()
        return notes or (
            f"[Release](https://github.com/{repository}/releases/tag/{quote(tag, safe='')})"
        )

    raise SystemExit(f"Cannot find release {repository} for version {version}")


def markdown_headings(text: str) -> list[re.Match[str]]:
    headings = []
    fence = ""
    for match in re.finditer(
        r"(?m)^ {0,3}(?:(`{3,}|~{3,})([^\n]*)|(#{1,6})(?=[ \t]|$))", text
    ):
        if fence:
            if (
                match[1]
                and match[1][0] == fence[0]
                and len(match[1]) >= len(fence)
                and not match[2].strip()
            ):
                fence = ""
        elif match[1]:
            fence = match[1]
        else:
            headings.append(match)
    return headings


def prepare_changelog(
    source: Path, destination: Path, repository: str, version: str
) -> tuple[str, dict[str, str]]:
    state_path = destination / ".publication.json"
    previous = (
        json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    )
    if previous and previous["repository"] != repository:
        raise SystemExit("Cannot generate changelog: app source repository has changed")

    state = {
        "repository": repository,
        "version": version,
        "sha": git_output(source, "rev-parse", "HEAD"),
    }
    notes = release_notes(repository, version).replace("\r\n", "\n")
    # Nest release headings under the version without changing fenced code blocks.
    for heading in reversed(markdown_headings(notes)):
        notes = (
            notes[:heading.start(3)]
            + "#" * min(len(heading[3]) + 2, 6)
            + notes[heading.end(3):]
        )

    changelog_path = destination / "CHANGELOG.md"
    history = (
        changelog_path.read_text(encoding="utf-8") if changelog_path.is_file() else ""
    )
    history = re.sub(r"\A# [^\n]+\n*", "", history)
    boundaries = [0] + [
        heading.start() for heading in markdown_headings(history) if heading[3] == "##"
    ] + [len(history)]
    sections = []
    for start, end in zip(boundaries, boundaries[1:]):
        section = history[start:end]
        if not section.startswith(f"## {version}\n"):
            sections.append(section)
    history = "".join(sections).strip()

    changelog = f"# Changelog\n\n## {version}\n\n{notes}\n"
    if history:
        changelog += "\n" + history + "\n"
    return changelog, state


def main() -> None:
    args = parse_args()
    validate_scalar("slug", args.slug)
    validate_scalar("version", args.version)
    validate_scalar("image", args.image)

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.slug):
        raise SystemExit("Invalid app slug")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.source_repository):
        raise SystemExit("Invalid source repository")

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
    changelog, publication = prepare_changelog(
        source, destination, args.source_repository, args.version
    )
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
    (destination / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (destination / ".publication.json").write_text(
        json.dumps(publication, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
