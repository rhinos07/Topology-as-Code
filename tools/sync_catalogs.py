#!/usr/bin/env python3
"""
Keeps the catalogs this repo copies from MasterData-as-Code in sync with
a pinned source commit (Warehouse-as-Code backlog C1, way A).

elements/catalog_sources.yaml records, per copied file, the source repo,
the commit (ref), the path in that repo and the sha256 of the content.
The copies are byte-identical to the source, so Topology keeps its own
checks against them (e.g. storage envelope vs. carrier dimensions) and
decides itself when to take over a new source version.

Usage:
    # CI / local: the copies still match their recorded hash
    python tools/sync_catalogs.py

    # CI: additionally compare against a checkout of the source repo at the pinned ref
    python tools/sync_catalogs.py --source-root ../masterdata-as-code

    # Take over a new source version: copy the files and re-pin
    python tools/sync_catalogs.py --update --source-root ../masterdata-as-code --ref <commit>

    # Print the pinned ref (used by CI to check out the source)
    python tools/sync_catalogs.py --print-ref
"""

import argparse
import hashlib
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ELEMENTS_DIR = REPO_ROOT / "elements"
SOURCES_FILE = ELEMENTS_DIR / "catalog_sources.yaml"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_sources(sources_file: Path = SOURCES_FILE) -> dict:
    with open(sources_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def check(sources: dict, elements_dir: Path, source_root: Path | None) -> list[str]:
    """Errors when a copy differs from its recorded hash, or (with
    source_root) from the source file in that checkout."""
    errors: list[str] = []
    for entry in sources.get("catalog_sources", []):
        copy = elements_dir / entry["file"]
        if not copy.exists():
            errors.append(f"{copy}: pinned copy missing")
            continue
        actual = sha256(copy)
        if actual != entry["sha256"]:
            errors.append(
                f"{copy}: content differs from the pinned {entry['repo']}@{entry['ref'][:12]} "
                f"(sha256 {actual[:12]} != {entry['sha256'][:12]}) - do not edit the copy, "
                f"change it in {entry['repo']} and re-pin with --update"
            )
        if source_root is not None:
            source = source_root / entry["path"]
            if not source.exists():
                errors.append(f"{source}: source file missing in checkout")
            elif sha256(source) != entry["sha256"]:
                errors.append(
                    f"{source}: source at the checked-out ref does not match the recorded sha256 - "
                    f"is the checkout at {entry['ref'][:12]}?"
                )
    return errors


def update(sources: dict, elements_dir: Path, source_root: Path, ref: str) -> dict:
    """Copies every source file into elements/ and re-pins ref and hash."""
    for entry in sources.get("catalog_sources", []):
        source = source_root / entry["path"]
        (elements_dir / entry["file"]).write_bytes(source.read_bytes())
        entry["ref"] = ref
        entry["sha256"] = sha256(source)
    return sources


def write_sources(sources: dict, sources_file: Path = SOURCES_FILE) -> None:
    header = (
        "# Catalogs copied byte-identically from sibling repos and pinned to a\n"
        "# source commit. Do not edit the copies; change the source and re-pin:\n"
        "#   python tools/sync_catalogs.py --update --source-root <checkout> --ref <commit>\n"
        "# CI checks the copies against this file and against the source at ref.\n\n"
    )
    sources_file.write_text(header + yaml.safe_dump(sources, sort_keys=False), encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--ref")
    parser.add_argument("--print-ref", action="store_true")
    args = parser.parse_args(argv[1:])

    sources = load_sources()
    if args.print_ref:
        refs = {entry["ref"] for entry in sources.get("catalog_sources", [])}
        if len(refs) != 1:
            print(f"Expected exactly one pinned ref, found {sorted(refs)}", file=sys.stderr)
            return 2
        print(refs.pop())
        return 0

    if args.update:
        if not args.source_root or not args.ref:
            print("--update needs --source-root and --ref", file=sys.stderr)
            return 2
        write_sources(update(sources, ELEMENTS_DIR, args.source_root, args.ref))
        print(f"✅ Re-pinned {len(sources['catalog_sources'])} catalog(s) to {args.ref}")
        return 0

    errors = check(sources, ELEMENTS_DIR, args.source_root)
    if errors:
        print(f"❌ {len(errors)} catalog sync error(s):\n")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"✅ {len(sources.get('catalog_sources', []))} pinned catalog(s) match their source.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
