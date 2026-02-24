from __future__ import annotations

import os

import uvicorn


def main() -> None:
    # Run the FastAPI app located in backend/app/main.py
    reload = os.getenv("PDF_EDITOR_RELOAD", "0") == "1"
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8001")),
        reload=reload,
        app_dir="backend",
    )


if __name__ == "__main__":
    main()
