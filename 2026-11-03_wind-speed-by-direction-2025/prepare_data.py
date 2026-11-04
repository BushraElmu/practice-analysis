"""Thin wrapper: preparation logic lives in the independent tooling project."""
from pathlib import Path
import subprocess
import sys

PRACTICE = Path(__file__).resolve().parent
REPOSITORY = PRACTICE.parent

if __name__ == "__main__":
    subprocess.run(["uv", "run", "--no-editable", "--project", str(REPOSITORY / "tooling"),
                    "awf", "step", str(PRACTICE), "prepare", *sys.argv[1:]], check=True)
