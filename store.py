"""Tiny JSON state file. Load, save, done. No database."""

import json
import os
import tempfile

EMPTY_STATE = {
    "agents": {},  # name -> Agent.to_dict()
    "ledger": {},  # name -> credits (sums to zero)
    "open": {},  # task -> {"requester", "helper", "price"}
    "shared_log": {"round": 0, "entries": []},
}


def load(path: str) -> dict:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return json.loads(json.dumps(EMPTY_STATE))
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # fill in anything missing so old files still work
    return {**json.loads(json.dumps(EMPTY_STATE)), **data}


def save(path: str, state: dict) -> None:
    """Write to a temp file then rename, so a crash mid-write can't corrupt the state."""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
