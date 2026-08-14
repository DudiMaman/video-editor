#!/usr/bin/env python3
"""Commit transcripts + processed-video ledger wiring back to main, resilient
to the frontend concurrently writing the same ledger entries.

The old approach committed a ledger patch built from a stale checkout and then
`git pull --rebase`d. When the frontend had meanwhile flipped the same entry to
'processing' (which it does the moment "process & approve" is clicked), the
rebase hit a merge conflict in data/ledger.json, aborted, and the finished
video was stranded in 'processing' forever even though its release existed.

Instead we reset to the freshest origin/main each attempt and re-apply our
ledger mutations (video_id-keyed, from out/ledger_patch.json) on top, so the
final approved+output state always wins over an intermediate status with no
possible conflict. Transcript files are re-materialised after the reset.
"""
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=REPO, check=check,
                          capture_output=True, text=True)


def apply_ledger_patch(ledger: list, patch: list) -> bool:
    """Apply video_id-keyed mutations to a ledger in place."""
    by_id = {e.get("video_id"): e for e in ledger}
    changed = False
    for m in patch:
        entry = by_id.get(m["video_id"])
        if not entry:
            continue
        for k, v in m.items():
            if k != "video_id":
                entry[k] = v
        changed = True
    return changed


def main() -> int:
    patch_file = REPO / "out" / "ledger_patch.json"
    patch = json.loads(patch_file.read_text()) if patch_file.exists() else []

    # Snapshot the transcript files this run produced; `git reset --hard`
    # reverts any that overwrite a tracked file, so we restore them after.
    tx_dir = REPO / "data" / "transcripts"
    saved = {os.path.basename(f): Path(f).read_bytes()
             for f in glob.glob(str(tx_dir / "*.json"))}

    if not saved and not patch:
        print("nothing to commit back")
        return 0

    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com")

    for attempt in range(1, 7):
        git("fetch", "origin", "main")
        git("reset", "--hard", "origin/main")

        tx_dir.mkdir(parents=True, exist_ok=True)
        for name, data in saved.items():
            (tx_dir / name).write_bytes(data)

        ledger_path = REPO / "data" / "ledger.json"
        if patch and ledger_path.exists():
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            if apply_ledger_patch(ledger, patch):
                ledger_path.write_text(
                    json.dumps(ledger, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")

        git("add", "data/transcripts", "data/ledger.json", check=False)
        if git("diff", "--cached", "--quiet", check=False).returncode == 0:
            print("nothing to commit back")
            return 0

        git("commit", "-m",
            "Batch run: transcripts + processed-video ledger wiring")
        push = git("push", "origin", "main", check=False)
        if push.returncode == 0:
            print(f"pushed results on attempt {attempt}")
            return 0
        print(f"push rejected (attempt {attempt}): "
              f"{(push.stderr or '').strip()[:200]}")
        time.sleep(2 * attempt)

    print("ERROR: could not push results after 6 attempts", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
