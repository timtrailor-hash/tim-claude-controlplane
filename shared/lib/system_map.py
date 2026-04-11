"""system_map.py — canonical reader for machines/<host>/system_map.yaml.

Every consumer (health_check.py, verify.sh via subprocess, drift_check.sh,
deploy.sh, system_inventory.sh) imports from here. No consumer carries a
hardcoded copy of anything in system_map.yaml.

Uses a minimal YAML parser (no external dependencies) so this module can
be imported from a LaunchAgent environment where pip packages may not be
installed.
"""

from __future__ import annotations
import os
import re
import sys
from pathlib import Path


def _repo_root() -> Path:
    """Locate tim-claude-controlplane repo root by walking up from this file."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "deploy.sh").exists() and (parent / "shared").is_dir():
            return parent
    raise RuntimeError("tim-claude-controlplane repo root not found")


def _machine() -> str:
    # Env override for testing; otherwise detect from hostname
    override = os.environ.get("SYSTEM_MAP_MACHINE")
    if override:
        return override
    hn = os.uname().nodename.lower()
    if "mini" in hn:
        return "mac-mini"
    return "laptop"


def _system_map_path() -> Path:
    return _repo_root() / "machines" / _machine() / "system_map.yaml"


def _try_pyyaml():
    try:
        import yaml  # type: ignore
        return yaml
    except ImportError:
        return None


def _mini_yaml_parse(text: str) -> dict:
    """Minimal YAML subset parser sufficient for system_map.yaml.

    Supports:
    - key: value (scalars)
    - nested mappings via indentation
    - lists: - item
    - comments with #
    - quoted strings (single and double)

    Does NOT support: anchors, aliases, multi-line scalars, flow style.
    Good enough for structured config files.
    """
    lines = text.splitlines()
    root: dict = {}
    stack: list[tuple[int, object]] = [(-1, root)]

    def _parse_scalar(v: str):
        v = v.strip()
        if not v:
            return ""
        if (v.startswith('"') and v.endswith('"')) or (
            v.startswith("'") and v.endswith("'")
        ):
            return v[1:-1]
        if v.lower() in ("true", "yes"):
            return True
        if v.lower() in ("false", "no"):
            return False
        if v.lower() in ("null", "~"):
            return None
        try:
            if "." in v:
                return float(v)
            return int(v)
        except ValueError:
            return v

    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        # strip comments (only # not inside a quoted string — simple heuristic)
        stripped = raw
        in_str = None
        out_chars = []
        for ch in raw:
            if in_str:
                out_chars.append(ch)
                if ch == in_str:
                    in_str = None
            elif ch in ('"', "'"):
                out_chars.append(ch)
                in_str = ch
            elif ch == "#":
                break
            else:
                out_chars.append(ch)
        stripped = "".join(out_chars).rstrip()
        if not stripped.strip():
            continue

        indent = len(stripped) - len(stripped.lstrip(" "))
        content = stripped.lstrip(" ")

        # Unwind stack to current indent
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else root

        if content.startswith("- "):
            # list item
            item_text = content[2:].strip()
            if not isinstance(parent, list):
                # can't handle: parent must be list
                continue
            if ":" in item_text and not item_text.startswith("["):
                # item is a mapping in-line (rare in our file)
                k, _, v = item_text.partition(":")
                obj = {k.strip(): _parse_scalar(v)}
                parent.append(obj)
                stack.append((indent + 2, obj))
            else:
                parent.append(_parse_scalar(item_text))
            continue

        # mapping entry
        if ":" in content:
            k, _, v = content.partition(":")
            k = k.strip()
            v = v.strip()
            if v == "":
                # nested mapping or list to follow
                # peek next non-empty line to decide
                j = i
                while j < len(lines):
                    nxt = lines[j]
                    if not nxt.strip() or nxt.lstrip(" ").startswith("#"):
                        j += 1
                        continue
                    nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                    nxt_content = nxt.lstrip(" ")
                    if nxt_indent <= indent:
                        # empty container at this key
                        if isinstance(parent, dict):
                            parent[k] = None
                        break
                    if nxt_content.startswith("- "):
                        new_container: object = []
                    else:
                        new_container = {}
                    if isinstance(parent, dict):
                        parent[k] = new_container
                    stack.append((indent, new_container))
                    break
                else:
                    if isinstance(parent, dict):
                        parent[k] = None
            else:
                if v.startswith("[") and v.endswith("]"):
                    inner = v[1:-1].strip()
                    if not inner:
                        parent[k] = []
                    else:
                        parent[k] = [_parse_scalar(p) for p in inner.split(",")]
                else:
                    if isinstance(parent, dict):
                        parent[k] = _parse_scalar(v)

    return root


# ---------------------------------------------------------------------------
# Schema validation — catches typos in field names, invalid probe types,
# missing required fields, and wrong types. Runs at parse time and as a
# dedicated verify.sh check.
#
# A faulty source of truth is more dangerous than a distributed one.
# -- Gemini 2.5 Pro, midway review 2026-04-11
# ---------------------------------------------------------------------------

_VALID_PROBE_TYPES = {"http", "file_age", "process", "keychain", "tcp"}
_VALID_FAILURE_MODES = {"GREEN", "YELLOW", "ORANGE", "RED", "LOW"}
_REQUIRED_SERVICE_FIELDS = {"label", "purpose", "mode", "failure_mode"}
_OPTIONAL_SERVICE_FIELDS = {
    "owner", "code", "binary", "port", "secrets", "failure_impact", "probe",
    "notes", "auto_fix", "risk_note",
}
_VALID_SERVICE_FIELDS = _REQUIRED_SERVICE_FIELDS | _OPTIONAL_SERVICE_FIELDS

_VALID_PROBE_FIELDS_BY_TYPE = {
    "http": {"type", "url", "expect_status", "timeout_s", "expect_json_keys", "notes"},
    "file_age": {"type", "path", "max_age_hours", "max_age_minutes", "notes"},
    "process": {"type", "name", "notes"},
    "keychain": {"type", "service", "account", "notes"},
    "tcp": {"type", "host", "port", "timeout_s", "notes"},
}


def validate(sm: dict | None = None) -> list[str]:
    """Validate a system_map dict against the schema. Returns a list of
    issue strings (empty list == valid)."""
    if sm is None:
        sm = load()
    issues: list[str] = []

    # Top-level required keys
    for key in ("schema_version", "machine", "services"):
        if key not in sm:
            issues.append(f"top-level: missing required key '{key}'")

    if sm.get("schema_version") != 1:
        issues.append(
            f"top-level: schema_version must be 1, got {sm.get('schema_version')!r}"
        )

    # Services
    services = sm.get("services") or {}
    if not isinstance(services, dict):
        issues.append(f"services: must be a mapping, got {type(services).__name__}")
        return issues

    for name, entry in services.items():
        prefix = f"services.{name}"
        if entry is None:
            # empty services dict on laptop is legitimate
            continue
        if not isinstance(entry, dict):
            issues.append(f"{prefix}: must be a mapping, got {type(entry).__name__}")
            continue
        # Unknown fields (typo detector)
        unknown = set(entry.keys()) - _VALID_SERVICE_FIELDS
        if unknown:
            issues.append(f"{prefix}: unknown fields {sorted(unknown)}")
        # Missing required fields
        missing = _REQUIRED_SERVICE_FIELDS - set(entry.keys())
        if missing:
            issues.append(f"{prefix}: missing required fields {sorted(missing)}")
        # failure_mode must be valid
        fm = entry.get("failure_mode")
        if fm and fm not in _VALID_FAILURE_MODES:
            issues.append(
                f"{prefix}: failure_mode {fm!r} not in {sorted(_VALID_FAILURE_MODES)}"
            )
        # label must look like com.timtrailor.<short>
        label = entry.get("label", "")
        if label and not label.startswith("com.timtrailor."):
            issues.append(
                f"{prefix}: label {label!r} must start with 'com.timtrailor.'"
            )
        # Validate probe if present
        probe = entry.get("probe")
        if probe is not None:
            if not isinstance(probe, dict):
                issues.append(f"{prefix}.probe: must be a mapping")
            else:
                ptype = probe.get("type")
                if ptype not in _VALID_PROBE_TYPES:
                    issues.append(
                        f"{prefix}.probe.type: {ptype!r} not in {sorted(_VALID_PROBE_TYPES)}"
                    )
                else:
                    valid_fields = _VALID_PROBE_FIELDS_BY_TYPE[ptype]
                    probe_unknown = set(probe.keys()) - valid_fields
                    if probe_unknown:
                        issues.append(
                            f"{prefix}.probe ({ptype}): unknown fields {sorted(probe_unknown)}"
                        )
                    # Check required probe-specific fields
                    if ptype == "http" and "url" not in probe:
                        issues.append(f"{prefix}.probe: http probe missing 'url'")
                    if ptype == "file_age":
                        if "path" not in probe:
                            issues.append(f"{prefix}.probe: file_age probe missing 'path'")
                        if "max_age_hours" not in probe and "max_age_minutes" not in probe:
                            issues.append(
                                f"{prefix}.probe: file_age probe missing 'max_age_hours' or 'max_age_minutes'"
                            )
                    if ptype == "process" and "name" not in probe:
                        issues.append(f"{prefix}.probe: process probe missing 'name'")
                    if ptype == "keychain":
                        if "service" not in probe:
                            issues.append(f"{prefix}.probe: keychain probe missing 'service'")
                        if "account" not in probe:
                            issues.append(f"{prefix}.probe: keychain probe missing 'account'")
                    if ptype == "tcp":
                        if "host" not in probe:
                            issues.append(f"{prefix}.probe: tcp probe missing 'host'")
                        if "port" not in probe:
                            issues.append(f"{prefix}.probe: tcp probe missing 'port'")

    # User-visible outputs: must have path, producer, consumer
    uv = sm.get("user_visible_outputs") or {}
    if isinstance(uv, dict):
        for name, entry in uv.items():
            prefix = f"user_visible_outputs.{name}"
            if not isinstance(entry, dict):
                continue
            for f in ("path", "producer", "consumer"):
                if f not in entry:
                    issues.append(f"{prefix}: missing required field '{f}'")

    # Canonical paths: must be absolute
    paths = sm.get("canonical_paths") or {}
    if isinstance(paths, dict):
        for name, path_val in paths.items():
            if not isinstance(path_val, str):
                continue
            if not path_val.startswith("/"):
                issues.append(
                    f"canonical_paths.{name}: {path_val!r} must be absolute"
                )

    return issues


def load() -> dict:
    """Load the system_map.yaml for the current machine. Returns a dict."""
    p = _system_map_path()
    if not p.exists():
        return {}
    text = p.read_text()
    yaml = _try_pyyaml()
    if yaml is not None:
        try:
            return yaml.safe_load(text) or {}
        except Exception:
            pass
    return _mini_yaml_parse(text)


def service_labels() -> list[str]:
    """Return the list of LaunchAgent labels that should be loaded."""
    sm = load()
    services = sm.get("services") or {}
    labels = []
    for name, entry in services.items():
        if isinstance(entry, dict) and entry.get("label"):
            labels.append(entry["label"])
        else:
            labels.append(f"com.timtrailor.{name}")
    return sorted(labels)


def services() -> dict:
    """Return the full services dict."""
    return load().get("services") or {}


def canonical_paths() -> dict:
    return load().get("canonical_paths") or {}


def user_visible_outputs() -> dict:
    return load().get("user_visible_outputs") or {}


def memory_repos() -> dict:
    return load().get("memory_repos") or {}


def deprecated() -> dict:
    return load().get("deprecated") or {}


if __name__ == "__main__":
    # CLI for quick inspection and shell consumption
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dump"
    if cmd == "labels":
        for lbl in service_labels():
            print(lbl)
    elif cmd == "paths":
        for k, v in canonical_paths().items():
            print(f"{k}={v}")
    elif cmd == "user_outputs":
        for name, entry in user_visible_outputs().items():
            if isinstance(entry, dict):
                path = entry.get("path", "")
                probe_url = entry.get("probe_url", "")
                required = entry.get("required_on_deploy", False)
                required_str = "required" if required else "optional"
                print(f"{name}|{path}|{probe_url}|{required_str}")
    elif cmd == "deprecated_labels":
        for name, entry in deprecated().items():
            if isinstance(entry, dict):
                print(f"com.timtrailor.{name}")
    elif cmd == "validate":
        sm = load()
        issues = validate(sm)
        if issues:
            print(f"INVALID: {len(issues)} issue(s)")
            for i in issues:
                print(f"  - {i}")
            sys.exit(2)
        print(
            f"OK: schema_version={sm.get('schema_version')} "
            f"services={len(sm.get('services') or {})} "
            f"paths={len(sm.get('canonical_paths') or {})} "
            f"probes={sum(1 for s in (sm.get('services') or {}).values() if isinstance(s, dict) and s.get('probe'))}"
        )
    else:
        import json
        print(json.dumps(load(), indent=2, default=str))
