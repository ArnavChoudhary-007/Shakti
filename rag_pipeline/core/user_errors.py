"""
core/user_errors.py
Turns ingestion failures into short, plain messages for the Documents page.

Library exceptions say things like "Package not found at 'C:\\Users\\...'" —
unhelpful, and they leak local paths into the browser (which may be the
public website). The technical detail is logged; the user gets a sentence
saying what's wrong and what to do.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Optional


class UserFacingError(ValueError):
    """An error whose message is already safe and helpful to show as-is."""


SUPPORTED_TYPES = (
    "PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx, .xls), CSV, text and Markdown, "
    "email (.eml, .mbox), WhatsApp/Telegram/Slack/Teams exports, audio, and images of invoices"
)

_OFFICE_KINDS = {".docx": "Word", ".pptx": "PowerPoint", ".xlsx": "Excel", ".xls": "Excel"}
_AUDIO = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".mp4"}
_IMAGES = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}


def unsupported_type(ext: str) -> UserFacingError:
    what = f"{ext} files" if ext else "files without an extension"
    return UserFacingError(f"Shakti can't read {what}. Supported: {SUPPORTED_TYPES}.")


def _missing_component(exc: BaseException) -> bool:
    if isinstance(exc, ImportError):
        return True
    text = str(exc).lower()
    return isinstance(exc, RuntimeError) and ("not installed" in text or "is required" in text or "requires" in text)


def _is_damaged_container(exc: BaseException) -> bool:
    text = str(exc).lower()
    return isinstance(exc, (zipfile.BadZipFile, KeyError)) or any(
        s in text for s in ("package not found", "not a zip file", "file is not a zip", "bad magic number",
                            "excel file format cannot be determined", "unsupported format")
    )


def describe_ingest_error(exc: BaseException, file_name: str, stage: Optional[str] = None) -> str:
    """One plain sentence (or two) for a failed upload. Never includes paths."""
    if isinstance(exc, UserFacingError):
        return str(exc)
    if isinstance(exc, MemoryError):
        return (f"There wasn't enough memory to process {file_name}. "
                "Close other apps and try again, or split the file into smaller ones.")

    if stage == "embedding":
        return (f"{file_name} was read, but Shakti couldn't index it because the document-search models "
                "aren't ready. Open Setup, finish \u201cDownload the search models\u201d, then upload again.")
    if stage == "saving":
        return (f"{file_name} was read, but Shakti couldn't save it to its data folder. "
                "Check that your disk has free space, then upload again.")

    ext = Path(file_name).suffix.lower()
    if _missing_component(exc):
        if ext == ".pst":
            return ("Outlook .pst mailboxes need an optional component that isn't installed. "
                    "Export the mailbox as .mbox or .eml instead and upload that.")
        if ext in _AUDIO:
            return "Audio transcription isn't installed in this copy of Shakti. Reinstall Shakti to add it."
        return f"This copy of Shakti is missing the component that reads {ext or 'these'} files. Reinstall Shakti to fix it."

    if ext == ".pdf":
        text = str(exc).lower()
        if "password" in text or "encrypted" in text:
            return f"{file_name} is password-protected. Remove the password (or print it to a new PDF) and upload again."
        return (f"{file_name} looks damaged, so Shakti couldn't open it. If it opens on your PC, "
                "print or export it to a new PDF and upload that.")
    if ext in _OFFICE_KINDS:
        kind = _OFFICE_KINDS[ext]
        if _is_damaged_container(exc):
            return (f"{file_name} isn't a valid {kind} file \u2014 it may be damaged, or renamed from another format. "
                    f"Open it in {kind}, save it again, and upload the new copy.")
        return f"Shakti couldn't read {file_name}. Open it in {kind}, save it again, and upload the new copy."
    if isinstance(exc, UnicodeDecodeError):
        return f"{file_name} uses a text encoding Shakti couldn't read. Save it as UTF-8 and upload again."
    if ext == ".json":
        return f"{file_name} isn't valid JSON, so Shakti couldn't read it."
    if ext in _AUDIO:
        return f"Shakti couldn't transcribe {file_name}. The recording may be damaged or in an unusual format."
    if ext in _IMAGES:
        return f"Shakti couldn't read the image {file_name}. It may be damaged."
    if ext in (".eml", ".mbox", ".msg"):
        return f"Shakti couldn't read the email file {file_name}. It may be damaged."
    return f"Shakti couldn't read {file_name}. The file may be damaged or in an unexpected format."
