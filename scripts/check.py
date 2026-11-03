#!/usr/bin/env python3

# scripts/check.py - Check source syntax, local documentation links, and configuration shape.
# Usage: python scripts/check.py
#
# This does not replace cloud integration tests or an independent security review

import ast
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from deploy_ops.config import load  # noqa: E402


def main() -> int:
    failures = []
    files = [path for path in ROOT.rglob("*") if path.is_file() and not any(
        part in {".git", ".venv", "__pycache__", ".pytest_cache", ".state", ".deploy_ops", ".deploy-ops", "build", "dist"}
        or part.endswith(".egg-info") for part in path.relative_to(ROOT).parts
    )]
    for path in files:
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        try:
            if path.suffix == ".py":
                ast.parse(text, filename=str(path))
            elif path.suffix == ".toml":
                tomllib.loads(text)
            elif path.suffix == ".json":
                json.loads(text)
            elif path.suffix in (".yaml", ".yml"):
                yaml.safe_load(text)
            elif path.suffix == ".sh":
                subprocess.run(["bash", "-n", str(path)], check=True)
            if path.suffix == ".md":
                for link in re.findall(r"\]\(([^)]+)\)", text):
                    if "://" in link or link.startswith(("#", "mailto:")):
                        continue
                    target = link.split("#", 1)[0]
                    if target and not (path.parent / target).exists():
                        raise ValueError("Unresolved local link: " + link)
            if not text.endswith("\n") or any(line.rstrip() != line for line in text.splitlines()):
                raise ValueError("Missing final newline or trailing whitespace")
        except (ValueError, SyntaxError, yaml.YAMLError, subprocess.SubprocessError) as error:
            failures.append(f"{path.relative_to(ROOT)}: {error}")
    try:
        for example in (ROOT / "config").glob("*.example.toml"):
            load(example)
    except ValueError as error:
        failures.append("Example controller configuration: " + str(error))
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Checked {len(files)} files. Python, JSON, YAML, TOML, shell syntax, and local links passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
