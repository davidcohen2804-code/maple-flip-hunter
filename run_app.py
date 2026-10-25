"""PyInstaller entry point / convenience launcher: `python run_app.py`."""
import sys

from maple_flip_hunter.app import main

if __name__ == "__main__":
    sys.exit(main())
