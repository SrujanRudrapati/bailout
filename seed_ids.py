"""Resolve who plays each demo roommate for /seed.

Mentions given on the command win. Any slot left empty falls back to the
SEED_<NAME>_ID env var, so a single person can run /seed with no arguments.
"""

from typing import Mapping, Optional

ROOMMATES = ("maya", "jordan", "priya")
SEED_ENV_VARS = {name: f"SEED_{name.upper()}_ID" for name in ROOMMATES}


def _env_id(name: str, env: Mapping[str, str]) -> Optional[int]:
    raw = env.get(SEED_ENV_VARS[name], "").strip()
    return int(raw) if raw.isdigit() else None


def resolve_seed_ids(mentions: Mapping[str, Optional[int]], env: Mapping[str, str]) -> tuple[dict[str, int], list[str]]:
    """Return (name -> discord id, names still missing). Does not mutate inputs."""
    resolved = {name: mentions.get(name) or _env_id(name, env) for name in ROOMMATES}
    ids = {name: discord_id for name, discord_id in resolved.items() if discord_id is not None}
    missing = [name for name in ROOMMATES if name not in ids]
    return ids, missing
