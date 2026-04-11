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
        assert sm.get("schema_version") == 1, "schema_version must be 1"
        assert sm.get("services"), "services section missing"
        print(f"OK: {len(sm.get('services', {}))} services, {len(sm.get('canonical_paths', {}))} paths")
    else:
        import json
        print(json.dumps(load(), indent=2, default=str))
