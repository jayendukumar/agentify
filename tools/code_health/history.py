"""Git-history metrics: churn per file (for hotspots) and code growth vs
feature growth per commit.

Hotspot score = commits touching the file x its total cognitive
complexity. A file that is both complicated and changed often is where
defects and slow changes concentrate (Tornhill, "Your Code as a Crime
Scene"). Churn is counted over the whole history (or `--since`).

Growth vs features: for every commit, non-blank lines of production and
test code against three feature counts -- API endpoints (route decorators),
user stories written (`USn.m -- ...` lines in planning/epics) and stories in
epics whose status says implemented. Code per endpoint/story rising over
time means each new feature costs more code than the last.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

_ROUTE = re.compile(rb"@router\.(get|post|put|patch|delete)\(")
_STORY = re.compile(rb"^US\d+\.\d+\s+--", re.MULTILINE)
_IMPLEMENTED = re.compile(rb"\*\*Status:[^\n]*(implemented|done|built)", re.IGNORECASE)


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


@dataclass
class FileChurn:
    path: str
    commits: int
    lines_changed: int


def churn(repo: Path, since: str | None = None) -> dict[str, FileChurn]:
    args = ["log", "--numstat", "--format=@@%H", "--no-renames"]
    if since:
        args.append(f"--since={since}")
    commits: Counter[str] = Counter()
    changed: Counter[str] = Counter()
    for raw in _git(repo, *args).decode("utf-8", "replace").splitlines():
        if not raw or raw.startswith("@@"):
            continue
        parts = raw.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        commits[path] += 1
        if added != "-":
            changed[path] += int(added) + int(deleted)
    return {p: FileChurn(p, commits[p], changed[p]) for p in commits}


@dataclass
class GrowthPoint:
    commit: str
    date: str
    subject: str
    prod_loc: int
    test_loc: int
    endpoints: int
    stories: int
    stories_implemented: int


def _classify(path: str) -> str | None:
    if not path.endswith((".py", ".ts", ".tsx")) or "/migrations/" in path or path.endswith(".d.ts"):
        return None
    if path.startswith("backend/tests/") or ".test." in path or path.endswith("setupTests.ts"):
        return "test"
    if path.startswith(("backend/app/", "frontend/src/")):
        return "prod"
    return None


def growth(repo: Path) -> list[GrowthPoint]:
    log = _git(repo, "log", "--reverse", "--format=%H%x1f%ad%x1f%s", "--date=short").decode("utf-8", "replace")
    points: list[GrowthPoint] = []
    for line in log.splitlines():
        sha, date, subject = line.split("\x1f")
        tree = _git(repo, "ls-tree", "-r", sha).decode("utf-8", "replace")
        wanted: list[tuple[str, str]] = []  # (blob, kind)
        for entry in tree.splitlines():
            meta, path = entry.split("\t", 1)
            blob = meta.split()[2]
            kind = _classify(path)
            if kind:
                wanted.append((blob, kind))
            elif path.startswith("planning/epics/") and path.endswith(".md"):
                wanted.append((blob, "epic"))
        if not wanted:
            continue
        batch = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            input=b"".join(f"{b}\n".encode() for b, _ in wanted), capture_output=True, check=True,
        ).stdout
        totals: dict[str, int] = defaultdict(int)
        endpoints = stories = implemented = 0
        pos = 0
        for _, kind in wanted:
            header_end = batch.index(b"\n", pos)
            size = int(batch[pos:header_end].split()[2])
            content = batch[header_end + 1 : header_end + 1 + size]
            pos = header_end + 1 + size + 1
            if kind == "epic":
                n = len(_STORY.findall(content))
                stories += n
                if _IMPLEMENTED.search(content[:1500]):
                    implemented += n
                continue
            totals[kind] += sum(1 for ln in content.splitlines() if ln.strip())
            endpoints += len(_ROUTE.findall(content))
        points.append(GrowthPoint(sha[:7], date, subject, totals["prod"], totals["test"], endpoints, stories, implemented))
    return points
