"""Reading canonical datasets, mapping documents, CSV exports and move lists."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Dataset, DatasetError


class LoaderError(ValueError):
    """Raised when an input file is missing or is not valid JSON/CSV."""


def read_json(path: Path) -> Any:
    path = Path(path)
    if not path.is_file():
        raise LoaderError(f"File not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise LoaderError(f"{path} is not valid JSON: {exc}") from exc


def write_json(path: Path, document: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False, sort_keys=False)
        handle.write("\n")
    return path


def load_dataset(path: Path) -> Dataset:
    document = read_json(Path(path))
    try:
        return Dataset.from_dict(document)
    except DatasetError as exc:
        raise LoaderError(f"{path} is not a valid canonical dataset: {exc}") from exc


def load_dataset_document(document: Dict[str, Any]) -> Dataset:
    try:
        return Dataset.from_dict(document)
    except DatasetError as exc:
        raise LoaderError(f"Invalid canonical dataset: {exc}") from exc


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    """Read a CSV export as a list of dictionaries, preserving column order.

    The delimiter is sniffed between ``,`` and ``;`` only, because those are the
    two separators every WMS export in practice uses and guessing further would
    make the result non-reproducible.
    """
    path = Path(path)
    if not path.is_file():
        raise LoaderError(f"CSV file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            raise LoaderError(f"{path} has no header row.")
        rows: List[Dict[str, str]] = []
        for raw in reader:
            rows.append({(k or "").strip(): (v or "").strip() for k, v in raw.items()})
        return rows


def csv_headers(path: Path) -> List[str]:
    path = Path(path)
    if not path.is_file():
        raise LoaderError(f"CSV file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.reader(handle, delimiter=delimiter)
        for row in reader:
            return [cell.strip() for cell in row]
    return []


def write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def write_text(path: Path, content: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def load_moves(path: Optional[Path]) -> Any:
    if path is None:
        raise LoaderError("A moves document is required.")
    return read_json(Path(path))
