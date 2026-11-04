# application/check.py - Check the example's user-visible entry point before building.


from pathlib import Path

# Require the visible heading and project identity before building
text = Path("index.html").read_text()
if "<h1>" not in text or "DeployOps" not in text:
    raise SystemExit("The page needs a heading and a project name")
print("Entry point passed")
