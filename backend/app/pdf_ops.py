from __future__ import annotations

import contextvars
from dataclasses import dataclass
from io import BytesIO
import os
import re
import tempfile
from pathlib import Path
from typing import Dict
from typing import Iterable, List, Optional, Sequence, Set, Tuple

import fitz  # PyMuPDF


_EXTRA_FONT_DIRS: contextvars.ContextVar[Tuple[str, ...]] = contextvars.ContextVar(
    "pdf_editor_extra_font_dirs",
    default=(),
)


class PdfOpError(ValueError):
    pass


def _infer_bold_italic(fontname: str) -> Tuple[bool, bool]:
    name = (fontname or "").lower()
    is_bold = any(k in name for k in ("bold", "black", "heavy", "semibold", "demibold"))
    is_italic = any(k in name for k in ("italic", "oblique", "slanted"))

    # LaTeX Computer Modern font codes often appear as CMBX12/CMR12/CMTI10/CMSS12...
    # These may not include literal "bold"/"italic" in the name.
    if not is_bold and any(k in name for k in ("cmbx", "cmssbx", "cmssb", "cmb")):
        is_bold = True
    if not is_italic and any(k in name for k in ("cmti", "cmsl")):
        is_italic = True

    return is_bold, is_italic


@dataclass(frozen=True)
class _SpanStyle:
    fontname: Optional[str]
    fontsize: Optional[float]
    color: Optional[Tuple[float, float, float]]
    bold: bool = False
    italic: bool = False


def _map_font_to_base14(fontname: str) -> Optional[str]:
    """Map common font names to the closest Base-14 font.

    This improves visual consistency when the source PDF uses an embedded/subset
    font that cannot be directly reused by name.
    """

    if not fontname:
        return None

    name = fontname.lower()
    is_bold, is_italic = _infer_bold_italic(name)

    def pick(family: str) -> str:
        if family == "times":
            if is_bold and is_italic:
                return "Times-BoldItalic"
            if is_bold:
                return "Times-Bold"
            if is_italic:
                return "Times-Italic"
            return "Times-Roman"
        if family == "helv":
            if is_bold and is_italic:
                return "Helvetica-BoldOblique"
            if is_bold:
                return "Helvetica-Bold"
            if is_italic:
                return "Helvetica-Oblique"
            return "Helvetica"
        if family == "cour":
            if is_bold and is_italic:
                return "Courier-BoldOblique"
            if is_bold:
                return "Courier-Bold"
            if is_italic:
                return "Courier-Oblique"
            return "Courier"
        return "Helvetica"

    # Computer Modern (LaTeX) fonts: map to closest Base-14 family.
    # CMR/CMBX/CMTI/CMSL are serif; CMSS is sans; CMTT is mono.
    if any(k in name for k in ("cmr", "cmbx", "cmti", "cmsl", "cmu")):
        # Force bold/italic based on CM codes.
        if "cmbx" in name:
            is_bold = True
        if any(k in name for k in ("cmti", "cmsl")):
            is_italic = True
        return pick("times")
    if "cmss" in name:
        if any(k in name for k in ("cmssbx", "cmssb")):
            is_bold = True
        return pick("helv")
    if "cmtt" in name:
        return pick("cour")

    # Times-like
    if "times" in name or "timenewroman" in name or "timesnewroman" in name:
        return pick("times")

    # Courier-like (monospace)
    if "courier" in name or "consolas" in name or "monospace" in name:
        return pick("cour")

    # Helvetica-like (most sans fonts)
    if any(k in name for k in ("helvetica", "arial", "calibri", "verdana", "tahoma", "sans")):
        return pick("helv")

    return None


def _normalize_fontname(fontname: str) -> str:
    name = (fontname or "").strip()
    if name.startswith("/"):
        name = name[1:]
    # Strip subset prefix like "ABCDEE+TimesNewRomanPSMT"
    if "+" in name:
        name = name.split("+", 1)[1]
    return name.strip().lower()


def _try_windows_fontfile(fontname: str, *, bold: Optional[bool] = None, italic: Optional[bool] = None) -> Optional[str]:
    """Best-effort mapping from a PDF font name to a Windows font file.

    This helps when the PDF font is not embedded / not extractable.
    """

    win_dir = os.environ.get("WINDIR")
    if not win_dir:
        return None
    fonts_dir = os.path.join(win_dir, "Fonts")
    if not os.path.isdir(fonts_dir):
        return None

    name = (fontname or "").lower()
    inferred_bold, inferred_italic = _infer_bold_italic(name)
    is_bold = bool(bold) if bold is not None else inferred_bold
    is_italic = bool(italic) if italic is not None else inferred_italic

    def pick(candidates: List[str]) -> Optional[str]:
        for fname in candidates:
            path = os.path.join(fonts_dir, fname)
            if os.path.isfile(path):
                return path
        return None

    key = (is_bold, is_italic)

    # Prefer exact family if we can infer it.
    if "calibri" in name:
        table = {
            (False, False): ["calibri.ttf"],
            (True, False): ["calibrib.ttf"],
            (False, True): ["calibrii.ttf"],
            (True, True): ["calibriz.ttf"],
        }
        got = pick(table[key])
        if got:
            return got

    if "cambria" in name:
        table = {
            (False, False): ["cambria.ttc", "cambria.ttf"],
            (True, False): ["cambriab.ttf"],
            (False, True): ["cambriai.ttf"],
            (True, True): ["cambriaz.ttf"],
        }
        got = pick(table[key])
        if got:
            return got

    if "georgia" in name:
        table = {
            (False, False): ["georgia.ttf"],
            (True, False): ["georgiab.ttf"],
            (False, True): ["georgiai.ttf"],
            (True, True): ["georgiaz.ttf"],
        }
        got = pick(table[key])
        if got:
            return got

    if "garamond" in name:
        table = {
            (False, False): ["gara.ttf", "garamond.ttf"],
            (True, False): ["garabd.ttf", "garamondb.ttf"],
            (False, True): ["garait.ttf", "garamondi.ttf"],
            (True, True): ["garabi.ttf", "garamondz.ttf"],
        }
        got = pick(table[key])
        if got:
            return got

    if "palatino" in name:
        table = {
            (False, False): ["pala.ttf"],
            (True, False): ["palab.ttf"],
            (False, True): ["palai.ttf"],
            (True, True): ["palabi.ttf"],
        }
        got = pick(table[key])
        if got:
            return got

    if "constantia" in name:
        table = {
            (False, False): ["constan.ttf"],
            (True, False): ["constanb.ttf"],
            (False, True): ["constani.ttf"],
            (True, True): ["constanz.ttf"],
        }
        got = pick(table[key])
        if got:
            return got

    if "bookman" in name or "bookos" in name:
        table = {
            (False, False): ["bookos.ttf"],
            (True, False): ["bookosb.ttf"],
            (False, True): ["bookosi.ttf"],
            (True, True): ["bookosbi.ttf"],
        }
        got = pick(table[key])
        if got:
            return got

    if "goudy" in name or "goudos" in name:
        table = {
            (False, False): ["goudos.ttf"],
            (True, False): ["goudosb.ttf"],
            (False, True): ["goudosi.ttf"],
            (True, True): ["goudosbi.ttf"],
        }
        got = pick(table[key])
        if got:
            return got

    if "centaur" in name:
        got = pick(["centaur.ttf"])
        if got:
            return got

    if "century" in name:
        got = pick(["century.ttf"])
        if got:
            return got

    # Palatino / Book Antiqua (common in resume headers)
    if any(k in name for k in ("palatino", "palatinolinotype", "bookantiqua", "book antiqua")):
        table = {
            (False, False): ["pala.ttf"],
            (True, False): ["palab.ttf"],
            (False, True): ["palai.ttf"],
            (True, True): ["palabi.ttf"],
        }
        got = pick(table[key])
        if got:
            return got

    # Constantia
    if "constantia" in name:
        table = {
            (False, False): ["constan.ttf"],
            (True, False): ["constanb.ttf"],
            (False, True): ["constani.ttf"],
            (True, True): ["constanz.ttf"],
        }
        got = pick(table[key])
        if got:
            return got

    # Bookman Old Style
    if any(k in name for k in ("bookman", "bookmanoldstyle", "bookos")):
        table = {
            (False, False): ["BOOKOS.TTF"],
            (True, False): ["BOOKOSB.TTF"],
            (False, True): ["BOOKOSI.TTF"],
            (True, True): ["BOOKOSBI.TTF"],
        }
        got = pick(table[key])
        if got:
            return got

    # Goudy Old Style
    if "goudy" in name:
        table = {
            (False, False): ["GOUDOS.TTF"],
            (True, False): ["GOUDOSB.TTF"],
            (False, True): ["GOUDOSI.TTF"],
            (True, True): ["GOUDOSBI.TTF"],
        }
        got = pick(table[key])
        if got:
            return got

    # Classify family.
    family = "sans"
    if any(k in name for k in ("courier", "consola", "monospace", "mono", "cmtt")):
        family = "mono"
    elif any(
        k in name
        for k in (
            "times",
            "roman",
            "serif",
            "georgia",
            "garamond",
            "cambria",
            "palatino",
            "constantia",
            "bookman",
            "goudy",
            "centaur",
            "century",
            "palatino",
            "bookman",
            "nimbus",
            "liberationserif",
            "minion",
            "baskerville",
            "caslon",
            # Computer Modern (LaTeX)
            "cmr",
            "cmbx",
            "cmti",
            "cmsl",
            "cmu",
        )
    ):
        family = "serif"
    elif any(k in name for k in ("helvetica", "arial", "verdana", "tahoma", "sans", "cmss")):
        family = "sans"

    # Known good Windows font filenames (most machines have these).
    serif = {
        (False, False): ["times.ttf"],
        (True, False): ["timesbd.ttf"],
        (False, True): ["timesi.ttf"],
        (True, True): ["timesbi.ttf"],
    }
    sans = {
        (False, False): ["arial.ttf"],
        (True, False): ["arialbd.ttf"],
        (False, True): ["ariali.ttf"],
        (True, True): ["arialbi.ttf"],
    }
    mono = {
        (False, False): ["consola.ttf", "cour.ttf"],
        (True, False): ["consolab.ttf", "courbd.ttf"],
        (False, True): ["consolai.ttf", "couri.ttf"],
        (True, True): ["consolaz.ttf", "courbi.ttf"],
    }

    table = sans if family == "sans" else serif if family == "serif" else mono
    candidates = table[key]
    for fname in candidates:
        path = os.path.join(fonts_dir, fname)
        if os.path.isfile(path):
            return path
    return None


