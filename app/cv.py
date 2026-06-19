"""CV ingestion: extract text from an uploaded resume (PDF or text)."""
import io


def extract_text(filename: str | None, raw: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            return ""
    try:
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""
