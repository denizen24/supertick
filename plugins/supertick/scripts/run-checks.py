#!/usr/bin/env python3
"""Run a trusted project's verification plan in an isolated file and environment snapshot."""
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_support import component, read_json, validate_run_directory, write_json


def digest(data):
    return hashlib.sha256(data).hexdigest()


def relative(value):
    p = Path(value)
    if p.is_absolute() or not p.parts or ".." in p.parts or ".git" in p.parts:
        raise ValueError(f"invalid relative path: {value!r}")
    return p


def git(repo, *argv):
    return subprocess.check_output(["git", "-C", str(repo), *argv], timeout=30)


def tracked(repo):
    # ls-files normally returns only tracked names; untracked files can never
    # become input just by appearing next to a run or a project .env.
    entries = {}
    for item in git(repo, "ls-files", "--stage", "-z").split(b"\0"):
        if not item:
            continue
        meta, raw = item.split(b"\t", 1)
        mode, _, stage = meta.split()
        p = relative(os.fsdecode(raw))
        if stage != b"0":
            raise ValueError(f"unmerged file: {p}")
        entries[p] = mode
    return entries


def safe_env(p):
    return all(not part.startswith(".env") for part in p.parts)


def snapshot(repo, directory, plan):
    files = tracked(repo)
    templates = {relative(p) for p in plan.get("env_templates", [])}
    if any(p.name != ".env.example" or not safe_env(p.parent) for p in templates):
        raise ValueError("only specific tracked .env.example templates are accepted")
    for p in templates:
        if files.get(p) not in (b"100644", b"100755"):
            raise ValueError(f"env template is not a tracked ordinary file: {p}")
    hashes = {}
    for p, mode in files.items():
        if not safe_env(p) and p not in templates:
            continue
        original = repo / p
        if mode not in (b"100644", b"100755") or original.is_symlink() or original.resolve() != original:
            raise ValueError(f"snapshot requires tracked ordinary files: {p}")
        target = directory / p
        target.parent.mkdir(parents=True, exist_ok=True)
        checksum = hashlib.sha256(mode + b"\0")
        with original.open("rb") as stream, target.open("wb") as copy:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                copy.write(chunk)
                checksum.update(chunk)
        target.chmod(0o755 if mode == b"100755" else 0o644)
        hashes[p.as_posix()] = checksum.hexdigest()
    return hashes


def audit_env(root, templates):
    def fail(exc):
        raise exc

    for path, directories, files in os.walk(root, onerror=fail):
        directories[:] = [name for name in directories if name != ".git"]
        for name in directories + files:
            if name.startswith(".env"):
                entry = Path(path) / name
                if entry.relative_to(root).as_posix() not in templates or not entry.is_file() or entry.is_symlink():
                    raise ValueError(f"unexpected environment file: {entry.relative_to(root)}")


def copy_dependencies(destination, copies):
    fingerprint = hashlib.sha256()
    for label, source in copies.items():
        local = destination / relative(label)
        if not safe_env(local.relative_to(destination)):
            raise ValueError("invalid dependency destination")
        if local.exists():
            raise ValueError(f"dependency destination already exists: {label}")
        source = Path(source).resolve(strict=True)
        # Preserve npm's relative .bin symlinks inside the physical dependency
        # tree. Reject links that would let the copied tree read outside itself.
        def fail(exc):
            raise exc

        for parent, dirs, files in os.walk(source, onerror=fail):
            for name in dirs + files:
                entry = Path(parent) / name
                if entry.is_symlink() and (Path(os.readlink(entry)).is_absolute()
                                           or not entry.resolve().is_relative_to(source)):
                    raise ValueError(f"nonlocal dependency symlink: {entry}")
        shutil.copytree(source, local, symlinks=True)
        for entry in sorted(local.rglob("*")):
            if entry.is_symlink():
                value = os.fsencode(os.readlink(entry))
            elif entry.is_file():
                with entry.open("rb") as item:
                    h = hashlib.sha256()
                    for chunk in iter(lambda: item.read(1024 * 1024), b""):
                        h.update(chunk)
                    value = h.digest()
            else:
                continue
            fingerprint.update(os.fsencode(entry.relative_to(destination)) + b"\0" + value + b"\0")
    return fingerprint.digest()