def _windows_fontfile_for_choice(choice: str, *, bold: bool, italic: bool) -> Optional[str]:
    """Return a Windows font file path for an explicit user choice."""

    c = (choice or "").strip().lower()
    if not c or c == "auto":
        return None

    # On non-Windows (e.g., Render Linux), treat these as family hints.
    if os.name != "nt":
        # Prefer the actual named family if the font is bundled.
        if c in {"times", "cambria", "georgia", "garamond", "palatino", "constantia", "arial", "calibri"}:
            return _try_system_fontfile(c, bold=bold, italic=italic)
        return _try_system_fontfile(c, bold=bold, italic=italic)

    # Feed into the existing mapping logic by crafting a name.
    parts = [c]
    if bold:
        parts.append("bold")
    if italic:
        parts.append("italic")
    return _try_windows_fontfile(" ".join(parts), bold=bold, italic=italic)


def _is_common_windows_font_family(fontname: str) -> bool:
    """Heuristic: does the PDF font name look like a standard Windows family?"""

    n = _normalize_fontname(fontname or "")
    if not n:
        return False
    return any(
        k in n
        for k in (
            "calibri",
            "cambria",
            "georgia",
            "garamond",
            "arial",
            "helvetica",
            "times",
            "timesnewroman",
            "timenewroman",
            "newroman",
        )
    )


def _baseline_point(rect: fitz.Rect, *, fontsize: float, fontname: str, fontfile: Optional[str]) -> fitz.Point:
    # Compute baseline using font descender metrics when possible.
    try:
        f = fitz.Font(fontfile=fontfile) if fontfile else fitz.Font(fontname=fontname)
        desc = float(getattr(f, "descender", -0.2))
        y = float(rect.y1) + desc * float(fontsize)
        # clamp into rect
        if y < rect.y0:
            y = float(rect.y0) + float(fontsize)
        if y > rect.y1:
            y = float(rect.y1) - 1.0
        return fitz.Point(rect.x0, y)
    except Exception:  # noqa: BLE001
        baseline_pad = max(1.0, float(fontsize) * 0.18)
        return fitz.Point(rect.x0, rect.y1 - baseline_pad)


def _get_font_obj(
    *,
    fontname: str,
    fontfile: Optional[str],
    cache: Dict[Tuple[str, str], fitz.Font],
) -> fitz.Font:
    """Get a cached fitz.Font for measuring/metrics.

    Keyed by (fontfile, fontname). If fontfile is provided it dominates.
    """

    key = (fontfile or "", (fontname or "").strip() or "helv")
    got = cache.get(key)
    if got is not None:
        return got

    f = fitz.Font(fontfile=fontfile) if fontfile else fitz.Font(fontname=key[1])
    cache[key] = f
    return f


def _replacement_introduces_new_chars(original_text: str, replacement_text: str) -> bool:
    orig_chars = set((original_text or ""))
    for ch in replacement_text or "":
        if ch not in orig_chars:
            return True
    return False


def _replace_case_insensitive(text: str, find_text: str, replace_text: str) -> str:
    if not text or not find_text:
        return text
    try:
        pattern = re.compile(re.escape(find_text), re.IGNORECASE)
        return pattern.sub(replace_text, text)
    except Exception:  # noqa: BLE001
        return text.replace(find_text, replace_text)


def _match_replacement_case(original: str, replacement: str) -> str:
    """Best-effort casing match between original and replacement.

    Many resume headings are ALL CAPS. Inserting mixed-case text into an ALL
    CAPS heading makes it look like a different font even if the font face is
    the same.
    """

    if not replacement:
        return replacement

    o = (original or "").strip()
    if not o:
        return replacement

    # Consider only letters for case heuristics.
    letters = [ch for ch in o if ch.isalpha()]
    if not letters:
        return replacement

    if all(ch.isupper() for ch in letters):
        return replacement.upper()
    if all(ch.islower() for ch in letters):
        return replacement.lower()

    # Title-case heuristic: each word starts upper and remaining letters are lower.
    words = [w for w in re.split(r"\s+", o) if w]
    if words:
        def is_title_word(w: str) -> bool:
            w_letters = [ch for ch in w if ch.isalpha()]
            if not w_letters:
                return True
            first = next((ch for ch in w if ch.isalpha()), "")
            if not first:
                return True
            return first.isupper() and all((not ch.isalpha()) or ch.islower() for ch in w[w.index(first) + 1 :])

        if all(is_title_word(w) for w in words):
            return replacement.title()

    return replacement


