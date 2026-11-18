#!/usr/bin/env python3
"""Run an engine with a deadline and mark actual timeouts separately from CLI exit codes."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_support import read_json


def signal_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def queued(run, role, group):
    for path in (run / "state" / "checks").glob("*.json"):
        try:
            state = read_json(path, {})
            if state.get("executor") == role and state.get("status") == "queued":
                if os.getpgid(int(state["pid"])) == group:
                    return True
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return False


def wait_for_engine(engine, limit, run, role):
    if not limit:
        return engine.wait()
    remaining = limit
    while remaining > 0:
        paused = queued(run, role, engine.pid)
        start = time.monotonic()
        try:
            return engine.wait(timeout=min(remaining, 0.25))
        except subprocess.TimeoutExpired:
            if not paused:
                remaining -= time.monotonic() - start
    raise subprocess.TimeoutExpired(engine.args, limit)


def run(limit, marker, run_dir, role, argv):
    try:
        engine = subprocess.Popen(argv, start_new_session=True)
    except OSError as exc:
        print(f"engine startup: {exc}", file=sys.stderr)
        marker.write_text("environment\n", encoding="utf-8")
        return 126

    def cancel(signum, _frame):
        raise InterruptedError(signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, cancel)
    try:
        returncode = wait_for_engine(engine, limit, run_dir, role)
    except (subprocess.TimeoutExpired, InterruptedError) as exc:
        expired = isinstance(exc, subprocess.TimeoutExpired)
        marker.write_text("timeout\n" if expired else "interrupted\n", encoding="utf-8")
        # The CLI may exit on TERM while one of its children keeps the pipe open.
        # Kill the whole group after the grace period even if the leader has exited.
        signal_group(engine.pid, signal.SIGTERM)
        try:
            engine.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        finally:
            signal_group(engine.pid, signal.SIGKILL)
            engine.wait()
        return 124 if expired else 128 + exc.args[0]
    # Children that stayed in the engine's group cannot keep running past
    # .done, even when the CLI returned before waiting for them.
    signal_group(engine.pid, signal.SIGKILL)
    return returncode if returncode >= 0 else 128 - returncode


if __name__ == "__main__":
    sys.exit(run(int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5:]))
