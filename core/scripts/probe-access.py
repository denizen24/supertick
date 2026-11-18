#!/usr/bin/env python3
"""Run inside the actual executor on a disposable worktree before starting roles."""
import argparse
import json
import os
import socket
import subprocess
import tempfile
from pathlib import Path


def writable(path):
    try:
        with path.open("r+b"):
            return True
    except PermissionError:
        return False


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--access", required=True, choices=("read", "write"))
    p.add_argument("--scratch")
    p.add_argument("--test-port", type=int, help="explicit local test database port")
    args = p.parse_args()
    repo = Path(args.repo).resolve()
    result = {}
    try:
        for flag, label in (("--absolute-git-dir", "worktreeGit"), ("--git-common-dir", "commonGit")):
            path = subprocess.check_output(["git", "-C", str(repo), "rev-parse", flag], timeout=10, text=True).strip()
            meta = (repo / path).resolve()
            canary = meta / "supertick-probe-canary"
            if not canary.is_file() or canary.is_symlink():
                raise ValueError("missing disposable Git probe canary")
            result[label] = writable(canary)
        canary = repo / "supertick-probe-canary"
        if not canary.is_file() or canary.is_symlink():
            raise ValueError("missing disposable project probe canary")
        result["sourceWrite"] = writable(canary)
        result["linkedWorktree"] = (repo / ".git").is_file()
        if args.scratch:
            with tempfile.TemporaryFile(dir=args.scratch):
                result["scratchWrite"] = True
        if args.test_port:
            with socket.create_connection(("127.0.0.1", args.test_port), timeout=3):
                result["testService"] = True
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "BROKEN", "checks": result, "detail": str(exc)}))
        return 2
    expected = args.access == "write"
    ok = all(result[k] == expected for k in ("sourceWrite", "worktreeGit", "commonGit"))
    print(json.dumps({"status": "PASS" if ok else "BROKEN", "checks": result}))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
