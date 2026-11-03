# application/build.py - Build a static artifact without cloud credentials.

from pathlib import Path

source = Path("index.html")
destination = Path("dist")
destination.mkdir(exist_ok=True)
(destination / "index.html").write_bytes(source.read_bytes())
print("Built dist/index.html")
