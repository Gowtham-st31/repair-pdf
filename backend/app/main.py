from __future__ import annotations

from io import BytesIO
from pathlib import Path
import time
import uuid
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import pdf_ops

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

app = FastAPI(title="PDF Editor")


@app.middleware("http")
async def no_cache_middleware(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store"
    return response

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory preview cache: id -> (created_ts, pdf_bytes, page_count)
_preview_store: Dict[str, Tuple[float, bytes, int]] = {}
_PREVIEW_TTL_SECONDS = 10 * 60


def _preview_gc() -> None:
    now = time.time()
    expired = [k for k, (ts, _b, _pc) in _preview_store.items() if now - ts > _PREVIEW_TTL_SECONDS]
    for k in expired:
        _preview_store.pop(k, None)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _pdf_stream_response(pdf_bytes: bytes, *, filename: str, extra_headers: Optional[Dict[str, str]] = None) -> StreamingResponse:
    buf = BytesIO(pdf_bytes)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if extra_headers:
        headers.update(extra_headers)
    return StreamingResponse(buf, media_type="application/pdf", headers=headers)


@app.post("/api/page-count")
async def api_page_count(pdf: UploadFile = File(...)):
    try:
        pdf_bytes = await pdf.read()
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        count = doc.page_count
        doc.close()
        return {"pageCount": count}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Unable to read PDF") from exc


@app.post("/api/preview-session")
async def api_preview_session(pdf: UploadFile = File(...)):
    """Create a short-lived preview session for rendering pages as images."""
    _preview_gc()
    try:
        pdf_bytes = await pdf.read()
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        count = doc.page_count
        doc.close()

        preview_id = uuid.uuid4().hex
        _preview_store[preview_id] = (time.time(), pdf_bytes, count)
        return {"sessionId": preview_id, "pageCount": count}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Unable to read PDF") from exc


def _clamp_scale(scale: float) -> float:
    try:
        s = float(scale)
    except Exception:
        return 1.2
    if s < 0.5:
        return 0.5
    if s > 2.0:
        return 2.0
    return s


def _render_preview_page(preview_id: str, page_number: int, *, scale: float = 1.2):
    _preview_gc()
    entry = _preview_store.get(preview_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Preview session not found")

    ts, pdf_bytes, page_count = entry
    if page_number < 1 or page_number > page_count:
        raise HTTPException(status_code=400, detail=f"page_number must be between 1 and {page_count}")

    try:
        import fitz  # PyMuPDF
        from fastapi.responses import Response

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc.load_page(page_number - 1)
        # Scale is clamped for safety.
        s = _clamp_scale(scale)
        mat = fitz.Matrix(s, s)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        doc.close()
        return Response(content=img_bytes, media_type="image/png")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Failed to render preview") from exc


def _pick_word_at_point(page, *, x: float, y: float) -> str:
    """Best-effort word picker for click-to-fill Find text.

    Uses PyMuPDF's word extraction and returns the word whose bbox contains the
    point, or the nearest word within a small tolerance.
    """

    try:
        import fitz  # PyMuPDF

        p = fitz.Point(float(x), float(y))
        words = page.get_text("words") or []
        best = ""
        best_dist = 1e18
        for w in words:
            if len(w) < 5:
                continue
            rect = fitz.Rect(float(w[0]), float(w[1]), float(w[2]), float(w[3]))
            text = str(w[4] or "").strip()
            if not text:
                continue
            if rect.contains(p):
                return text
            # Nearest word within tolerance.
            cx = (rect.x0 + rect.x1) / 2.0
            cy = (rect.y0 + rect.y1) / 2.0
            dx = float(p.x) - float(cx)
            dy = float(p.y) - float(cy)
            d2 = dx * dx + dy * dy
            if d2 < best_dist:
                best_dist = d2
                best = text

        # Only return nearest if reasonably close (about ~12pt).
        if best and best_dist <= (12.0 * 12.0):
            return best
        return ""
    except Exception:  # noqa: BLE001
        return ""


@app.post("/api/preview-pick-text")
async def api_preview_pick_text(
    sessionId: str = Form(...),
    pageNumber: int = Form(...),
    x: float = Form(...),
    y: float = Form(...),
    scale: float = Form(1.2),
):
    """Pick the word at a preview image coordinate.

    Frontend provides (x,y) in *image pixels* for the rendered preview scale.
    We convert back to PDF coords by dividing by scale.
    """

    _preview_gc()
    entry = _preview_store.get(sessionId)
    if not entry:
        raise HTTPException(status_code=404, detail="Preview session not found")

    _ts, pdf_bytes, page_count = entry
    if pageNumber < 1 or pageNumber > page_count:
        raise HTTPException(status_code=400, detail=f"pageNumber must be between 1 and {page_count}")

    try:
        import fitz  # PyMuPDF

        s = _clamp_scale(scale)
        px = float(x) / float(s)
        py = float(y) / float(s)

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc.load_page(pageNumber - 1)
        text = _pick_word_at_point(page, x=px, y=py)
        doc.close()
        return {"text": text}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Failed to pick text") from exc


# New route used by the UI
@app.get("/api/preview-page/{preview_id}/{page_number}")
def api_preview_page(preview_id: str, page_number: int, scale: float = 1.2):
    return _render_preview_page(preview_id, page_number, scale=scale)


# Backwards-compatible route (older UI)
@app.get("/api/preview-session/{preview_id}/page/{page_number}")
def api_preview_page_legacy(preview_id: str, page_number: int, scale: float = 1.2):
    return _render_preview_page(preview_id, page_number, scale=scale)


class _PreviewPickWordReq(BaseModel):
    sessionId: str
    pageNumber: int
    x: float
    y: float
    scale: float = 1.2


@app.post("/api/preview-pick-word")
def api_preview_pick_word(req: _PreviewPickWordReq):
    """Return the word at (x,y) in the rendered preview image.

    The frontend sends x/y in *image pixel coordinates* for the preview PNG.
    We convert back to page coordinates using the render scale.
    """

    _preview_gc()
    entry = _preview_store.get(req.sessionId)
    if not entry:
        raise HTTPException(status_code=404, detail="Preview session not found")

    ts, pdf_bytes, page_count = entry
    if req.pageNumber < 1 or req.pageNumber > int(page_count or 0):
        raise HTTPException(status_code=400, detail="Invalid pageNumber")

    try:
        import fitz  # PyMuPDF

        scale = float(req.scale) if req.scale else 1.2
        if scale <= 0.05 or scale > 6.0:
            scale = 1.2

        # Convert from image pixels back to page coordinates.
        px = float(req.x) / scale
        py = float(req.y) / scale

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc.load_page(req.pageNumber - 1)

        words = page.get_text("words") or []
        picked = ""
        best_dist = 1e18

        # First: direct hit.
        for w in words:
            if len(w) < 5:
                continue
            rect = fitz.Rect(float(w[0]), float(w[1]), float(w[2]), float(w[3]))
            rect = rect + (-1.0, -1.0, 1.0, 1.0)  # small tolerance
            if rect.contains(fitz.Point(px, py)):
                picked = str(w[4] or "").strip()
                break

        # Fallback: nearest word within radius.
        if not picked:
            for w in words:
                if len(w) < 5:
                    continue
                rect = fitz.Rect(float(w[0]), float(w[1]), float(w[2]), float(w[3]))
                cx = float(rect.x0 + rect.width / 2.0)
                cy = float(rect.y0 + rect.height / 2.0)
                dx = cx - px
                dy = cy - py
                d2 = dx * dx + dy * dy
                if d2 < best_dist:
                    best_dist = d2
                    picked = str(w[4] or "").strip()

            # Ignore extremely far clicks.
            if best_dist > (25.0 * 25.0):
                picked = ""

        doc.close()
        return {"text": picked}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Failed to pick word") from exc


@app.post("/api/find-replace")
async def api_find_replace(
    pdf: UploadFile = File(...),
    extraFonts: List[UploadFile] = File(default=[]),
    findText: str = Form(...),
    replaceText: str = Form(""),
    scope: str = Form("all"),
    fromPage: Optional[int] = Form(None),
    toPage: Optional[int] = Form(None),
    fontChoice: Optional[str] = Form(None),
):
    try:
        pdf_bytes = await pdf.read()

        extra_font_files: List[Tuple[str, bytes]] = []
        # Basic safety limits: avoid huge uploads.
        max_files = 20
        max_each = 5 * 1024 * 1024
        for f in (extraFonts or [])[:max_files]:
            try:
                name = (f.filename or "").strip()
                if not name.lower().endswith((".ttf", ".otf")):
                    continue
                data = await f.read()
                if not data:
                    continue
                if len(data) > max_each:
                    continue
                extra_font_files.append((name, data))
            except Exception:
                continue

        out, count, debug = pdf_ops.find_replace_text_with_count_and_debug(
            pdf_bytes,
            find_text=findText,
            replace_text=replaceText,
            scope=scope,
            from_page=fromPage,
            to_page=toPage,
            font_choice=fontChoice,
            extra_fonts=extra_font_files,
        )

        extra = {"X-Replacements": str(count)}
        if debug:
            # Keep headers short and ASCII-friendly.
            detected = (debug.get("detectedFont") or "")[:200]
            used = (debug.get("usedFont") or "")[:200]
            src = (debug.get("usedSource") or "")[:60]
            extra.update(
                {
                    "X-Detected-Font": detected,
                    "X-Used-Font": used,
                    "X-Used-Source": src,
                }
            )
        return _pdf_stream_response(
            out,
            filename="find-replace.pdf",
            extra_headers=extra,
        )
    except pdf_ops.PdfOpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/merge")
async def api_merge(pdfs: List[UploadFile] = File(...)):
    try:
        pdf_bytes_list = [await f.read() for f in pdfs]
        out = pdf_ops.merge_pdfs(pdf_bytes_list)
        return _pdf_stream_response(out, filename="merged.pdf")
    except pdf_ops.PdfOpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/reorder")
async def api_reorder(pdf: UploadFile = File(...), order: str = Form(...)):
    try:
        pdf_bytes = await pdf.read()
        out = pdf_ops.reorder_pages(pdf_bytes, order)
        return _pdf_stream_response(out, filename="reordered.pdf")
    except pdf_ops.PdfOpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/remove-pages")
async def api_remove_pages(pdf: UploadFile = File(...), pages: str = Form(...)):
    try:
        pdf_bytes = await pdf.read()
        out = pdf_ops.remove_pages(pdf_bytes, pages)
        return _pdf_stream_response(out, filename="pages-removed.pdf")
    except pdf_ops.PdfOpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