def _expand_rect_to_word(
    page: fitz.Page,
    rect: fitz.Rect,
    *,
    find_text: str,
    replace_text: str,
) -> Tuple[fitz.Rect, str, str]:
    """If match is within a word, expand to the full word bbox.

    This avoids leaving suffix/prefix fragments behind when the user searches
    for a substring like "arun" inside "Arunesh".
    """

    ft = (find_text or "").strip()
    if not ft or any(ch.isspace() for ch in ft):
        return rect, "", replace_text

    try:
        words = page.get_text("words") or []
    except Exception:  # noqa: BLE001
        return rect, "", replace_text

    ft_l = ft.lower()
    best_area = 0.0
    best_word: Optional[Tuple] = None

    for w in words:
        # words tuple layout: x0,y0,x1,y1,"word", block, line, word_no
        if len(w) < 5:
            continue
        word_text = str(w[4] or "")
        if not word_text:
            continue
        if ft_l not in word_text.lower():
            continue
        wrect = fitz.Rect(float(w[0]), float(w[1]), float(w[2]), float(w[3]))
        if not wrect.intersects(rect):
            continue
        inter = wrect & rect
        area = float(inter.get_area()) if inter else 0.0
        if area > best_area:
            best_area = area
            best_word = w

    if not best_word or best_area <= 0.0:
        return rect, "", replace_text

    wrect = fitz.Rect(float(best_word[0]), float(best_word[1]), float(best_word[2]), float(best_word[3]))
    word_text = str(best_word[4] or "")
    replaced = _replace_case_insensitive(word_text, ft, replace_text)

    # Slight padding helps ensure full redaction of tracked glyph fragments.
    h = max(1.0, float(wrect.height))
    pad_x = max(1.5, h * 0.10)
    pad_y = max(1.0, h * 0.06)
    wrect = fitz.Rect(float(wrect.x0) - pad_x, float(wrect.y0) - pad_y, float(wrect.x1) + pad_x, float(wrect.y1) + pad_y)
    return wrect, word_text, replaced


def _windows_font_path(filename: str) -> Optional[str]:
    win_dir = os.environ.get("WINDIR") or "C:\\Windows"
    path = os.path.join(win_dir, "Fonts", filename)
    return path if os.path.isfile(path) else None


def _first_existing(paths: Sequence[str]) -> Optional[str]:
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def _bundled_fonts_dir() -> Optional[str]:
    """Optional directory for fonts shipped with the app.

    This is useful for Linux deployments (e.g., Render) where Windows fonts are
    not available.
    """

    try:
        d = Path(__file__).resolve().parent / "fonts"
        return str(d) if d.is_dir() else None
    except Exception:  # noqa: BLE001
        return None


def _custom_fonts_dirs() -> List[str]:
    return [d for d, _src in _custom_fonts_dirs_with_source()]


def _custom_fonts_dirs_with_source() -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []

    # Request-scoped uploaded fonts (highest priority).
    for d in list(_EXTRA_FONT_DIRS.get() or ()):  # type: ignore[arg-type]
        if d:
            pairs.append((d, "uploaded"))

    env_dir = (os.getenv("PDF_EDITOR_FONTS_DIR") or "").strip()
    if env_dir:
        pairs.append((env_dir, "custom"))

    bundled = _bundled_fonts_dir()
    if bundled:
        pairs.append((bundled, "bundled"))

    # de-dup while preserving order
    seen: Set[str] = set()
    out: List[Tuple[str, str]] = []
    for d, src in pairs:
        if d and d not in seen:
            seen.add(d)
            out.append((d, src))
    return out


def _score_font_filename(filename: str, *, family_key: str, bold: bool, italic: bool) -> int:
    f = (filename or "").lower()
    score = 0

    if family_key and family_key in f:
        score += 10

    want_bold = bool(bold)
    want_italic = bool(italic)

    is_bold_name = any(k in f for k in ("bold", "bd", "black", "heavy", "semibold", "demibold"))
    is_italic_name = any(k in f for k in ("italic", "oblique", "slanted"))
    is_regular_name = any(k in f for k in ("regular", "book", "roman", "medium"))

    if want_bold:
        score += 4 if is_bold_name else -1
    else:
        score += 1 if is_regular_name and not is_bold_name else 0
        score -= 2 if is_bold_name else 0

    if want_italic:
        score += 4 if is_italic_name else -1
    else:
        score -= 2 if is_italic_name else 0

    # Prefer actual font files.
    if f.endswith(".ttf") or f.endswith(".otf"):
        score += 1

    return score


def _family_key_from_norm(norm: str) -> Optional[str]:
    n = (norm or "").lower()
    if not n:
        return None

    # Handle common family names. (Computer Modern is handled separately.)
    if any(k in n for k in ("timesnewroman", "timenewroman", "times")):
        return "times"
    if any(k in n for k in ("arial", "helvetica")):
        return "arial"
    if "calibri" in n:
        return "calibri"
    if "cambria" in n:
        return "cambria"
    if "georgia" in n:
        return "georgia"
    if "garamond" in n:
        return "garamond"
    if "palatino" in n:
        return "palatino"
    if "constantia" in n:
        return "constantia"
    if any(k in n for k in ("dejavu-serif", "dejavuserif")):
        return "dejavuserif"
    if any(k in n for k in ("dejavu-sans", "dejavusans")):
        return "dejavusans"
    if "dejavu" in n:
        return "dejavu"
    if any(k in n for k in ("liberation-serif", "liberationserif")):
        return "liberationserif"
    if any(k in n for k in ("liberation-sans", "liberationsans")):
        return "liberationsans"
    if "liberation" in n:
        return "liberation"
    if "noto" in n:
        return "noto"
    if "courier" in n:
        return "courier"
    if any(k in n for k in ("courier", "consolas", "monospace", "mono")):
        return "mono"
    return None


def _try_custom_fontfile_with_source(fontname: str, *, bold: bool, italic: bool) -> Tuple[Optional[str], str]:
    """Try to find a matching font file from user-provided directories.

    Users can bundle fonts in backend/app/fonts/ or point PDF_EDITOR_FONTS_DIR
    to a directory. This is the only way to get an *exact* match on Render/Linux
    for proprietary fonts (Calibri/Cambria/etc.).
    """

    norm = _normalize_fontname(fontname)
    if not norm:
        return None, ""

    # Computer Modern/Latin Modern have a dedicated locator.
    if any(k in norm for k in ("cmr", "cmbx", "cmti", "cmsl", "cmss", "cmtt", "cmu")):
        got = _try_computer_modern_fontfile(bold=bold, italic=italic)
        return got, "uploaded" if got and any(got.startswith(d + os.sep) for d, s in _custom_fonts_dirs_with_source() if s == "uploaded") else "bundled" if got and any(got.startswith(d + os.sep) for d, s in _custom_fonts_dirs_with_source() if s == "bundled") else "custom" if got and any(got.startswith(d + os.sep) for d, s in _custom_fonts_dirs_with_source() if s == "custom") else ""

    family_key = _family_key_from_norm(norm)
    if not family_key:
        # Fall back to matching arbitrary font families by their normalized name.
        # This helps when users upload fonts that aren't covered by our known list.
        family_key = norm
        if len(family_key) < 3:
            return None, ""

    best_score = 0
    best_path: Optional[str] = None
    best_src = ""
    for d, src in _custom_fonts_dirs_with_source():
        try:
            for fname in os.listdir(d):
                if not fname.lower().endswith((".ttf", ".otf")):
                    continue
                s = _score_font_filename(fname, family_key=family_key, bold=bold, italic=italic)
                if s <= best_score:
                    continue
                candidate = os.path.join(d, fname)
                if os.path.isfile(candidate):
                    best_score = s
                    best_path = candidate
                    best_src = src
        except Exception:  # noqa: BLE001
            continue

    return best_path, best_src


def _try_custom_fontfile(fontname: str, *, bold: bool, italic: bool) -> Optional[str]:
    path, _src = _try_custom_fontfile_with_source(fontname, bold=bold, italic=italic)
    return path


