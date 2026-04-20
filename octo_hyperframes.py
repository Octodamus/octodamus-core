"""
octo_hyperframes.py
HyperFrames project scaffold + render wrapper for OctodamusCEO.

Usage:
    python octo_hyperframes.py scaffold <name>   # init project with Octodamus DESIGN.md
    python octo_hyperframes.py render [path]     # render project to MP4
    python octo_hyperframes.py lint [path]       # lint composition
    python octo_hyperframes.py list              # list video projects
"""

import shutil
import subprocess
import sys
from pathlib import Path

VIDEOS_DIR  = Path(r"C:\Users\walli\octodamus-site\videos")
DESIGN_SRC  = VIDEOS_DIR / "DESIGN.md"
SITE_DIR    = Path(r"C:\Users\walli\octodamus-site")


def _run(cmd: list, cwd: Path = None) -> int:
    result = subprocess.run(
        cmd, cwd=str(cwd or Path.cwd()),
        capture_output=False, text=True, encoding="utf-8"
    )
    return result.returncode


def scaffold(name: str) -> Path:
    project_dir = VIDEOS_DIR / name
    if project_dir.exists():
        print(f"Project already exists: {project_dir}")
        return project_dir

    print(f"Scaffolding: {project_dir}")
    rc = _run(
        ["npx", "hyperframes", "init", name, "--non-interactive"],
        cwd=VIDEOS_DIR
    )
    if rc != 0:
        print(f"Scaffold failed (rc={rc})")
        return project_dir

    # Copy Octodamus DESIGN.md into project
    if DESIGN_SRC.exists() and project_dir.exists():
        shutil.copy(DESIGN_SRC, project_dir / "DESIGN.md")
        print(f"Copied DESIGN.md -> {project_dir / 'DESIGN.md'}")

    print(f"\nProject ready: {project_dir}")
    print(f"Next: edit {project_dir / 'index.html'}")
    print(f"Then: python octo_hyperframes.py lint {name}")
    print(f"Then: python octo_hyperframes.py render {name}")
    return project_dir


def lint(name: str = None) -> int:
    path = VIDEOS_DIR / name if name else Path.cwd()
    print(f"Linting: {path}")
    return _run(["npx", "hyperframes", "lint", "--verbose"], cwd=path)


def render(name: str = None, quality: str = "standard", output: str = None) -> int:
    path = VIDEOS_DIR / name if name else Path.cwd()
    cmd = ["npx", "hyperframes", "render", "--quality", quality, "--workers", "2"]
    if output:
        cmd += ["--output", output]
    print(f"Rendering: {path} (quality={quality})")
    return _run(cmd, cwd=path)


def preview(name: str = None) -> int:
    path = VIDEOS_DIR / name if name else Path.cwd()
    print(f"Preview: {path} -- opening studio in browser...")
    return _run(["npx", "hyperframes", "preview"], cwd=path)


def list_projects():
    if not VIDEOS_DIR.exists():
        print("No videos directory found.")
        return
    projects = [d for d in VIDEOS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not projects:
        print("No video projects yet.")
        return
    print(f"Video projects in {VIDEOS_DIR}:")
    for p in sorted(projects):
        renders = list((p / "renders").glob("*.mp4")) if (p / "renders").exists() else []
        status = f"[{len(renders)} render(s)]" if renders else "[no renders]"
        print(f"  {p.name}  {status}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    arg = args[1] if len(args) > 1 else None

    if cmd == "scaffold":
        if not arg:
            print("Usage: python octo_hyperframes.py scaffold <name>")
            sys.exit(1)
        scaffold(arg)
    elif cmd == "render":
        quality = "draft" if "--draft" in args else "standard"
        sys.exit(render(arg, quality=quality))
    elif cmd == "lint":
        sys.exit(lint(arg))
    elif cmd == "preview":
        sys.exit(preview(arg))
    elif cmd == "list":
        list_projects()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
