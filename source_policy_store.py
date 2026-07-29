"""Storage boundary for public source-policy defaults and local approvals."""

import json
import os
import uuid
from pathlib import Path


class SourcePolicyStoreError(RuntimeError):
    """Raised when source-policy state cannot be loaded or saved."""


def load_or_initialize(local_path: Path, default_path: Path) -> dict:
    """Load local policy, initializing it from the public default if absent."""
    try:
        return _read_policy(local_path)
    except FileNotFoundError:
        try:
            default_policy = _read_policy(default_path)
        except (FileNotFoundError, json.JSONDecodeError, OSError) as error:
            raise SourcePolicyStoreError(
                f"Default source policy could not be read at {default_path}."
            ) from error

        save(local_path, default_policy)
        return default_policy
    except (json.JSONDecodeError, OSError) as error:
        raise SourcePolicyStoreError(
            f"Local source policy could not be read at {local_path}."
        ) from error


def save(path: Path, policy: dict) -> None:
    """Atomically save one local source policy."""
    if not isinstance(policy, dict):
        raise SourcePolicyStoreError("Source policy must be a JSON object.")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary_path.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as output:
            json.dump(policy, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise SourcePolicyStoreError(
            f"Source policy could not be saved at {path}."
        ) from error


def _read_policy(path: Path) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise SourcePolicyStoreError("Source policy must be a JSON object.")
    return policy
