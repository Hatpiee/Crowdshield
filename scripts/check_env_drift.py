"""Compares the developer's real .env against .env.example, key by key.

Simple, boring, manual check — NOT run automatically (no pre-commit hook,
no CI). Run this after pulling any phase's changes that touched
.env.example, before assuming your local .env is up to date.

WHY THIS EXISTS: a real .env silently shadowing a newer, intentional
config.py default (via pydantic-settings' env-file precedence over class
defaults) has caused genuine, reproduced bugs three separate times in this
project's history — Phase 6, Phase 13 (RISK_ELEVATED_THRESHOLD/
RISK_CRITICAL_THRESHOLD/RISK_INCIDENT_THRESHOLD stuck on Phase 1's old
0-1 scale), and Phase 14 (VLM_MODEL stuck on "placeholder-vlm", making
Vision Intelligence fail to construct at all). See DECISIONS.md's
"Implementation-Discovered Constraints" entries for the concrete incidents.

This script does NOT try to guess which differences are "real bugs" vs.
"intentional local overrides" (e.g. your own DATABASE_URL password is
SUPPOSED to differ from .env.example's placeholder) — it just prints every
shared key's value in both files side by side, flagged wherever they
differ, and lets a human decide. Deliberately dumb by design, stdlib only
— no venv activation required to run it.

Usage: python scripts/check_env_drift.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def main() -> None:
    if not ENV_EXAMPLE_PATH.exists():
        print(f"FATAL: {ENV_EXAMPLE_PATH} not found.")
        sys.exit(1)
    if not ENV_PATH.exists():
        print(
            f"No .env found at {ENV_PATH} — nothing to compare. Run "
            "`cp .env.example .env` first (see README.md's Setup section)."
        )
        sys.exit(1)

    env_values = _parse_env_file(ENV_PATH)
    example_values = _parse_env_file(ENV_EXAMPLE_PATH)

    shared_keys = sorted(set(env_values) & set(example_values))
    only_in_env = sorted(set(env_values) - set(example_values))
    only_in_example = sorted(set(example_values) - set(env_values))

    print(f"Comparing {ENV_PATH}")
    print(f"     against {ENV_EXAMPLE_PATH}")
    print(
        f"{len(shared_keys)} shared keys, {len(only_in_env)} only in .env, "
        f"{len(only_in_example)} only in .env.example"
    )
    print()

    differing = [k for k in shared_keys if env_values[k] != example_values[k]]
    same = [k for k in shared_keys if env_values[k] == example_values[k]]

    if differing:
        print(f"=== DIFFERS ({len(differing)}) ===")
        print(
            "A difference is NOT automatically wrong (e.g. your own "
            "DATABASE_URL password is expected to differ) — but IS worth a "
            "deliberate look. This is exactly the class of thing that has "
            "caused real bugs before (see this script's own docstring)."
        )
        for key in differing:
            print(f"  {key}")
            print(f"    .env         = {env_values[key]!r}")
            print(f"    .env.example = {example_values[key]!r}")
        print()

    if only_in_example:
        print(f"=== IN .env.example BUT NOT YOUR .env ({len(only_in_example)}) ===")
        print("New keys from a phase you haven't picked up yet — consider adding them.")
        for key in only_in_example:
            print(f"  {key} = {example_values[key]!r}")
        print()

    if only_in_env:
        print(f"=== IN YOUR .env BUT NOT .env.example ({len(only_in_env)}) ===")
        print("A leftover key .env.example no longer documents — probably fine, worth a glance.")
        for key in only_in_env:
            print(f"  {key}")
        print()

    print(f"=== SAME VALUE IN BOTH ({len(same)}) ===")
    print(("  " + ", ".join(same)) if same else "  (none)")


if __name__ == "__main__":
    main()