def _try_computer_modern_fontfile(*, bold: bool, italic: bool) -> Optional[str]:
    """Try to locate an installed Computer Modern / Latin Modern font file.

    These fonts are not installed by default on Windows, but may appear after
    installing MiKTeX/TeX Live or the CMU/Latin Modern font packages.
    """

    # If these are present, we can match LaTeX PDFs much more closely than Times.
    table = {
        (False, False): [
            "cmunrm.ttf",
            "cmunr.ttf",
            "lmroman10-regular.otf",
            "lmroman10-regular.ttf",
        ],
        (True, False): [
            "cmunbx.ttf",
            "cmunb.ttf",
            "lmroman10-bold.otf",
            "lmroman10-bold.ttf",
        ],
        (False, True): [
            "cmunit.ttf",
            "cmunri.ttf",
            "lmroman10-italic.otf",
            "lmroman10-italic.ttf",
        ],
        (True, True): [
            "cmunbi.ttf",
            "cmunbxo.ttf",
            "lmroman10-bolditalic.otf",
            "lmroman10-bolditalic.ttf",
        ],
    }

    candidates = table[(bool(bold), bool(italic))]

    search_dirs: List[str] = [d for d, _src in _custom_fonts_dirs_with_source()]

    # Typical Linux font locations for TeX/Latin Modern/CMU (may not exist).
    search_dirs.extend(
        [
            "/usr/share/texmf/fonts/opentype/public/lm",
            "/usr/share/texmf/fonts/truetype/public/cm-unicode",
            "/usr/share/fonts/opentype/latin-modern",
            "/usr/share/fonts/truetype/cmu",
        ]
    )

    # Windows Fonts folder.
    win_dir = os.environ.get("WINDIR")
    if win_dir:
        search_dirs.append(os.path.join(win_dir, "Fonts"))

    paths: List[str] = []
    for d in search_dirs:
        for fname in candidates:
            paths.append(os.path.join(d, fname))

    # Also allow candidates to be passed as absolute paths.
    paths.extend([p for p in candidates if os.path.isabs(p)])

    return _first_existing(paths)


def _try_system_fontfile_native(fontname: str, *, bold: Optional[bool] = None, italic: Optional[bool] = None) -> Optional[str]:
    """Best-effort mapping of a font name to a local system font file.

    On Windows this maps to common fonts in C:\\Windows\\Fonts.
    On Linux (e.g. Render) this maps to DejaVu/Liberation font files.
    """

    raw = (fontname or "").lower()
    norm = _normalize_fontname(fontname)
    if not norm:
        return None

    inferred_bold, inferred_italic = _infer_bold_italic(raw)
    is_bold = bool(bold) if bold is not None else inferred_bold
    is_italic = bool(italic) if italic is not None else inferred_italic

    # LaTeX Computer Modern (CM*) fonts: prefer actual CMU/Latin Modern font files
    # if installed; otherwise fall back to Times New Roman.
    if any(k in norm for k in ("cmr", "cmbx", "cmti", "cmsl", "cmu")):
        cm = _try_computer_modern_fontfile(bold=is_bold, italic=is_italic)
        if cm:
            return cm

    if os.name == "nt":
        # Times New Roman family
        if any(k in norm for k in ("times", "newroman", "timesnewroman")):
            if is_bold and is_italic:
                return _windows_font_path("timesbi.ttf")
            if is_bold:
                return _windows_font_path("timesbd.ttf")
            if is_italic:
                return _windows_font_path("timesi.ttf")
            return _windows_font_path("times.ttf")

        # Arial family
        if any(k in norm for k in ("arial", "helvetica")):
            if is_bold and is_italic:
                return _windows_font_path("arialbi.ttf")
            if is_bold:
                return _windows_font_path("arialbd.ttf")
            if is_italic:
                return _windows_font_path("ariali.ttf")
            return _windows_font_path("arial.ttf")

        # Calibri family (common in resumes)
        if "calibri" in norm:
            if is_bold and is_italic:
                return _windows_font_path("calibriz.ttf")
            if is_bold:
                return _windows_font_path("calibrib.ttf")
            if is_italic:
                return _windows_font_path("calibrii.ttf")
            return _windows_font_path("calibri.ttf")

        return None

    # Linux / Render: pick commonly available fonts.
    serif_table = {
        (False, False): [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        ],
        (True, False): [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        ],
        (False, True): [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Italic.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
        ],
        (True, True): [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-BoldItalic.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf",
        ],
    }
    sans_table = {
        (False, False): [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ],
        (True, False): [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ],
        (False, True): [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Italic.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
        ],
        (True, True): [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-BoldItalic.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf",
        ],
    }
    mono_table = {
        (False, False): [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        ],
        (True, False): [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        ],
        (False, True): [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationMono-Italic.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Italic.ttf",
        ],
        (True, True): [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-BoldOblique.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationMono-BoldItalic.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-BoldItalic.ttf",
        ],
    }

    fam = "sans"
    if any(k in norm for k in ("courier", "mono", "monospace", "cmtt")):
        fam = "mono"
    elif any(
        k in norm
        for k in (
            "times",
            "serif",
            "roman",
            "georgia",
            "garamond",
            "cambria",
            "palatino",
            "constantia",
            "cmr",
            "cmbx",
            "cmti",
            "cmsl",
            "cmu",
        )
    ):
        fam = "serif"

    key = (bool(is_bold), bool(is_italic))
    table = serif_table if fam == "serif" else mono_table if fam == "mono" else sans_table
    return _first_existing(table[key])


def _try_system_fontfile_with_source(
    fontname: str,
    *,
    bold: Optional[bool] = None,
    italic: Optional[bool] = None,
) -> Tuple[Optional[str], str]:
    """Resolve a font file and return (path, source_label)."""

    raw = (fontname or "").lower()
    inferred_bold, inferred_italic = _infer_bold_italic(raw)
    is_bold = bool(bold) if bold is not None else inferred_bold
    is_italic = bool(italic) if italic is not None else inferred_italic

    custom, custom_src = _try_custom_fontfile_with_source(fontname, bold=is_bold, italic=is_italic)
    if custom:
        return custom, custom_src or "custom"

    return _try_system_fontfile_native(fontname, bold=bold, italic=italic), "system"


def _try_system_fontfile(fontname: str, *, bold: Optional[bool] = None, italic: Optional[bool] = None) -> Optional[str]:
    path, _ = _try_system_fontfile_with_source(fontname, bold=bold, italic=italic)
    return path


def _is_computer_modern_font(fontname: str) -> bool:
    n = _normalize_fontname(fontname or "")
    if not n:
        return False
    return n.startswith("cm") or any(k in n for k in ("cmr", "cmbx", "cmti", "cmsl", "cmss", "cmtt", "cmu"))


def _try_extract_embedded_fontfile(
    doc: fitz.Document,
    page: fitz.Page,
    fontname: str,
    *,
    tmpdir: str,
) -> Optional[str]:
    """Try to extract an embedded font program and return a font file path.

    Returns None if the font is not embedded (Base-14) or cannot be extracted.
    """

    want_norm = _normalize_fontname(fontname)
    if not want_norm:
        return None

    try:
        fonts = page.get_fonts(full=True)
    except Exception:  # noqa: BLE001
        return None

    candidate_xrefs: List[int] = []
    for entry in fonts or []:
        # Layout (observed): (xref, ext, type, basefont, name, encoding, stream_xref?)
        try:
            xref = int(entry[0])
        except Exception:  # noqa: BLE001
            continue

        basefont = str(entry[3]) if len(entry) > 3 and entry[3] else ""
        name = str(entry[4]) if len(entry) > 4 and entry[4] else ""

        base_norm = _normalize_fontname(basefont)
        name_norm = _normalize_fontname(name)

        # Prefer exact match, but also allow contains match (some PDFs vary naming).
        if base_norm == want_norm or name_norm == want_norm:
            candidate_xrefs.insert(0, xref)
        elif want_norm and (want_norm in base_norm or want_norm in name_norm or base_norm in want_norm or name_norm in want_norm):
            candidate_xrefs.append(xref)

    if not candidate_xrefs:
        return None

    # Pick the first candidate that actually yields a usable font buffer.
    for xref in candidate_xrefs:
        try:
            extracted = doc.extract_font(xref)
        except Exception:  # noqa: BLE001
            continue

        try:
            ext = extracted[1]
            buf = extracted[3]
        except Exception:  # noqa: BLE001
            continue

        if not buf:
            continue

        safe_ext = str(ext) if isinstance(ext, str) and ext and ext != "n/a" else "bin"
        out_path = os.path.join(tmpdir, f"font_{xref}.{safe_ext}")
        try:
            with open(out_path, "wb") as f:
                f.write(buf)
        except Exception:  # noqa: BLE001
            continue
        return out_path

    return None


