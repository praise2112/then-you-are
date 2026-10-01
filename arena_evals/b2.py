"""Copies files between this machine and the oddstage/ prefix of the B2 bucket in .env.

    uv run python -m arena_evals.b2 push data/prefs-1 FILE [FILE ...]
    uv run python -m arena_evals.b2 pull runs/judge-lfm25-350m-s0 DEST [--only model/]

`push` uploads each file under oddstage/PREFIX/ by its name; `pull` downloads everything under
oddstage/PREFIX/ (or only the part under --only) into DEST, keeping the relative paths.
"""

import argparse
import os
import sys
from pathlib import Path

from b2sdk.v2 import B2Api, InMemoryAccountInfo

ROOT = "oddstage"


def bucket():
    api = B2Api(InMemoryAccountInfo())  # type: ignore[arg-type]
    api.authorize_account(
        "production", os.environ["B2_APP_KEY_ID_PERSONAL"], os.environ["B2_APP_KEY_PERSONAL"]
    )
    return api.get_bucket_by_name(os.environ["B2_BUCKET_NAME_PERSONAL"])


def push(prefix: str, files: list[Path]) -> None:
    b = bucket()
    for f in files:
        b.upload_local_file(local_file=str(f), file_name=f"{ROOT}/{prefix}/{f.name}")
        print(f"pushed {f.name} ({f.stat().st_size / 2**20:.1f} MB)", file=sys.stderr)


def pull(prefix: str, dest: Path, only: str) -> None:
    b = bucket()
    base = f"{ROOT}/{prefix}/"
    found = 0
    for fv, _ in b.ls(base + only, recursive=True):
        target = dest / fv.file_name.removeprefix(base)
        target.parent.mkdir(parents=True, exist_ok=True)
        b.download_file_by_name(fv.file_name).save_to(str(target))
        found += 1
    if not found:
        raise SystemExit(f"nothing under {base}{only}")
    print(f"pulled {found} files into {dest}", file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pu = sub.add_parser("push")
    pu.add_argument("prefix")
    pu.add_argument("files", nargs="+", type=Path)
    pl = sub.add_parser("pull")
    pl.add_argument("prefix")
    pl.add_argument("dest", type=Path)
    pl.add_argument("--only", default="", help="a path under the prefix, such as model/")
    args = ap.parse_args()
    if args.cmd == "push":
        push(args.prefix, args.files)
    else:
        pull(args.prefix, args.dest, args.only)
