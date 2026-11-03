import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from metrics_tracker import metrics_tracker
from portals.base import TenderPortal
from workflow_state import TenderState

DOWNLOAD_DIR = Path("data/downloads")


def safe_filename(name):
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_. "
    return "".join(c if c in allowed else "_" for c in name)


def valid_document(path: Path, filename: str) -> bool:
    """Reject empty files, HTML responses and incomplete PDF downloads."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    with path.open("rb") as file:
        header = file.read(1024)
        if filename.lower().endswith(".pdf"):
            if not header.startswith(b"%PDF-"):
                return False
            file.seek(max(0, path.stat().st_size - 1024))
            return b"%%EOF" in file.read()
        lowered = header.lstrip().lower()
        return not any(tag in lowered for tag in (b"<!doctype html", b"<html", b"<head", b"<form"))


def write_status(folder: Path, status: dict) -> None:
    path = folder / "download_status.json"
    temporary = folder / "download_status.json.tmp"
    temporary.write_text(json.dumps(status, indent=2), encoding="utf-8")
    temporary.replace(path)


def download_documents(tender, portal: TenderPortal):
    if not tender.relevant:
        return tender
    folder = DOWNLOAD_DIR / safe_filename(
        tender.tender_id or tender.reference_number or "unknown"
    )
    folder.mkdir(parents=True, exist_ok=True)
    status = {
        "portal": tender.portal,
        "tender_id": tender.tender_id,
        "status": "pending",
        "documents": {},
    }
    write_status(folder, status)
    failures = []
    for document in tender.documents:
        filename = safe_filename(document.name)
        path = folder / filename
        if valid_document(path, filename):
            status["documents"][filename] = {"status": "complete"}
            write_status(folder, status)
            continue
        temporary = None
        try:
            with NamedTemporaryFile(dir=folder, suffix=".part", delete=False) as file:
                temporary = Path(file.name)
            portal.download_document(document.url, temporary)
            if not valid_document(temporary, filename):
                raise ValueError(
                    "Response is not a valid document (possibly CAPTCHA/HTML or incomplete download)"
                )
            temporary.replace(path)
            status["documents"][filename] = {"status": "complete"}
        except Exception as exc:
            failures.append(f"{filename}: {exc}")
            status["documents"][filename] = {"status": "failed", "error": str(exc)}
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            write_status(folder, status)
    status["status"] = (
        "failed" if failures else "complete" if tender.documents else "no_documents"
    )
    write_status(folder, status)
    if failures:
        raise ValueError("; ".join(failures))
    return tender


def download_node(state: TenderState) -> dict[str, Any]:
    tender = state.get("tender")
    portal = state.get("portal")
    if tender is None:
        return {"error": "Cannot download documents without a tender"}
    if portal is None:
        return {"error": "Cannot download documents without a portal"}
    with metrics_tracker.track("download"):
        if not tender.relevant:
            return {"tender": tender}
        print("[DOWNLOAD] Validating existing files and retrying unfinished downloads")
        download_documents(tender, portal)
    return {"tender": tender}