def pinned_git(repo, commit, out):
    gitdir = os.fsdecode(git(repo, "rev-parse", "--absolute-git-dir")).strip()
    # Limit the original git context to the requested read-only helper.
    script = out / "git-tracked"
    import shlex

    script.write_text("#!/bin/sh\nexec git --git-dir=" + shlex.quote(gitdir)
                      + " ls-tree -r --name-only " + shlex.quote(commit) + ' -- "$@"\n', encoding="utf-8")
    script.chmod(0o700)
    return script


def take_lock(path, activity, data, limit):
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.open("a+", encoding="utf-8")
    start = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                lock.seek(0)
                try:
                    holder = json.load(lock)
                except ValueError:
                    holder = {}
                elapsed = int(time.monotonic() - start)
                write_json(activity, {**data, "status": "queued", "queueSeconds": elapsed, "holder": holder})
                if limit and elapsed >= limit:
                    raise TimeoutError("verification environment queue timed out; test never started")
                time.sleep(0.25)
        lock.seek(0)
        lock.truncate()
        json.dump({key: data[key] for key in ("check", "executor", "pid")}, lock)
        lock.flush()
        data["queueSeconds"] = int(time.monotonic() - start)
        return lock
    except BaseException:
        lock.close()
        raise


def selected_hashes(step, hashes):
    paths = step.get("inputs")
    if paths is None:
        return hashes
    from fnmatch import fnmatchcase

    result = {name: value for name, value in hashes.items() if any(fnmatchcase(name, p) for p in paths)}
    if not result:
        raise ValueError(f"step {step['id']} has no inputs")
    return result


def verify(args):
    repo = Path(args.repo).resolve()
    run = validate_run_directory(args.run_dir, repo)
    key = component(args.id)
    root = run / "tmp" / "checks" / key
    root.mkdir(parents=True, exist_ok=True)
    activity = run / "state" / "checks" / f"{key}.json"
    summary = root / "results.json"
    plan_bytes = Path(args.plan).read_bytes()
    plan = json.loads(plan_bytes)
    secrets = {"MONGO_URL", "DATABASE_URL", "REDIS_URL"}
    env_values = plan.get("env", {})
    for name, value in env_values.items():
        if not isinstance(value, str) or not name or "=" in name or "\0" in name or "\0" in value:
            raise ValueError("env must contain string names and values")
        if name in secrets and value:
            from urllib.parse import urlsplit

            url = urlsplit(value)
            if url.hostname not in ("127.0.0.1", "::1", "localhost") or url.username or url.password:
                raise ValueError(f"{name} must point to an unauthenticated local test service")
    names = [component(step["id"]) for step in plan["steps"]]
    if not names or len(set(names)) != len(names) or set(args.only) - set(names):
        raise ValueError("verification step ids must be unique and selected steps must exist")
    data = {"check": key, "executor": component(args.executor or key), "pid": os.getpid(), "queueSeconds": 0}
    guard = root / "active.lock"
    with guard.open("a+") as unique:
        fcntl.flock(unique, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Refresh only after locking: another invocation may have just finished.
        previous = read_json(summary, {})
        if previous and not args.resume:
            raise ValueError("check already exists; use --resume or choose a new id")
        lock = None
        try:
            lock = take_lock(Path(args.lock).resolve() if args.lock else None, activity, data, args.queue_timeout)
            write_json(activity, {**data, "status": "preparing"})
            attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=root))
            workspace = attempt / "workspace"
            workspace.mkdir()
            temp = attempt / "tmp"
            temp.mkdir()
            scratch_home = attempt / "home"
            scratch_home.mkdir()
            commit = git(repo, "rev-parse", "HEAD").decode().strip()
            hashes = snapshot(repo, workspace, plan)
            previous_hashes = previous.get("files", {})
            changed = sorted(p for p in previous_hashes.keys() | hashes.keys() if previous_hashes.get(p) != hashes.get(p))
            result = {"check": key, "commitAtCopy": commit, "files": hashes,
                      "changedSinceLastAttempt": changed if previous else [], "steps": {}, "attempt": str(attempt)}
            if git(repo, "rev-parse", "HEAD").decode().strip() != commit:
                raise ValueError("HEAD moved while copying; take a new snapshot")
            dependencies = copy_dependencies(workspace, plan.get("copy_dependencies", {}))
            audit_env(workspace, set(plan.get("env_templates", [])))
            manifest = attempt / "source.json"
            write_json(manifest, result)
            import re

            prefix = plan.get("test_db_prefix", "supertick_test_")
            if not re.fullmatch(r"[a-zA-Z0-9_]{1,32}", prefix) or "test" not in prefix.lower():
                raise ValueError("test_db_prefix must contain 'test' and fit 32 ASCII characters")
            db = prefix + digest(os.fsencode(attempt))[:12]
            env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8", **env_values,
                   "HOME": str(scratch_home), "TMPDIR": str(temp), "DB_NAME": db,
                   "SUPERTICK_TRACKED_MANIFEST": str(manifest)}
            if plan.get("git_context"):
                env["SUPERTICK_GIT_TRACKED"] = str(pinned_git(repo, commit, attempt))
            pending = []
            for step in plan["steps"]:
                name = step["id"]
                fingerprint = digest(plan_bytes + dependencies + os.fsencode(env["PATH"]) + b"\0"
                                     + json.dumps(selected_hashes(step, hashes), sort_keys=True).encode())
                old = previous.get("steps", {}).get(name, {})
                if old.get("fingerprint") == fingerprint and old.get("status") == "PASS":
                    result["steps"][name] = old
                elif args.only and name not in args.only:
                    result["steps"][name] = {"status": "OUTDATED", "previous": old.get("log")}
                else:
                    result["steps"][name] = {"status": "PENDING", "fingerprint": fingerprint}
                    pending.append((step, fingerprint))
            write_json(summary, result)
            for step, fingerprint in pending:
                name = step["id"]
                cwd = workspace / relative(step["cwd"]) if step.get("cwd") else workspace
                log = attempt / f"{name}.log"
                argv = step["argv"]
                if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
                    raise ValueError(f"invalid argv in {name}")
                record = {"status": "RUNNING", "fingerprint": fingerprint, "log": str(log), "manifest": str(manifest)}
                result["steps"][name] = record
                write_json(summary, result)
                write_json(activity, {**data, "status": "running", "step": name, "startedAt": time.time()})
                start = time.monotonic()
                try:
                    with log.open("wb") as output:
                        code = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                              stdout=output, stderr=subprocess.STDOUT).returncode
                    record.update(exit=code, status="PASS" if code == 0 else "FAIL")
                except OSError as exc:
                    record.update(status="BROKEN", reason=str(exc))
                record["executionSeconds"] = round(time.monotonic() - start, 3)
                write_json(summary, result)
            write_json(summary, result)
            status = "PASS" if all(s["status"] == "PASS" for s in result["steps"].values()) else "INCOMPLETE"
            write_json(activity, {**data, "status": status.lower()})
            print(json.dumps({"status": status, "results": str(summary), "queueSeconds": data["queueSeconds"]}))
            return 0 if status == "PASS" else 1
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            write_json(activity, {**data, "status": "broken", "reason": str(exc)})
            raise
        finally:
            if lock:
                lock.close()