def _parse_positive_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except Exception as exc:  # noqa: BLE001
        raise PdfOpError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise PdfOpError(f"{name} must be >= 1")
    return parsed


def parse_page_ranges(pages: str, *, page_count: int) -> Set[int]:
    """Parse '1,3,5-7' into a set of 0-based page indices."""

    if page_count <= 0:
        raise PdfOpError("PDF has no pages")

    cleaned = pages.strip()
    if not cleaned:
        raise PdfOpError("pages is required")

    indices: Set[int] = set()
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            start_s, end_s = [s.strip() for s in part.split("-", 1)]
            start = _parse_positive_int(start_s, name="range start")
            end = _parse_positive_int(end_s, name="range end")
            if end < start:
                raise PdfOpError("range end must be >= range start")
            for one_based in range(start, end + 1):
                idx = one_based - 1
                if idx < 0 or idx >= page_count:
                    raise PdfOpError(f"page {one_based} is out of bounds (1..{page_count})")
                indices.add(idx)
        else:
            one_based = _parse_positive_int(part, name="page")
            idx = one_based - 1
            if idx < 0 or idx >= page_count:
                raise PdfOpError(f"page {one_based} is out of bounds (1..{page_count})")
            indices.add(idx)

    return indices


def parse_reorder(order: str, *, page_count: int) -> List[int]:
    """Parse '3,1,2' into a list of 0-based page indices."""

    if page_count <= 0:
        raise PdfOpError("PDF has no pages")

    cleaned = order.strip()
    if not cleaned:
        raise PdfOpError("order is required")

    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    if len(parts) != page_count:
        raise PdfOpError(f"order must specify exactly {page_count} pages")

    result: List[int] = []
    seen: Set[int] = set()
    for part in parts:
        one_based = _parse_positive_int(part, name="order item")
        idx = one_based - 1
        if idx < 0 or idx >= page_count:
            raise PdfOpError(f"order contains out-of-bounds page {one_based} (1..{page_count})")
        if idx in seen:
            raise PdfOpError("order contains duplicates")
        seen.add(idx)
        result.append(idx)

    return result


def merge_pdfs(pdf_bytes_list: Sequence[bytes]) -> bytes:
    if not pdf_bytes_list:
        raise PdfOpError("At least one PDF is required")

    out = fitz.open()
    for pdf_bytes in pdf_bytes_list:
        src = fitz.open(stream=pdf_bytes, filetype="pdf")
        out.insert_pdf(src)
        src.close()

    data = out.tobytes()
    out.close()
    return data


def reorder_pages(pdf_bytes: bytes, order: str) -> bytes:
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = src.page_count
    page_order = parse_reorder(order, page_count=page_count)

    out = fitz.open()
    for idx in page_order:
        out.insert_pdf(src, from_page=idx, to_page=idx)

    data = out.tobytes()
    out.close()
    src.close()
    return data


def remove_pages(pdf_bytes: bytes, pages: str) -> bytes:
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = src.page_count
    to_remove = parse_page_ranges(pages, page_count=page_count)

    out = fitz.open()
    for idx in range(page_count):
        if idx in to_remove:
            continue
        out.insert_pdf(src, from_page=idx, to_page=idx)

    data = out.tobytes()
    out.close()
    src.close()
    return data


def _estimate_font_size(rect: fitz.Rect) -> float:
    # A simple heuristic: text height tends to be ~70-85% of bounding box height
    h = max(1.0, float(rect.height))
    return max(6.0, min(48.0, h * 0.78))


def _merge_close_rects(rects: Sequence[fitz.Rect]) -> List[fitz.Rect]:
    """Merge overlapping / very close rects into bigger ones.

    Some PDFs (especially headings) draw text as many small spans/glyphs.
    `search_for` may return multiple rects for what visually looks like one hit.
    Replacing each rect separately can cause overlaps and apparent "missing" letters.
    """

    if not rects:
        return []

    cleaned: List[fitz.Rect] = [fitz.Rect(r) for r in rects if r]
    cleaned.sort(key=lambda r: (float(r.y0), float(r.x0)))

    merged: List[fitz.Rect] = []
    for r in cleaned:
        if not merged:
            merged.append(r)
            continue

        last = merged[-1]

        # Same line-ish if vertical overlap is significant OR vertical centers
        # are close (handles slight misalignment).
        v_overlap = max(0.0, min(float(last.y1), float(r.y1)) - max(float(last.y0), float(r.y0)))
        min_h = max(1.0, min(float(last.height), float(r.height)))
        same_line = v_overlap / min_h >= 0.3

        # Also check if centers are close vertically.
        c1 = (float(last.y0) + float(last.y1)) / 2.0
        c2 = (float(r.y0) + float(r.y1)) / 2.0
        if abs(c1 - c2) < min_h * 0.8:
            same_line = True

        # If rects are on the same line, always merge them (handles wide
        # letter-spacing in headings where gaps can be very large).
        if last.intersects(r) or same_line:
            merged[-1] = fitz.Rect(
                min(float(last.x0), float(r.x0)),
                min(float(last.y0), float(r.y0)),
                max(float(last.x1), float(r.x1)),
                max(float(last.y1), float(r.y1)),
            )
        else:
            merged.append(r)

    # Add small padding to each merged rect to cover glyph fragments that may
    # fall slightly outside the search rectangles.
    padded: List[fitz.Rect] = []
    for r in merged:
        h = float(r.height)
        pad_x = max(2.0, h * 0.15)
        pad_y = max(1.0, h * 0.08)
        padded.append(fitz.Rect(
            float(r.x0) - pad_x,
            float(r.y0) - pad_y,
            float(r.x1) + pad_x,
            float(r.y1) + pad_y,
        ))
    return padded


def _int_to_rgb01(color: int) -> Tuple[float, float, float]:
    # PyMuPDF span color is typically an int: 0xRRGGBB.
    r = (color >> 16) & 0xFF
    g = (color >> 8) & 0xFF
    b = color & 0xFF
    return (r / 255.0, g / 255.0, b / 255.0)


def _extract_spans(text_dict: dict) -> List[Tuple[fitz.Rect, _SpanStyle]]:
    spans: List[Tuple[fitz.Rect, _SpanStyle]] = []

    for block in text_dict.get("blocks", []) or []:
        for line in block.get("lines", []) or []:
            for span in line.get("spans", []) or []:
                bbox = span.get("bbox")
                if not bbox:
                    continue

                fontname = span.get("font")
                fontsize = span.get("size")
                color = span.get("color")
                flags = span.get("flags")

                name_s = str(fontname) if isinstance(fontname, str) and fontname else ""
                inferred_bold, inferred_italic = _infer_bold_italic(name_s)
                try:
                    fval = int(flags) if flags is not None else 0
                except Exception:  # noqa: BLE001
                    fval = 0
                # PyMuPDF span flags commonly use bit 16 for bold, bit 2 for italic.
                is_bold = inferred_bold or ((fval & 16) != 0)
                is_italic = inferred_italic or ((fval & 2) != 0)

                rgb = _int_to_rgb01(int(color)) if isinstance(color, int) else None
                try:
                    fontsize_f = float(fontsize) if fontsize is not None else None
                except Exception:  # noqa: BLE001
                    fontsize_f = None

                spans.append(
                    (
                        fitz.Rect(bbox),
                        _SpanStyle(
                            fontname=str(fontname) if isinstance(fontname, str) and fontname else None,
                            fontsize=fontsize_f,
                            color=rgb,
                            bold=bool(is_bold),
                            italic=bool(is_italic),
                        ),
                    )
                )

    return spans


