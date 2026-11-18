"""Shared, non-secret metadata for the launcher, executor cards and supervisor."""
import json
import os
import re
import tempfile
from pathlib import Path


def component(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"invalid identifier: {value!r}")
    return value


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=1)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def append_record(path, record):
    payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def done_data(path):
    try:
        return dict(line.split("=", 1) for line in Path(path).read_text(
            encoding="utf-8", errors="replace").splitlines() if "=" in line)
    except FileNotFoundError:
        return {}


def engine_timeout(run, name, requested=None):
    card = read_json(Path(run) / "runs" / f"{component(name)}.json", {}) or {}
    declared = card.get("timeoutSec")
    if declared not in (None, ""):
        declared = int(declared)
        if requested is not None and int(requested) != declared:
            raise ValueError(f"process timeout {requested}s differs from card timeoutSec={declared}; set the card before launch")
        return declared, "card"
    if requested is not None:
        return int(requested), "argument"
    return 3600, "launcher-default"


def alive(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    # A zombie will never produce a result. Linux exposes this without invoking ps.
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return True
    return state != "Z"


def latest_call(run, name):
    folder = Path(run) / "engine" / component(name)
    markers = sorted(folder.glob("*.out.pid"), key=lambda p: p.stat().st_mtime_ns)
    return markers[-1].with_suffix("") if markers else None


def take_result(run, out):
    run, out = Path(run).resolve(), Path(out).resolve()
    if out.parent.parent != run / "engine" or not out.name.endswith(".out"):
        raise ValueError("result must belong to this run (engine/<name>/*.out)")
    metadata = done_data(f"{out}.done")
    if not metadata:
        raise ValueError("call does not have a completed .done marker")
    # Previous versions omit report on failure. Never substitute the raw log for approval.
    report = metadata.get("report")
    partial = metadata.get("result") or metadata.get("out") or str(out)
    Path(f"{out}.taken").touch(exist_ok=True)
    return {
        "status": metadata.get("status", "unknown"),
        "exit": metadata.get("exit"),
        "failureKind": metadata.get("failure_kind"),
        "reason": metadata.get("reason"),
        "report": report if metadata.get("status") == "done" else None,
        "artifact": partial,
        "session": metadata.get("session") or None,
    }


def validate_run_directory(path, repo=None):
    run = Path(path).resolve()
    # Existing legacy runs may lack state.md; a repository root must never be a run.
    if not run.is_dir() or (run / ".git").exists() or (repo and run == Path(repo).resolve()):
        raise ValueError(f"not a run directory: {run}")
    return run
