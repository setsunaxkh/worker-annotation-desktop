"""PyInstaller entry; source users run python -m worker_annotator."""
from worker_annotator.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
