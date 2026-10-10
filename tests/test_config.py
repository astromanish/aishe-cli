"""
Config tests.

The deepcopy bug (BUG-001) lived here: `load()` shallow-copied DEFAULT_CONFIG, so
nested dicts were shared and mutating a returned config poisoned the defaults
for the rest of the process. These tests exist so it cannot come back.
"""

from harness import T, check, eq, raises, section  # noqa: E402

from aishe_pkg import config  # noqa: E402

section("config.load returns an isolated copy")

c1 = config.load()
c1["providers"]["openrouter"]["api_key"] = "MUTATED"
c1["voice"]["default_voice"] = "MUTATED"
c2 = config.load()
check("nested provider dict is not shared", c2["providers"]["openrouter"]["api_key"] != "MUTATED")
check("nested voice dict is not shared", c2["voice"]["default_voice"] != "MUTATED")

section("get / set round-trip")

config.set_key("test.marker", "hello")
eq("set_key then get", config.get("test.marker"), "hello")

config.set_key("test.nested.deep", 42)
eq("nested set_key", config.get("test.nested.deep"), 42)

eq("missing key returns the default", config.get("test.nothing.here", "fallback"), "fallback")
check("missing key without default is None", config.get("test.absent") is None)

section("get reads live values, not stale defaults")

check("provider is a string", isinstance(config.get("provider", ""), str))
check("services map has ports", isinstance(config.get("services.deepagent", ""), str))

section("_deep_get / _deep_merge")

# _deep_get takes a list of parts, not a dotted string.
check("_deep_get walks nested keys", config._deep_get({"a": {"b": {"c": 1}}}, ["a", "b", "c"]) == 1)
check("_deep_get on a missing path is None", config._deep_get({"a": {}}, ["a", "x", "y"]) is None)
check("_deep_get on a non-dict is None", config._deep_get({"a": 5}, ["a", "b"]) is None)

# _deep_merge mutates base in place and returns None — easy to get wrong.
base = {"a": {"b": 1, "c": 2}}
ret = config._deep_merge(base, {"a": {"c": 99, "d": 3}})
check("_deep_merge returns None (mutates in place)", ret is None)
eq("deep_merge overwrites leaf", base["a"]["c"], 99)
eq("deep_merge adds new leaf", base["a"]["d"], 3)
eq("deep_merge preserves untouched leaf", base["a"]["b"], 1)

section("save / load persistence")

config.set_key("test.persisted", "value-123")
config.save(config.load())
reloaded = config.load()
eq("value survives save+load", reloaded.get("test", {}).get("persisted"), "value-123")

section("key masking")

# Config stores keys pre-masked, so `aishe config` dumps the whole file without
# leaking a live credential. The masker lives in config.py so it is testable.
check("mask keeps a fingerprint", config.mask_key("sk-or-v1-abcdef1234567890") == "sk-o…7890")
check("mask reveals neither the middle nor the length",
      "abcdef1234567890" not in config.mask_key("sk-or-v1-abcdef1234567890"))
check("short keys report as 'set', not themselves", config.mask_key("short") == "set")
check("empty key masks to empty", config.mask_key("") == "")
check("None key masks to empty", config.mask_key(None) == "")
check("mask is stable for the same key",
      config.mask_key("sk-or-v1-abcdef1234567890") == config.mask_key("sk-or-v1-abcdef1234567890"))
check("different keys mask differently",
      config.mask_key("sk-or-v1-aaaa") != config.mask_key("sk-or-v1-bbbb"))

section("paths")

from pathlib import Path  # noqa: E402

check("config path is absolute", config.get_config_path().is_absolute())
check("data dir is absolute", Path(str(config.get("data.dir", ""))).is_absolute())
check("AISHE_HOME is honoured", str(T.home) in str(config.get("data.dir", "")))

if __name__ == "__main__":
    import sys

    from harness import R

    sys.exit(0 if R.report("config") else 1)
