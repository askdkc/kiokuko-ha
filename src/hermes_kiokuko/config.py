from copy import deepcopy
from pathlib import Path

from .errors import KiokukoError
from .filesystem import atomic_write, file_lock, private_directory

DEFAULTS = {
    "schema_version": 1,
    "verified_compaction": {"enabled": True},
    "write_policy": "explicit_verbatim_or_human_approval_or_file_verification",
    "context_injection": {"enabled": True, "source": "pre_llm_call", "max_entries": 8,
                          "max_chars": 2200, "min_authority": 70, "min_confidence": 0.80},
    "explicit_commands": {"enabled": True, "max_body_chars": 600,
                          "allowed_scopes": ["principal", "principal_workspace"]},
    "passive_capture": {"enabled": True, "max_evidence_chars": 600,
                        "detect_explicit_remember_requests": True, "detect_corrections": True},
    "promotion": {"from_verified_explicit_command": True, "from_model_proposal": False,
                  "from_background_review": False, "from_cron": False, "from_group_chat": False,
                  "from_delegation": False, "from_tool_observation": False},
    "retrieval": {"lexical": True, "semantic": False, "candidate_limit": 64},
    "security": {"reject_secrets": True, "reject_prompt_injection": True,
                 "reject_invisible_unicode": True, "store_external_ids": False},
    "storage": {"synchronous": "full", "busy_timeout_ms": 2500, "read_timeout_ms": 150},
}


def read_yaml(path: Path) -> dict:
    import yaml
    try:
        if path.is_symlink():
            raise KiokukoError("UNSAFE_PATH")
        value = yaml.safe_load(path.read_text()) if path.exists() else {}
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise KiokukoError("INVALID_CONFIG")
        return value
    except (OSError, yaml.YAMLError):
        raise KiokukoError("INVALID_CONFIG") from None


def write_yaml(path: Path, value: dict) -> None:
    import yaml
    atomic_write(path, yaml.safe_dump(value, allow_unicode=True, sort_keys=False).encode())


def validate_native(home: Path) -> None:
    cfg = read_yaml(home / "config.yaml")
    memory = cfg.get("memory", {})
    if not isinstance(memory, dict) or memory.get("provider") != "kiokuko" or \
            memory.get("memory_enabled") is not False or memory.get("user_profile_enabled") is not False:
        raise KiokukoError("NATIVE_MEMORY_CONFIG")
    if cfg.get("compression", {}).get("checkpoint_required", False):
        raise KiokukoError("UNSUPPORTED_CHECKPOINT")
    plugins = cfg.get("plugins", {})
    if "kiokuko-tools" not in plugins.get("enabled", []) or "kiokuko-tools" in plugins.get("disabled", []):
        raise KiokukoError("GENERAL_PLUGIN_DISABLED")


def load_config(home: Path) -> dict:
    cfg = deepcopy(DEFAULTS)
    supplied = read_yaml(home / "kiokuko" / "config.yaml")
    if supplied.get("write_policy") == "explicit_verbatim_or_human_approval":
        supplied["write_policy"] = DEFAULTS["write_policy"]
    for key, value in supplied.items():
        if key not in cfg:
            raise KiokukoError("UNKNOWN_CONFIG")
        if isinstance(cfg[key], dict):
            if not isinstance(value, dict) or set(value) - set(cfg[key]):
                raise KiokukoError("UNKNOWN_CONFIG")
            cfg[key].update(value)
        else:
            cfg[key] = value
    adjustable = {("context_injection", "enabled"), ("context_injection", "max_entries"),
                  ("verified_compaction", "enabled"),
                  ("context_injection", "max_chars"), ("context_injection", "min_authority"),
                  ("context_injection", "min_confidence"), ("explicit_commands", "enabled"),
                  ("passive_capture", "enabled"), ("passive_capture", "detect_explicit_remember_requests"),
                  ("passive_capture", "detect_corrections")}
    for key, value in DEFAULTS.items():
        if not isinstance(value, dict):
            if type(cfg[key]) is not type(value) or cfg[key] != value:
                raise KiokukoError("UNSUPPORTED_CONFIG")
        else:
            for name, expected in value.items():
                actual = cfg[key][name]
                if type(actual) is not type(expected) or ((key, name) not in adjustable and actual != expected):
                    raise KiokukoError("UNSUPPORTED_CONFIG")
    inject = cfg["context_injection"]
    if not (1 <= inject["max_entries"] <= 8 and 700 <= inject["max_chars"] <= 2200
            and 70 <= inject["min_authority"] <= 100 and .8 <= inject["min_confidence"] <= 1):
        raise KiokukoError("INVALID_CONFIG")
    return cfg


def setup(home: Path) -> None:
    private_directory(home)
    private_directory(home / "kiokuko")
    with file_lock(home / "kiokuko" / "config.lock", exclusive=True):
        cfg = read_yaml(home / "config.yaml")
        for key in ("memory", "compression"):
            if not isinstance(cfg.setdefault(key, {}), dict):
                raise KiokukoError("INVALID_CONFIG")
        cfg["memory"].update(provider="kiokuko", memory_enabled=False, user_profile_enabled=False)
        cfg["compression"]["checkpoint_required"] = False
        plugins = cfg.setdefault("plugins", {})
        if not isinstance(plugins, dict):
            raise KiokukoError("INVALID_CONFIG")
        for key in ("enabled", "disabled"):
            if not isinstance(plugins.setdefault(key, []), list):
                raise KiokukoError("INVALID_CONFIG")
        if "kiokuko-tools" not in plugins["enabled"]:
            plugins["enabled"].append("kiokuko-tools")
        plugins["disabled"] = [item for item in plugins["disabled"] if item != "kiokuko-tools"]
        write_yaml(home / "config.yaml", cfg)
        if not (home / "kiokuko" / "config.yaml").exists():
            write_yaml(home / "kiokuko" / "config.yaml", DEFAULTS)


def save_config(values: dict, home: Path) -> None:
    # Hermes setup only exposes these two bounded toggles.
    if set(values) - {"passive_capture_enabled", "context_injection_enabled"}:
        raise KiokukoError("UNKNOWN_CONFIG")
    private_directory(home / "kiokuko")
    with file_lock(home / "kiokuko" / "config.lock", exclusive=True):
        cfg = load_config(home)
        for field, value in values.items():
            if type(value) is not bool:
                raise KiokukoError("INVALID_CONFIG")
            cfg[field.removesuffix("_enabled")]["enabled"] = value
        write_yaml(home / "kiokuko" / "config.yaml", cfg)
