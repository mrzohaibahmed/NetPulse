import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    # Works in both source-run and PyInstaller onefile mode.
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def main() -> int:
    root = _repo_root()
    backend_dir = root / "backend"
    if not backend_dir.exists():
        print("ERROR: backend folder not found next to the executable.")
        return 1

    os.environ.setdefault("FLASK_DEBUG", "false")

    # Ensure imports like `from config.database import ...` work.
    os.chdir(str(backend_dir))
    sys.path.insert(0, str(backend_dir))

    import app as backend_app  # noqa: E402

    backend_app.app.run(host="0.0.0.0", port=5000, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

