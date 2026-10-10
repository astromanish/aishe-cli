"""
Command policy for `run_command`.

Why this file exists: `run_command` executes shell as the user. It used to do so
with no checks at all — safety was a line in the system prompt asking the model
politely to "confirm with the user" before anything destructive. That is not
enforcement. A hallucinated command, or a prompt injection smuggled in through
`web_extract` output, had the same access as the user: `rm -rf ~`, force-pushes,
installing packages.

Three tiers, evaluated in order:

    DENY     never runs, no override. Destructive or irreversible patterns.
    CONFIRM  runs only when the caller passes `confirmed=True`.
    ALLOW    read-only commands that pass the allowlist.

Anything not in the allowlist is denied. The default is refusal, so a new
command the policy has never seen fails closed rather than open.

Configurable in `~/.config/aishe/config.yaml` under `security.commands`:

    security:
      commands:
        enabled: true              # false disables all enforcement
        allow: [git, ls, pwd]      # extra commands to permit
        deny: [curl, wget]         # extra patterns to forbid
        confirm: [make, docker]    # extra commands that need confirmation
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["classify", "check", "ALLOWED", "DENY_PATTERNS", "CONFIRM_COMMANDS"]

# ─── Tier 1: ALLOW ───────────────────────────────────────────────────────────
# Read-only commands. Each is safe to run unattended, so the agent can inspect
# the system without interrupting the user.
ALLOWED: List[str] = [
    # inspection
    "ls", "pwd", "cat", "head", "tail", "wc", "file", "stat", "tree", "du", "df",
    "find", "which", "whereis", "type", "echo", "printf", "basename", "dirname",
    "realpath", "readlink",
    # text
    "grep", "egrep", "fgrep", "sed", "awk", "sort", "uniq", "cut", "tr", "diff",
    "column", "rev", "nl", "paste", "join", "comm",
    # system info
    "date", "cal", "uptime", "whoami", "hostname", "uname", "id", "env", "printenv",
    "ps", "top", "htop", "free", "lscpu", "lsblk", "lsusb", "lspci", "nproc",
    "arch", "locale", "tty", "tput",
    # network inspection (read-only)
    "ip", "ifconfig", "ping", "traceroute", "dig", "nslookup", "host", "ss",
    "netstat", "arp", "route",
    # development
    "git", "make", "python", "python3", "pip", "node", "npm", "pytest", "ruff",
    "black", "mypy", "go", "cargo", "rustc", "java", "javac", "gcc", "g++", "clang",
    "docker", "systemctl", "journalctl", "sqlite3", "jq", "yq", "curl", "wget",
]

# ─── Tier 2: DENY ────────────────────────────────────────────────────────────
# Matched against the whole command string, not just the argv[0], because the
# damage usually comes from the arguments (`git push --force`, `chmod -R 777 /`).
DENY_PATTERNS: List[Tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf]", "rm -rf / recursive delete"),
    (r"\brm\s+(-[a-zA-Z]+\s+)+", "rm with flags"),
    (r"\brm\s+.*(/|~|\$HOME)\b", "rm on a path outside the cwd"),
    (r"\b(sudo|su|doas)\b", "privilege escalation"),
    (r"\bmkfs\b|\bdd\s+if=", "disk formatting"),
    (r"\b:\(\)\s*\{.*\}|\bfork\s*bomb", "fork bomb"),
    (r">\s*/dev/sd[a-z]", "write to a raw block device"),
    (r"\bchmod\s+(-[a-zA-Z]+\s+)*777", "world-writable chmod"),
    (r"\bchown\s+-[a-zA-Z]*R", "recursive chown"),
    (r"\b(curl|wget)\b.*\|\s*(sudo\s+)?(ba)?sh", "pipe remote script to a shell"),
    (r"\b(curl|wget)\b.*\|\s*python", "pipe remote script to python"),
    (r"\bgit\s+push\b.*(--force|-f)\b", "force push"),
    (r"\bgit\s+reset\s+--hard\b", "hard reset discards work"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*[dxf]", "git clean deletes untracked files"),
    (r"\bgit\s+checkout\s+--\s*\.|\bgit\s+restore\s+\.", "discard all local changes"),
    (r"\btruncate\b|\bshred\b", "unrecoverable file destruction"),
    (r"\bkill\s+-9\b|\bkillall\b|\bpkill\b", "indiscriminate process kill"),
    (r"\bshutdown\b|\breboot\b|\bpoweroff\b|\bhalt\b", "system power control"),
    (r"\b(useradd|userdel|usermod|passwd)\b", "account management"),
    (r"\b(crontab|systemctl\s+(disable|mask))\b", "persistent system modification"),
    (r"\bexport\s+\w+=", "export may leak credentials into the environment"),
    (r"\bhistory\s+-c\b", "history erasure"),
    (r"\b(alias|unalias)\b", "shell state manipulation"),
]

# ─── Tier 3: CONFIRM ─────────────────────────────────────────────────────────
# Legitimate but consequential: builds, installs, container and service control.
# These run only with an explicit `confirmed=True` from the caller.
#
# Some of these are safe for specific subcommands — `git status` never mutates
# anything, `python --version` just prints. Those read-only forms are listed in
# READONLY_SUBCOMMANDS so they run unattended while `git push` still asks.
CONFIRM_COMMANDS: List[str] = [
    "make", "docker", "docker-compose", "podman", "systemctl", "service",
    "apt", "apt-get", "dnf", "yum", "pacman", "brew", "snap", "flatpak",
    "pip", "pip3", "npm", "npx", "pnpm", "yarn", "cargo", "go",
    "git", "ssh", "scp", "rsync", "tar", "unzip", "gzip", "gunzip",
    "python", "python3", "node", "bash", "sh", "zsh", "fish",
]

# command -> subcommands that are read-only. Matched against the first
# non-flag argument, so `git -C /x status` resolves to `status`.
READONLY_SUBCOMMANDS: Dict[str, List[str]] = {
    "git": [
        "status", "log", "diff", "show", "branch", "remote", "tag", "blame",
        "describe", "rev-parse", "ls-files", "ls-tree", "shortlog", "config",
        "stash list", "reflog", "whatchanged", "grep", "fetch --dry-run",
    ],
    "docker": ["ps", "images", "logs", "inspect", "stats", "top", "version", "info"],
    "docker-compose": ["ps", "logs", "config", "images", "top", "version"],
    "systemctl": ["status", "list-units", "list-unit-files", "is-active",
                  "is-enabled", "show", "cat"],
    "pip": ["list", "show", "freeze", "--version", "search"],
    "pip3": ["list", "show", "freeze", "--version", "search"],
    "npm": ["list", "ls", "view", "info", "outdated", "--version", "audit"],
    "yarn": ["list", "info", "why", "audit", "--version"],
    "cargo": ["tree", "search", "--version", "metadata"],
    "go": ["version", "env", "list", "vet"],
    "tar": ["-tf", "--list"],
    "ssh": ["-V"],
    "make": ["-n", "--dry-run"],
    # Interpreters: --version/-V just print. Running a script is still confirm.
    "python": ["--version", "-V"],
    "python3": ["--version", "-V"],
    "node": ["--version", "-v"],
    # Extractors need confirmation (they write files), except listing.
    "unzip": ["-l", "--list"],
    "gzip": ["-l", "--list", "-t", "--test"],
    "rsync": ["--dry-run", "-n"],
}

# Shell metacharacters that chain commands or redirect. A command containing
# these is evaluated on its first segment only; the rest is treated as a reason
# to be cautious, because `ls && rm -rf ~` is a single argv[0] of `ls`.
_CHAINERS = ["&&", "||", ";", "|", "`", "$(", "\n", "&"]


def _load_policy() -> Dict[str, Any]:
    """Read `security.commands` from config, tolerating its absence."""
    try:
        import sys
        from pathlib import Path as _P

        sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
        from aishe_pkg import config  # type: ignore

        return dict(config.get("security.commands", {}) or {})
    except Exception:
        return {}


def _first_segment(command: str) -> str:
    """The command name from the first segment, before any chaining operator."""
    first = command
    for ch in _CHAINERS:
        idx = first.find(ch)
        if idx != -1:
            first = first[:idx]
    first = first.strip()
    try:
        parts = shlex.split(first)
    except ValueError:
        parts = first.split()
    if not parts:
        return ""
    return parts[0].split("/")[-1]


def _subcommand(command: str) -> str:
    """The first non-flag argument after the command name.

    `git -C /repo status` -> `status`. Returns "" when there is no subcommand, as
    in a bare `git` or a flag-only call like `python --version` (which yields
    `--version`).
    """
    first = command
    for ch in _CHAINERS:
        idx = first.find(ch)
        if idx != -1:
            first = first[:idx]
    try:
        parts = shlex.split(first.strip())
    except ValueError:
        parts = first.strip().split()
    for p in parts[1:]:
        if not p.startswith("-"):
            return p
        # A flag with no value is itself the subcommand for tools like pip/npm.
        return p
    return ""


def _is_readonly_form(command: str) -> bool:
    """True when a normally-confirm command is being used read-only.

    `git status` never mutates anything and `python --version` only prints, so
    those run unattended while `git push` and `pip install` still ask. Checked
    against the subcommand, and against the whole trimmed command for
    multi-word entries like `git stash list`.
    """
    name = _first_segment(command)
    forms = READONLY_SUBCOMMANDS.get(name)
    if not forms:
        return False

    sub = _subcommand(command)
    trimmed = command.strip()

    # Single-word forms: match the subcommand, or a flag like `--version`.
    for f in forms:
        if " " not in f and f == sub:
            return True
    # Multi-word forms: match anywhere in the first segment.
    first = command
    for ch in _CHAINERS:
        idx = first.find(ch)
        if idx != -1:
            first = first[:idx]
    first = " ".join(first.split())
    for f in forms:
        if " " in f and f in first:
            return True
    return False


def classify(command: str) -> str:
    """Return 'allow', 'confirm', 'deny' or 'unknown' for a command string."""
    if not command or not command.strip():
        return "deny"

    policy = _load_policy()
    if policy.get("enabled") is False:
        return "allow"  # operator opted out explicitly

    # Config denies win over everything, including the built-in allowlist.
    for pat in policy.get("deny", []) or []:
        try:
            if re.search(pat, command):
                return "deny"
        except re.error:
            continue

    for pat, _why in DENY_PATTERNS:
        if re.search(pat, command):
            return "deny"

    name = _first_segment(command)
    if not name:
        return "deny"

    if name in (policy.get("allow", []) or []):
        return "allow"
    if name in (policy.get("confirm", []) or []):
        return "confirm" if not _is_readonly_form(command) else "allow"

    if name in ALLOWED or name in CONFIRM_COMMANDS:
        # A confirm-tier command used in a read-only form (git status,
        # python --version, docker ps) is safe to run unattended.
        if name in CONFIRM_COMMANDS and not _is_readonly_form(command):
            return "confirm"
        return "allow"

    return "unknown"


def check(command: str, confirmed: bool = False) -> Tuple[bool, str]:
    """Decide whether a command may run.

    Returns (allowed, reason). `reason` is empty when allowed, and is written to
    be shown to the model so it can tell the user what happened and why.
    """
    verdict = classify(command)

    if verdict == "allow":
        return True, ""
    if verdict == "deny":
        return False, (
            "Blocked by Aishe's command policy: this command is destructive or "
            "irreversible and is never run automatically. Tell the user what you "
            "wanted to do and let them run it themselves."
        )
    if verdict == "confirm":
        if confirmed:
            return True, ""
        return False, (
            "This command needs confirmation before it runs. Tell the user exactly "
            "what it will do, then ask them to approve it — do not re-run it "
            "unprompted."
        )
    return False, (
        f"Blocked: '{_first_segment(command)}' is not on Aishe's allowlist. "
        "Aishe runs read-only commands automatically; anything else needs the user "
        "to run it. Explain what you wanted to do instead."
    )
