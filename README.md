# PDF Editor Web App

Features:
- Find & replace text (all pages or a specific page)
- Merge PDFs
- Reorder pages
- Remove pages

## Prerequisites
- Node.js 18+
- Python 3.10+

## Run backend
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
python main.py
```

Open: http://localhost:8001

To use a different port:
```powershell
$env:PORT = 8000
python main.py
```

## Deploy on Render (free plan)

This app is compatible with Render's free Python web service.

- Build command: `pip install -r backend/requirements.txt`
- Start command: `python main.py`

`main.py` binds to `0.0.0.0` and uses Render's provided `$PORT`.

### Font matching on Render

Render runs Linux, so Windows fonts like Calibri/Cambria/Times New Roman are not available by default.
The app will fall back to common Linux fonts (DejaVu/Liberation) when the original PDF font can't be reused.

If you need an *exact* match, you must provide the same font files the PDF uses.
You can do this in either of these ways:

- Put `.ttf` / `.otf` files in `backend/app/fonts/` (recommended)
- Or set `PDF_EDITOR_FONTS_DIR` to a directory containing your fonts

After that, Find/Replace (Auto) will prefer your bundled fonts. The UI debug line will show `usedSource: bundled` when this is working.

If your PDF uses LaTeX Computer Modern fonts (e.g. `CMBX12`) and you need a closer match, you can bundle font files in:

- `backend/app/fonts/`

Supported filenames include (any subset is fine):
- `cmunrm.ttf`, `cmunbx.ttf`, `cmunit.ttf`, `cmunbi.ttf` (CMU)
- `lmroman10-regular.otf`, `lmroman10-bold.otf`, `lmroman10-italic.otf`, `lmroman10-bolditalic.otf` (Latin Modern)

After deploying, Auto font matching will prefer these bundled fonts.
