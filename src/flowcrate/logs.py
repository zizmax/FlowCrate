import csv
import json
from dataclasses import dataclass

from .paths import LOGS_DIR, ensure_dirs


@dataclass
class LogSummary:
    filename: str
    rows: int
    modified: float
    format: str


def list_logs():
    ensure_dirs()
    summaries = []
    globbed = (
        list(LOGS_DIR.glob("*.json"))
        + list(LOGS_DIR.glob("*.csv"))
        + list(LOGS_DIR.glob("*.log"))
    )
    for path in globbed:
        rows = _count_rows(path)
        summaries.append(LogSummary(path.name, rows, path.stat().st_mtime, path.suffix.lstrip(".").upper()))
    return sorted(summaries, key=lambda s: s.modified, reverse=True)


def read_log(filename, status_filter=""):
    path = (LOGS_DIR / filename).resolve()
    if not path.exists() or path.parent != LOGS_DIR.resolve():
        raise FileNotFoundError(filename)
    if path.suffix == ".json":
        rows = _read_json_rows(path)
        if status_filter:
            rows = _filter_hierarchical_rows(rows, status_filter)
        return rows
    if path.suffix in (".log", ".txt"):
        # Plain-text server/launchd logs: one dict per line, newest last.
        text = path.read_text(encoding="utf-8", errors="replace")
        return [{"text": line} for line in text.splitlines()]
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if status_filter:
        rows = [row for row in rows if row.get("match_status") == status_filter]
    return rows


def _count_rows(path):
    if path.suffix == ".json":
        return len(_flatten(_read_json_rows(path)))
    if path.suffix in (".log", ".txt"):
        return sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
    return max(sum(1 for _ in path.open(encoding="utf-8")) - 1, 0)


def _read_json_rows(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("results", [])


def _flatten(rows):
    flat = []
    for row in rows:
        flat.append(row)
        flat.extend(row.get("children", []))
    return flat


def _filter_hierarchical_rows(rows, status_filter):
    filtered = []
    for row in rows:
        children = [child for child in row.get("children", []) if child.get("match_status") == status_filter]
        if row.get("match_status") == status_filter or children:
            row_copy = dict(row)
            row_copy["children"] = children if row.get("match_status") != status_filter else row.get("children", [])
            filtered.append(row_copy)
    return filtered
