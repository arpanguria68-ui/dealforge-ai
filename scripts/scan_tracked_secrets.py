#!/usr/bin/env python3
"""Fail CI if tracked files contain likely hardcoded secrets."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".mov",
    ".avi",
    ".webm",
    ".xlsx",
    ".docx",
    ".pptx",
    ".sqlite",
    ".db",
}

SKIP_PATH_PARTS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
}

PLACEHOLDER_MARKERS = {
    "...",
    "***",
    "example",
    "replace",
    "placeholder",
    "changeme",
    "your-",
    "your_",
    "_here",
    "dummy",
    "test",
    "no_key_required",
}

ASSIGNMENT_KEY_NAMES = {
    "api_key",
    "token",
    "secret",
    "secret_key",
    "password",
    "access_key",
}

ENV_ASSIGNMENT_RE = re.compile(
    r"(?P<name>[A-Za-z0-9_]*(?:API_KEY|TOKEN|SECRET|SECRET_KEY|PASSWORD|ACCESS_KEY))\s*=\s*(?P<value>[^\s#]+)"
)

JSON_ASSIGNMENT_RE = re.compile(
    r"[\"']?(?P<name>[A-Za-z0-9_]*(?:API_KEY|TOKEN|SECRET|SECRET_KEY|PASSWORD|ACCESS_KEY))[\"']?\s*:\s*[\"'](?P<value>[^\"']+)[\"']"
)

DETECTORS = [
    ("OpenAI key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    (
        "AWS access key",
        re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    ),
    ("GitHub token", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
]

PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")


def tracked_files() -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    files: list[Path] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        rel = item.decode("utf-8", errors="ignore")
        path = ROOT / rel
        if path.is_file():
            files.append(path)
    return files


def should_skip(path: Path) -> bool:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    lower_parts = {part.lower() for part in path.parts}
    if lower_parts.intersection(SKIP_PATH_PARTS):
        return True
    return False


def is_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return True
    return b"\x00" in chunk


def clean_value(raw: str) -> str:
    value = raw.strip().strip("\"'`,;)")
    return value


def looks_placeholder(value: str) -> bool:
    if not value:
        return True
    lower = value.lower()
    if lower in {"null", "none", "false", "true"}:
        return True
    if lower.startswith("${") or lower.startswith("$("):
        return True
    if re.fullmatch(r"\$[a-zA-Z_][a-zA-Z0-9_]*", value):
        return True
    if lower.startswith("http://") or lower.startswith("https://"):
        return True
    if any(marker in lower for marker in PLACEHOLDER_MARKERS):
        return True
    return False


def looks_secret_value(value: str) -> bool:
    if looks_placeholder(value):
        return False
    if len(value) < 12:
        return False
    if re.fullmatch(r"[*-]+", value):
        return False
    return True


def scan_text(path: Path, text: str) -> list[str]:
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if PRIVATE_KEY_RE.search(line):
            findings.append(f"{path.relative_to(ROOT)}:{lineno}: private key block")

        for label, regex in DETECTORS:
            if regex.search(line):
                findings.append(f"{path.relative_to(ROOT)}:{lineno}: {label}")

        for match in ENV_ASSIGNMENT_RE.finditer(line):
            key_name = match.group("name").lower()
            value = clean_value(match.group("value"))
            if not any(suffix in key_name for suffix in ASSIGNMENT_KEY_NAMES):
                continue
            if looks_secret_value(value):
                findings.append(
                    f"{path.relative_to(ROOT)}:{lineno}: suspicious assignment ({match.group('name')})"
                )

        for match in JSON_ASSIGNMENT_RE.finditer(line):
            key_name = match.group("name").lower()
            value = clean_value(match.group("value"))
            if not any(suffix in key_name for suffix in ASSIGNMENT_KEY_NAMES):
                continue
            if looks_secret_value(value):
                findings.append(
                    f"{path.relative_to(ROOT)}:{lineno}: suspicious assignment ({match.group('name')})"
                )

    return findings


def main() -> int:
    all_findings: list[str] = []

    for path in tracked_files():
        if should_skip(path) or is_binary(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        all_findings.extend(scan_text(path, text))

    if all_findings:
        print("Potential secrets found in tracked files:")
        for finding in all_findings:
            print(f" - {finding}")
        print("\nResolve findings or replace with placeholders before committing.")
        return 1

    print("No likely hardcoded secrets found in tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
