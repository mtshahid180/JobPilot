"""Resume upload, text extraction and review-before-apply.

A resume is stored per user, parsed into a *draft* profile, and only merged
into the saved profile when the user confirms (optionally after editing) the
draft.  Extraction failures are surfaced honestly: a scanned PDF with no text
layer is marked failed rather than silently producing an empty profile.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from jobpilot.core.config import get_settings
from jobpilot.core.context import current_user_id
from jobpilot.core.db import Database, from_json, get_db, to_json
from jobpilot.core.models import Profile, Resume, ResumeDraft, utcnow
from jobpilot.services.profile import ProfileService

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
TEXT_EXTENSIONS = {".txt", ".md"}
MAX_TEXT_CHARS = 200_000

MERGABLE_FIELDS = (
    "full_name",
    "headline",
    "email",
    "phone",
    "location",
    "summary",
    "skills",
    "experience",
    "education",
    "projects",
    "certifications",
    "achievements",
    "links",
)


class ResumeError(ValueError):
    """User-facing resume problem (bad type, too large, unreadable)."""


@dataclass
class ExtractedText:
    text: str
    warning: str = ""


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", value or "user")[:80] or "user"


def _extension(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def extract_text(filename: str, data: bytes) -> ExtractedText:
    """Extract selectable text from an uploaded resume."""
    extension = _extension(filename)
    if extension == ".pdf":
        return _extract_pdf(data)
    if extension == ".docx":
        return _extract_docx(data)
    if extension in TEXT_EXTENSIONS:
        return ExtractedText(_decode_text(data))
    raise ResumeError(
        "Unsupported file type. Upload a PDF, DOCX, TXT or Markdown resume."
    )


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)[:MAX_TEXT_CHARS]
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")[:MAX_TEXT_CHARS]


def _extract_pdf(data: bytes) -> ExtractedText:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ResumeError(
            "PDF parsing is unavailable because the 'pypdf' package is not installed."
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ResumeError(f"Could not read this PDF: {exc}") from exc
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ResumeError(
                "This PDF is password protected. Upload an unlocked copy."
            ) from exc
    pages: list[str] = []
    warning = ""
    for index, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # a single damaged page should not fail the file
            warning = f"Page {index + 1} could not be read: {exc}"
    text = "\n".join(pages)[:MAX_TEXT_CHARS]
    if not text.strip():
        raise ResumeError(
            "No selectable text was found in this PDF. It may be a scanned image; "
            "OCR is not available, so paste the text or upload a text-based copy."
        )
    return ExtractedText(text, warning)


def _extract_docx(data: bytes) -> ExtractedText:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if "word/document.xml" not in archive.namelist():
                raise ResumeError("This file is not a valid DOCX document.")
            xml_bytes = archive.read("word/document.xml")
    except zipfile.BadZipFile as exc:
        raise ResumeError("This file is not a valid DOCX document.") from exc
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise ResumeError(f"Could not parse this DOCX file: {exc}") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        pieces: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{namespace}t" and node.text:
                pieces.append(node.text)
            elif node.tag == f"{namespace}tab":
                pieces.append("\t")
            elif node.tag == f"{namespace}br":
                pieces.append("\n")
        lines.append("".join(pieces))
    text = "\n".join(lines)[:MAX_TEXT_CHARS]
    if not text.strip():
        raise ResumeError("This DOCX contained no readable text.")
    return ExtractedText(text)


class ResumeService:
    def __init__(
        self,
        db: Database | None = None,
        profiles: ProfileService | None = None,
    ) -> None:
        self.db = db or get_db()
        self.profiles = profiles or ProfileService(self.db)
        self.settings = get_settings()

    # --- storage -----------------------------------------------------------
    def _storage_dir(self, user_id: str) -> Path:
        root = Path(self.settings.data_dir) / "resumes" / _safe_component(user_id)
        root.mkdir(parents=True, exist_ok=True)
        return root

    # --- reads -------------------------------------------------------------
    def list(self) -> list[Resume]:
        rows = self.db.query(
            "SELECT * FROM resumes WHERE user_id = ? ORDER BY created_at DESC",
            (current_user_id(),),
        )
        return [self._to_resume(row) for row in rows]

    def get(self, resume_id: str) -> Resume | None:
        row = self.db.query_one(
            "SELECT * FROM resumes WHERE id = ? AND user_id = ?",
            (resume_id, current_user_id()),
        )
        return self._to_resume(row) if row else None

    def current(self) -> Resume | None:
        row = self.db.query_one(
            "SELECT * FROM resumes WHERE user_id = ? AND is_current = 1",
            (current_user_id(),),
        )
        return self._to_resume(row) if row else None

    # --- upload ------------------------------------------------------------
    def upload(self, filename: str, data: bytes, content_type: str = "") -> ResumeDraft:
        user_id = current_user_id()
        filename = Path(filename or "resume").name
        extension = _extension(filename)
        if extension not in ALLOWED_EXTENSIONS:
            raise ResumeError(
                "Unsupported file type. Upload a PDF, DOCX, TXT or Markdown resume."
            )
        limit = max(1, int(self.settings.max_resume_mb)) * 1024 * 1024
        if not data:
            raise ResumeError("The uploaded file was empty.")
        if len(data) > limit:
            raise ResumeError(
                f"That file is larger than the {self.settings.max_resume_mb} MB limit."
            )

        resume = Resume(
            filename=filename,
            content_type=content_type or "",
            size=len(data),
            status="extracting",
        )
        target = self._storage_dir(user_id) / f"{resume.id}{extension}"
        target.write_bytes(data)
        resume.storage_path = str(target.relative_to(Path(self.settings.data_dir)))
        self._insert(resume, user_id)

        try:
            extracted = extract_text(filename, data)
        except ResumeError as exc:
            self._mark_failed(resume, str(exc))
            return ResumeDraft(
                resume=resume, profile_draft=self.profiles.get(), warnings=[str(exc)]
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._mark_failed(resume, f"Could not read this file: {exc}")
            return ResumeDraft(
                resume=resume, profile_draft=self.profiles.get(), warnings=[str(exc)]
            )

        text = extracted.text.strip()
        if len(text) < 40:
            message = (
                "The file contained too little text to build a profile. "
                "Paste the CV text instead."
            )
            self._mark_failed(resume, message)
            return ResumeDraft(
                resume=resume, profile_draft=self.profiles.get(), warnings=[message]
            )

        current_profile = self.profiles.get()
        # The review draft is built from the resume alone. Reusing the saved
        # profile would make missing fields look "detected" and hide what the
        # upload actually contained. Existing values are preserved on apply.
        draft = self.profiles.parse_cv_into(text, Profile())
        changed = changed_fields(current_profile, draft)
        warnings = [
            "Review every extracted field before applying. Nothing has been saved yet."
        ]
        if extracted.warning:
            warnings.append(extracted.warning)
        if not changed:
            warnings.append(
                "No new profile fields were recognised; the CV may use an unusual layout."
            )
        if not _has_bullets(draft):
            warnings.append(
                "No experience bullets were detected. Add them manually for better tailoring."
            )

        resume.status = "needs_review" if changed else "extracted"
        resume.extracted = draft.model_dump(mode="json")
        resume.text_chars = len(text)
        resume.error = ""
        resume.updated_at = utcnow()
        self._update_extraction(resume, user_id)
        return ResumeDraft(
            resume=resume,
            profile_draft=draft,
            changed_fields=changed,
            warnings=warnings,
        )

    # --- apply -------------------------------------------------------------
    def apply(
        self,
        resume_id: str,
        *,
        fields: list[str] | None = None,
        patch: dict | None = None,
    ) -> Profile:
        resume = self.get(resume_id)
        if resume is None:
            raise ResumeError("Resume not found.")
        if resume.status == "failed":
            raise ResumeError(resume.error or "This resume could not be read.")
        if not resume.extracted:
            raise ResumeError("This resume has no extracted profile to apply.")

        selected = [field for field in (fields or MERGABLE_FIELDS) if field in MERGABLE_FIELDS]
        if not selected:
            raise ResumeError("Select at least one field to apply.")

        draft_data = dict(resume.extracted)
        if patch:
            for key, value in patch.items():
                if key in MERGABLE_FIELDS:
                    draft_data[key] = value
        draft = Profile.model_validate(draft_data)

        current = self.profiles.get()
        merged = current.model_dump()
        draft_values = draft.model_dump()
        for field in selected:
            value = draft_values[field]
            # An empty field in the resume is not a replacement: keep whatever
            # the profile already had.
            if _is_empty(value):
                continue
            merged[field] = value
        profile = self.profiles.save(Profile.model_validate(merged))

        resume.status = "extracted"
        resume.is_current = True
        resume.updated_at = utcnow()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE resumes SET is_current = 0 WHERE user_id = ?", (current_user_id(),)
            )
            conn.execute(
                "UPDATE resumes SET status = ?, is_current = 1, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (
                    resume.status,
                    resume.updated_at.isoformat(),
                    resume.id,
                    current_user_id(),
                ),
            )
        return profile

    def set_current(self, resume_id: str) -> Resume | None:
        resume = self.get(resume_id)
        if resume is None:
            raise ResumeError("Resume not found.")
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE resumes SET is_current = 0 WHERE user_id = ?", (current_user_id(),)
            )
            conn.execute(
                "UPDATE resumes SET is_current = 1, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (utcnow().isoformat(), resume_id, current_user_id()),
            )
        return self.get(resume_id)

    def delete(self, resume_id: str) -> bool:
        resume = self.get(resume_id)
        if resume is None:
            return False
        if resume.storage_path:
            path = Path(self.settings.data_dir) / resume.storage_path
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # The row is still removed; a locked file is reported by the
                # caller through normal error handling if it matters.
                pass
        self.db.execute(
            "DELETE FROM resumes WHERE id = ? AND user_id = ?",
            (resume_id, current_user_id()),
        )
        return True

    # --- persistence -------------------------------------------------------
    def _insert(self, resume: Resume, user_id: str) -> None:
        self.db.execute(
            "INSERT INTO resumes (id, user_id, filename, content_type, size, storage_path, "
            "status, error, extracted, text_chars, is_current, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                resume.id,
                user_id,
                resume.filename,
                resume.content_type,
                resume.size,
                resume.storage_path,
                resume.status,
                resume.error,
                to_json(resume.extracted),
                resume.text_chars,
                int(resume.is_current),
                resume.created_at.isoformat(),
                resume.updated_at.isoformat(),
            ),
        )

    def _mark_failed(self, resume: Resume, message: str) -> None:
        resume.status = "failed"
        resume.error = message
        resume.updated_at = utcnow()
        self.db.execute(
            "UPDATE resumes SET status = ?, error = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (resume.status, resume.error, resume.updated_at.isoformat(), resume.id, current_user_id()),
        )

    def _update_extraction(self, resume: Resume, user_id: str) -> None:
        self.db.execute(
            "UPDATE resumes SET status = ?, error = ?, extracted = ?, text_chars = ?, "
            "updated_at = ? WHERE id = ? AND user_id = ?",
            (
                resume.status,
                resume.error,
                to_json(resume.extracted),
                resume.text_chars,
                resume.updated_at.isoformat(),
                resume.id,
                user_id,
            ),
        )

    def _to_resume(self, row: dict) -> Resume:
        return Resume(
            id=row["id"],
            filename=row["filename"],
            content_type=row["content_type"],
            size=int(row["size"] or 0),
            storage_path=row["storage_path"],
            status=row["status"],
            error=row["error"],
            extracted=from_json(row["extracted"], {}),
            text_chars=int(row["text_chars"] or 0),
            is_current=bool(row["is_current"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )


def changed_fields(current: Profile, draft: Profile) -> list[str]:
    before = current.model_dump(mode="json")
    after = draft.model_dump(mode="json")
    changed: list[str] = []
    for field in MERGABLE_FIELDS:
        if _normalise(after.get(field)) and _normalise(after.get(field)) != _normalise(
            before.get(field)
        ):
            changed.append(field)
    return changed


def _normalise(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).strip().lower()
    return str(value).strip().lower()


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        cleaned = value.strip().lower()
        return not cleaned or cleaned == "your name"
    if isinstance(value, (list, tuple, dict)):
        return not value
    return False


def _has_bullets(profile: Profile) -> bool:
    return any(experience.bullets for experience in profile.experience)


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)


_resume_service: ResumeService | None = None


def get_resume_service() -> ResumeService:
    global _resume_service
    if _resume_service is None:
        _resume_service = ResumeService()
    return _resume_service
