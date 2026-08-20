"""Keep the demo site's copies of the shared assets up to date.

The demo deliberately uses the application's own stylesheet and renderer, so
that what a visitor sees is what the real interface produces. Vercel serves the
demo directory as static files and cannot reach outside it, so the shared files
are copied in rather than referenced.

Copying is a hazard - copies go stale - so this script is the single way it
happens, and a test asserts the copies still match their sources.

Run with:  python -m demo.sync_assets
"""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
STATIC_DIR = DEMO_DIR.parent / "app" / "static"

SHARED_ASSETS = ("style.css", "render.js")


def sync() -> list[str]:
    """Copy any shared asset that has drifted. Returns what changed."""
    changed = []
    for name in SHARED_ASSETS:
        source, target = STATIC_DIR / name, DEMO_DIR / name
        if not target.exists() or not filecmp.cmp(source, target, shallow=False):
            shutil.copyfile(source, target)
            changed.append(name)
    return changed


def out_of_date() -> list[str]:
    """Return the shared assets whose demo copy no longer matches the source."""
    return [
        name
        for name in SHARED_ASSETS
        if not (DEMO_DIR / name).exists()
        or not filecmp.cmp(STATIC_DIR / name, DEMO_DIR / name, shallow=False)
    ]


if __name__ == "__main__":
    updated = sync()
    print(f"updated: {', '.join(updated)}" if updated else "already up to date")