def _pick_style_for_rect_from_spans(spans: Sequence[Tuple[fitz.Rect, _SpanStyle]], rect: fitz.Rect) -> _SpanStyle:
    """Pick the span style that best overlaps the given rect."""

    best_area = 0.0
    best_style: Optional[_SpanStyle] = None

    for span_rect, style in spans:
        # Fast reject.
        if not span_rect.intersects(rect):
            continue
        inter = span_rect & rect
        area = float(inter.get_area()) if inter else 0.0
        if area > best_area:
            best_area = area
            best_style = style

    return best_style if best_style and best_area > 0 else _SpanStyle(fontname=None, fontsize=None, color=None)


def _pick_style_for_rect_clip(page: fitz.Page, rect: fitz.Rect) -> _SpanStyle:
    """More reliable style picker using a clipped text extraction."""

    try:
        td = page.get_text("dict", clip=rect)
        spans = _extract_spans(td)
        return _pick_style_for_rect_from_spans(spans, rect)
    except Exception:  # noqa: BLE001
        return _SpanStyle(fontname=None, fontsize=None, color=None)


def _measure_text_width(
    text: str,
    *,
    fontsize: float,
    fontname: str,
    fontfile: Optional[str],
    font_cache: Optional[Dict[Tuple[str, str], fitz.Font]] = None,
) -> float:
    if fontfile:
        try:
            cache = font_cache if font_cache is not None else {}
            f = _get_font_obj(fontname=fontname, fontfile=fontfile, cache=cache)
            return float(f.text_length(text, fontsize=fontsize))
        except Exception:  # noqa: BLE001
            # Fall back to approximation.
            return float(fitz.get_text_length(text, fontname="helv", fontsize=fontsize))
    return float(fitz.get_text_length(text, fontname=fontname or "helv", fontsize=fontsize))


def _font_supports_text(
    text: str,
    *,
    fontsize: float,
    fontname: str,
    fontfile: Optional[str],
    font_cache: Optional[Dict[Tuple[str, str], fitz.Font]] = None,
) -> bool:
    """Best-effort glyph coverage check for font files.

    Extracted embedded fonts can be subset fonts. If they don't contain glyphs
    for some replacement characters, those characters may render blank.

    We check support by verifying that each non-space character maps to a
    glyph in the font (via fitz.Font.has_glyph). This is more reliable than
    measuring widths because missing glyphs may still report a non-zero width.
    """

    if not text:
        return True
    if not fontfile:
        return True

    for ch in text:
        if ch.isspace():
            continue

        cp = ord(ch)
        try:
            cache = font_cache if font_cache is not None else {}
            f = _get_font_obj(fontname=fontname or "helv", fontfile=fontfile, cache=cache)
        except Exception:  # noqa: BLE001
            return False

        try:
            has = f.has_glyph(cp)
        except Exception:  # noqa: BLE001
            # If we can't verify, assume unsupported to avoid blanks.
            return False
        if not has:
            return False
    return True


def _insert_text_distributed(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    *,
    fontname: str,
    fontfile: Optional[str],
    fontsize: float,
    color: Tuple[float, float, float],
    font_cache: Optional[Dict[Tuple[str, str], fitz.Font]] = None,
) -> None:
    """Insert text as individual characters distributed across rect width.

    This is a best-effort approximation for PDFs that render headings using
    custom tracking / glyph positioning (common in resume name headers).
    """

    if not text:
        return

    chars = list(text)
    if len(chars) <= 1:
        _insert_text_fit(
            page,
            rect,
            text,
            fontname=fontname,
            fontfile=fontfile,
            fontsize=fontsize,
            color=color,
            font_cache=font_cache,
        )
        return

    # Measure each char width.
    widths: List[float] = []
    total = 0.0
    for ch in chars:
        w = _measure_text_width(ch, fontsize=fontsize, fontname=fontname, fontfile=fontfile, font_cache=font_cache)
        widths.append(w)
        total += w

    max_width = max(1.0, float(rect.width) - 1.0)
    gaps = max(1, len(chars) - 1)
    extra = (max_width - total) / float(gaps) if max_width > total else 0.0

    point = _baseline_point(rect, fontsize=fontsize, fontname=fontname, fontfile=fontfile)
    x = float(point.x)
    y = float(point.y)

    # Avoid ridiculous spacing if rect is huge.
    extra = max(0.0, min(extra, float(fontsize) * 2.5))

    for ch, w in zip(chars, widths):
        if ch:
            page.insert_text(
                fitz.Point(x, y),
                ch,
                fontname=fontname,
                fontfile=fontfile,
                fontsize=fontsize,
                color=color,
                overlay=True,
            )
        x += float(w) + extra


def _should_use_distributed_insertion(
    *,
    rect: fitz.Rect,
    original_text: str,
    replacement_text: str,
    measured_replacement: float,
    measured_original: float,
    fontsize: float,
) -> bool:
    """Heuristic to decide if we should mimic tracking by distributing chars.

    This is helpful when the *original* text is tracked (extra letter-spacing).
    But if the replacement is much shorter than the original (e.g. replacing a
    full name with a single word), distributing creates huge gaps like
    "G o w t h a m".
    """

    if fontsize < 12:
        return False
    if not replacement_text or len(replacement_text) <= 2:
        return False
    if measured_replacement <= 0:
        return False

    rect_w = max(1.0, float(rect.width) - 1.0)
    repl_fill = rect_w / float(measured_replacement)
    if repl_fill < 1.20:
        return False

    orig = (original_text or "").strip()
    repl = (replacement_text or "").strip()
    if not orig:
        return False

    # If whitespace pattern differs (e.g. "First Last" -> "First"), don't distribute.
    if any(ch.isspace() for ch in orig) != any(ch.isspace() for ch in repl):
        return False

    # Require original to also look under-filled (tracked) for the chosen font.
    if measured_original <= 0:
        return False
    orig_fill = rect_w / float(measured_original)
    if orig_fill < 1.10:
        return False

    # Require similar text lengths to avoid extreme spacing.
    lo = max(1, len(orig))
    ratio = len(repl) / float(lo)
    if ratio < 0.65 or ratio > 1.35:
        return False

    return True


def _insert_text_fit(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    *,
    fontname: str,
    fontfile: Optional[str] = None,
    fontsize: float,
    color: Tuple[float, float, float],
    font_cache: Optional[Dict[Tuple[str, str], fitz.Font]] = None,
) -> None:
    """Insert single-line text fitted to the rect width, aligned to rect baseline.

    This produces more consistent visual results than insert_textbox, which may
    wrap or adjust layout.
    """

    size = float(fontsize)
    min_size = 4.0
    max_width = max(1.0, float(rect.width) - 1.0)

    # One fast scale step, then a couple of small adjustments.
    w0 = _measure_text_width(text, fontsize=size, fontname=fontname, fontfile=fontfile, font_cache=font_cache)
    if w0 > max_width and w0 > 0:
        size = max(min_size, size * (max_width / w0))
        for _ in range(4):
            w = _measure_text_width(text, fontsize=size, fontname=fontname, fontfile=fontfile, font_cache=font_cache)
            if w <= max_width:
                break
            next_size = size * 0.97
            if next_size < min_size:
                size = min_size
                break
            size = next_size

    point = _baseline_point(rect, fontsize=size, fontname=fontname, fontfile=fontfile)
    page.insert_text(
        point,
        text,
        fontname=fontname,
        fontfile=fontfile,
        fontsize=size,
        color=color,
        overlay=True,
    )


def find_replace_text(
    pdf_bytes: bytes,
    *,
    find_text: str,
    replace_text: str,
    scope: str,
    from_page: Optional[int],
    to_page: Optional[int],
) -> bytes:
    out, _count = find_replace_text_with_count(
        pdf_bytes,
        find_text=find_text,
        replace_text=replace_text,
        scope=scope,
        from_page=from_page,
        to_page=to_page,
        font_choice=None,
    )
    return out