def bind(args):
    repo = Path(args.repo).resolve()
    run = validate_run_directory(args.run_dir, repo)
    root = run / "tmp" / "checks" / component(args.id)
    result = read_json(root / "results.json", {})
    if not result.get("steps") or any(step.get("status") != "PASS" for step in result["steps"].values()):
        raise ValueError("cannot bind incomplete verification")
    # Compare Git objects, not the current checkout. Read all committed paths
    # selected by the verification snapshot, with their tracked executable bits.
    commit = git(repo, "rev-parse", "--verify", "--end-of-options", args.commit + "^{commit}").decode().strip()
    tree = {}
    for entry in git(repo, "ls-tree", "-rz", commit).split(b"\0"):
        if entry:
            header, name = entry.split(b"\t", 1)
            mode, kind, _ = header.split()
            p = os.fsdecode(name)
            if safe_env(Path(p)) or p in result["files"]:
                tree[p] = (mode, kind)
    if tree.keys() != result["files"].keys():
        raise ValueError("committed paths differ from tested snapshot")
    for p, expected in result["files"].items():
        mode, kind = tree[p]
        if kind != b"blob" or digest(mode + b"\0" + git(repo, "show", f"{commit}:{p}")) != expected:
            raise ValueError(f"committed file differs from tested snapshot: {p}")
    print(json.dumps({"commit": commit, "matchedFiles": len(tree), "results": str(root / "results.json")}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "bind"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--executor")
    parser.add_argument("--plan")
    parser.add_argument("--lock")
    parser.add_argument("--queue-timeout", type=int, default=900)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--commit", default="HEAD")
    args = parser.parse_args()
    if args.queue_timeout <= 0:
        parser.error("--queue-timeout must be positive")
    if args.action == "run" and not args.plan:
        parser.error("run requires --plan")
    try:
        return verify(args) if args.action == "run" else bind(args)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"BROKEN: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
