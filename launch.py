"""Double-click friendly local launcher; no external package installation."""
import os
from pathlib import Path
import sys
import threading
import webbrowser


def main():
    if sys.version_info < (3, 12):
        print("Python 3.12 이상을 설치한 뒤 다시 실행해 주세요.")
        raise SystemExit(1)
    os.chdir(Path(__file__).resolve().parent)
    from uahome.server import main as serve
    # Server-only custom CLI flags belong to `python -m uahome`.
    if len(sys.argv) == 1:
        timer = threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8765"))
        timer.daemon = True
        timer.start()
    serve()


if __name__ == "__main__":
    main()