def find_replace_text_with_count(
    pdf_bytes: bytes,
    *,
    find_text: str,
    replace_text: str,
    scope: str,
    from_page: Optional[int],
    to_page: Optional[int],
    font_choice: Optional[str],
    extra_fonts: Optional[List[Tuple[str, bytes]]] = None,
) -> Tuple[bytes, int]:
    """Like find_replace_text, but also returns number of occurrences replaced."""

    out, count, _debug = _find_replace_core(
        pdf_bytes,
        find_text=find_text,
        replace_text=replace_text,
        scope=scope,
        from_page=from_page,
        to_page=to_page,
        font_choice=font_choice,
        extra_fonts=extra_fonts,
        collect_debug=False,
    )
    return out, count


def find_replace_text_with_count_and_debug(
    pdf_bytes: bytes,
    *,
    find_text: str,
    replace_text: str,
    scope: str,
    from_page: Optional[int],
    to_page: Optional[int],
    font_choice: Optional[str],
    extra_fonts: Optional[List[Tuple[str, bytes]]] = None,
) -> Tuple[bytes, int, Dict[str, str]]:
    """Like find_replace_text_with_count, but returns debug font info.

    Debug data is best-effort and only includes the *first* replacement.
    """

    return _find_replace_core(
        pdf_bytes,
        find_text=find_text,
        replace_text=replace_text,
        scope=scope,
        from_page=from_page,
        to_page=to_page,
        font_choice=font_choice,
        extra_fonts=extra_fonts,
        collect_debug=True,
    )


def _find_replace_core(
    pdf_bytes: bytes,
    *,
    find_text: str,
    replace_text: str,
    scope: str,
    from_page: Optional[int],
    to_page: Optional[int],
    font_choice: Optional[str],
    extra_fonts: Optional[List[Tuple[str, bytes]]],
    collect_debug: bool,
) -> Tuple[bytes, int, Dict[str, str]]:
    debug: Dict[str, str] = {}

    if not find_text:
        raise PdfOpError("findText is required")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = doc.page_count

    if scope not in {"all", "range"}:
        doc.close()
        raise PdfOpError("scope must be 'all' or 'range'")

    if scope == "range":
        if from_page is None or to_page is None:
            doc.close()
            raise PdfOpError("fromPage and toPage are required when scope='range'")
        if from_page < 1 or from_page > page_count:
            doc.close()
            raise PdfOpError(f"fromPage must be between 1 and {page_count}")
        if to_page < 1 or to_page > page_count:
            doc.close()
            raise PdfOpError(f"toPage must be between 1 and {page_count}")
        if to_page < from_page:
            doc.close()
            raise PdfOpError("toPage must be >= fromPage")
        page_indices = list(range(from_page - 1, to_page))
    else:
        page_indices = list(range(page_count))

    replace_count = 0
    with tempfile.TemporaryDirectory(prefix="pdf_editor_fonts_") as tmpdir:
        token = None
        if extra_fonts:
            user_dir = os.path.join(tmpdir, "uploaded_fonts")
            try:
                os.makedirs(user_dir, exist_ok=True)
                for i, (fname, data) in enumerate(extra_fonts):
                    if not data:
                        continue
                    base = os.path.basename(fname or "font")
                    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
                    if not base.lower().endswith((".ttf", ".otf")):
                        continue
                    out_name = f"{i:02d}_{base}"
                    with open(os.path.join(user_dir, out_name), "wb") as f:
                        f.write(data)
                token = _EXTRA_FONT_DIRS.set((user_dir,))
            except Exception:  # noqa: BLE001
                token = None

        # Cache extracted font files per font name.
        fontfile_cache: dict[str, Optional[str]] = {}
        # Cache font objects for width + metrics (per request).
        font_obj_cache: Dict[Tuple[str, str], fitz.Font] = {}

        try:
            for page_index in page_indices:
                page = doc.load_page(page_index)
                raw_rects = page.search_for(find_text)
                rects = _merge_close_rects(raw_rects)
                if not rects:
                    continue

                # Parse text once per page (this is expensive) and reuse for all matches.
                try:
                    page_text = page.get_text("dict")
                    spans = _extract_spans(page_text)
                except Exception:  # noqa: BLE001
                    spans = []

                # Expand each match to a whole word bbox when the match is a substring.
                targets: List[fitz.Rect] = []
                originals: List[str] = []
                inserts: List[str] = []
                for rect in rects:
                    trect, orig_word, replaced_word = _expand_rect_to_word(
                        page,
                        rect,
                        find_text=find_text,
                        replace_text=replace_text,
                    )
                    targets.append(trect)
                    inserts.append(replaced_word)
                    if orig_word:
                        originals.append(orig_word)
                    else:
                        originals.append("")

                styles: List[_SpanStyle] = []
                for rect in targets:
                    s = _pick_style_for_rect_from_spans(spans, rect)
                    if not s.fontname:
                        s = _pick_style_for_rect_clip(page, rect)
                    styles.append(s)

                # Fill in originals for cases where we didn't expand to a word.
                for i, rect in enumerate(targets):
                    if originals[i]:
                        continue
                    try:
                        originals[i] = (page.get_textbox(rect) or "").strip()
                    except Exception:  # noqa: BLE001
                        originals[i] = find_text
                replace_count += len(rects)

                # Extract needed embedded fonts once per page/doc.
                for style in styles:
                    if not style.fontname:
                        continue
                    key = _normalize_fontname(style.fontname)
                    if not key or key in fontfile_cache:
                        continue
                    fontfile_cache[key] = _try_extract_embedded_fontfile(doc, page, style.fontname, tmpdir=tmpdir)

                for rect in targets:
                    page.add_redact_annot(rect, fill=(1, 1, 1))
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

                for rect, style, original, insert_text in zip(targets, styles, originals, inserts):
                    target_size = style.fontsize or _estimate_font_size(rect)
                    target_color = style.color or (0.0, 0.0, 0.0)
                    mapped_font = _map_font_to_base14(style.fontname or "")

                    insert_text = _match_replacement_case(original, insert_text)

                    # Preserve bold/italic as detected. Exact font matching is only
                    # possible when the embedded font can be reused/extracted or the
                    # same font files are available (bundled/system).
                    detected_name = str(style.fontname or "")
                    is_cm = _is_computer_modern_font(detected_name)

                    base_bold, base_italic = _infer_bold_italic(detected_name)
                    is_bold = bool(style.bold) or base_bold
                    is_italic = bool(style.italic) or base_italic

                    effective_bold = is_bold
                    effective_italic = is_italic

                    system_fontfile, system_font_source = _try_system_fontfile_with_source(
                        detected_name,
                        bold=effective_bold,
                        italic=effective_italic,
                    )

                    embedded_fontfile = None
                    if style.fontname:
                        embedded_fontfile = fontfile_cache.get(_normalize_fontname(style.fontname))

                    forced_windows = _windows_fontfile_for_choice(font_choice or "", bold=is_bold, italic=is_italic)
                    windows_fontfile = forced_windows or _try_windows_fontfile(
                        detected_name, bold=effective_bold, italic=effective_italic
                    )
                    if not windows_fontfile and mapped_font:
                        windows_fontfile = _try_windows_fontfile(mapped_font, bold=effective_bold, italic=effective_italic)

                    # Subset fonts (often "ABCDEE+FontName") may not contain glyphs
                    # for new characters, which can render as squares. In that case,
                    # prefer a full system font first.
                    is_subset_font = bool(style.fontname and "+" in style.fontname)
                    introduces_new = _replacement_introduces_new_chars(original, replace_text)
                    prefer_full_font = bool(forced_windows) or is_subset_font or introduces_new

                    # Priority (Auto):
                    # 1) User-forced Windows font (if chosen)
                    # 2) Extracted embedded font program (exact match), unless subset+new glyphs
                    # 3) Windows font derived from font name
                    # 4) System font file mapping
                    # 5) Original font name / Base-14 / Helvetica

                    # Use embedded extracted font when it can render the replacement.
                    # This gives the closest possible match.
                    can_use_embedded = False
                    if embedded_fontfile and not forced_windows:
                        can_use_embedded = _font_supports_text(
                            insert_text,
                            fontsize=target_size,
                            fontname=str(style.fontname or "helv"),
                            fontfile=embedded_fontfile,
                            font_cache=font_obj_cache,
                        )

                    # Important: if the embedded font is a subset and the replacement
                    # introduces new characters, reusing the embedded program can
                    # render missing letters. In those cases prefer a full font.
                    if embedded_fontfile and not forced_windows and can_use_embedded and not prefer_full_font:
                        try:
                            embed_name = f"emb_{_normalize_fontname(style.fontname or 'font')}_{page_index}"
                            if collect_debug and not debug:
                                debug.update(
                                    {
                                        "detectedFont": str(style.fontname or ""),
                                        "detectedBold": "1" if is_bold else "0",
                                        "detectedItalic": "1" if is_italic else "0",
                                        "usedSource": "embedded",
                                        "usedFont": os.path.basename(embedded_fontfile) if embedded_fontfile else "",
                                    }
                                )
                            # If the bbox is much wider than the measured text, the
                            # original line likely used tracking; distribute chars.
                            measured = _measure_text_width(
                                insert_text,
                                fontsize=target_size,
                                fontname=embed_name,
                                fontfile=embedded_fontfile,
                                font_cache=font_obj_cache,
                            )
                            measured_orig = _measure_text_width(
                                original,
                                fontsize=target_size,
                                fontname=embed_name,
                                fontfile=embedded_fontfile,
                                font_cache=font_obj_cache,
                            )
                            if _should_use_distributed_insertion(
                                rect=rect,
                                original_text=original,
                                replacement_text=insert_text,
                                measured_replacement=measured,
                                measured_original=measured_orig,
                                fontsize=target_size,
                            ):
                                _insert_text_distributed(
                                    page,
                                    rect,
                                    insert_text,
                                    fontname=embed_name,
                                    fontfile=embedded_fontfile,
                                    fontsize=target_size,
                                    color=target_color,
                                    font_cache=font_obj_cache,
                                )
                            else:
                                _insert_text_fit(
                                    page,
                                    rect,
                                    insert_text,
                                    fontname=embed_name,
                                    fontfile=embedded_fontfile,
                                    fontsize=target_size,
                                    color=target_color,
                                    font_cache=font_obj_cache,
                                )
                            continue
                        except Exception:  # noqa: BLE001
                            pass

                    # If user forced Windows, or embedded is unsafe/unavailable.
                    if windows_fontfile and (
                        forced_windows
                        or prefer_full_font
                        or introduces_new
                        or not embedded_fontfile
                        or not can_use_embedded
                    ):
                        try:
                            base_name = font_choice if forced_windows else (style.fontname or "font")
                            win_name = f"win_{_normalize_fontname(str(base_name))}_{page_index}"
                            if collect_debug and not debug:
                                debug.update(
                                    {
                                        "detectedFont": str(style.fontname or ""),
                                        "detectedBold": "1" if is_bold else "0",
                                        "detectedItalic": "1" if is_italic else "0",
                                        "usedSource": "windows",
                                        "usedFont": os.path.basename(windows_fontfile) if windows_fontfile else "",
                                    }
                                )
                            measured = _measure_text_width(
                                insert_text,
                                fontsize=target_size,
                                fontname=win_name,
                                fontfile=windows_fontfile,
                                font_cache=font_obj_cache,
                            )
                            measured_orig = _measure_text_width(
                                original,
                                fontsize=target_size,
                                fontname=win_name,
                                fontfile=windows_fontfile,
                                font_cache=font_obj_cache,
                            )
                            if _should_use_distributed_insertion(
                                rect=rect,
                                original_text=original,
                                replacement_text=insert_text,
                                measured_replacement=measured,
                                measured_original=measured_orig,
                                fontsize=target_size,
                            ):
                                _insert_text_distributed(
                                    page,
                                    rect,
                                    insert_text,
                                    fontname=win_name,
                                    fontfile=windows_fontfile,
                                    fontsize=target_size,
                                    color=target_color,
                                    font_cache=font_obj_cache,
                                )
                            else:
                                _insert_text_fit(
                                    page,
                                    rect,
                                    insert_text,
                                    fontname=win_name,
                                    fontfile=windows_fontfile,
                                    fontsize=target_size,
                                    color=target_color,
                                    font_cache=font_obj_cache,
                                )
                            continue
                        except Exception:  # noqa: BLE001
                            pass

                    # If embedded exists but we didn't use it above, try it as a last resort
                    # before dropping to built-in fonts.
                    if embedded_fontfile:
                        try:
                            embed_name = f"emb_{_normalize_fontname(style.fontname or 'font')}_{page_index}"
                            if collect_debug and not debug:
                                debug.update(
                                    {
                                        "detectedFont": str(style.fontname or ""),
                                        "detectedBold": "1" if is_bold else "0",
                                        "detectedItalic": "1" if is_italic else "0",
                                        "usedSource": "embedded_last_resort",
                                        "usedFont": os.path.basename(embedded_fontfile) if embedded_fontfile else "",
                                    }
                                )
                            _insert_text_fit(
                                page,
                                rect,
                                insert_text,
                                fontname=embed_name,
                                fontfile=embedded_fontfile,
                                fontsize=target_size,
                                color=target_color,
                                font_cache=font_obj_cache,
                            )
                            continue
                        except Exception:  # noqa: BLE001
                            pass

                    if windows_fontfile:
                        try:
                            win_name = f"win_{_normalize_fontname(style.fontname or 'font')}_{page_index}"
                            if collect_debug and not debug:
                                debug.update(
                                    {
                                        "detectedFont": str(style.fontname or ""),
                                        "detectedBold": "1" if is_bold else "0",
                                        "detectedItalic": "1" if is_italic else "0",
                                        "usedSource": "windows_fallback",
                                        "usedFont": os.path.basename(windows_fontfile) if windows_fontfile else "",
                                    }
                                )
                            _insert_text_fit(
                                page,
                                rect,
                                insert_text,
                                fontname=win_name,
                                fontfile=windows_fontfile,
                                fontsize=target_size,
                                color=target_color,
                                font_cache=font_obj_cache,
                            )
                            continue
                        except Exception:  # noqa: BLE001
                            pass

                    # If embedded extraction failed, try a system font file.
                    if system_fontfile:
                        try:
                            sys_name = f"sys_{_normalize_fontname(style.fontname or 'font')}_{page_index}"
                            if collect_debug and not debug:
                                debug.update(
                                    {
                                        "detectedFont": str(style.fontname or ""),
                                        "detectedBold": "1" if is_bold else "0",
                                        "detectedItalic": "1" if is_italic else "0",
                                        "usedSource": str(system_font_source or "system"),
                                        "usedFont": os.path.basename(system_fontfile) if system_fontfile else "",
                                    }
                                )
                            _insert_text_fit(
                                page,
                                rect,
                                insert_text,
                                fontname=sys_name,
                                fontfile=system_fontfile,
                                fontsize=target_size,
                                color=target_color,
                                font_cache=font_obj_cache,
                            )
                            continue
                        except Exception:  # noqa: BLE001
                            pass

                    for candidate in (style.fontname, mapped_font, "helv"):
                        if not candidate:
                            continue
                        try:
                            if collect_debug and not debug:
                                debug.update(
                                    {
                                        "detectedFont": str(style.fontname or ""),
                                        "detectedBold": "1" if is_bold else "0",
                                        "detectedItalic": "1" if is_italic else "0",
                                        "usedSource": "builtin",
                                        "usedFont": str(candidate),
                                    }
                                )
                            _insert_text_fit(
                                page,
                                rect,
                                insert_text,
                                fontname=str(candidate),
                                fontsize=target_size,
                                color=target_color,
                                font_cache=font_obj_cache,
                            )
                            break
                        except Exception:  # noqa: BLE001
                            continue

        finally:
            if token is not None:
                try:
                    _EXTRA_FONT_DIRS.reset(token)
                except Exception:  # noqa: BLE001
                    pass

    data = doc.tobytes()
    doc.close()
    return data, replace_count, debug
