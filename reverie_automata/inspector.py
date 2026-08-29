"""The tool-layer brake — a capability firewall that runs on the *action*, not the plan.

Plan-level risk labels are UX; they can be talked around. The real guard classifies
each concrete tool call at the moment it is made: a write to a protected path, a
privileged shell command, raw network egress to a non-allowlisted host, mass
deletion, or a message to an unverified recipient becomes an approval, filed and
parked, while everything else proceeds and is logged.

This module is pure classification (string/path logic, no I/O) so it is trivially
testable and can be wired into whatever your agent backend exposes as a pre-tool
hook. ``classify`` returns ("allow", "") or ("block", reason).
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any


WRITE_TOOLS = {"write_file": ("path", "file_path"), "patch": ("path", "file_path"),
               "edit_file": ("path", "file_path"), "create_file": ("path", "file_path")}
SHELL_TOOLS = {"terminal", "shell", "bash", "run_command", "exec"}
MSG_TOOLS = {"send_message", "post", "email", "notify", "dm"}
READ_SAFE = {"read_file", "list_dir", "ls", "grep", "glob", "search", "web_search",
             "browse", "fetch", "get", "view", "recall", "think"}

_CMD_BLOCK = (r"\bsudo\b", r"\bsystemctl\b", r"\bcrontab\b", r"\bpip3?\s+install\b",
              r"\bapt(-get)?\s+install\b", r"\bnpm\s+install\s+-g\b", r"\|\s*(ba)?sh\b",
              r"\b(?:sh|bash)\s+-c\b",
              r"\bchmod\s+-?R?\s*777\b", r"\bgit\s+push\b", r"\bmkfs\b", r"\bshutdown\b",
              r"\breboot\b", r"\bdd\b[^|]*of=/dev/")
_EGRESS = r"\b(curl|wget)\b[^|;&]*(\s-(d|F|T|X\s*(POST|PUT|DELETE))|--data|--upload-file|--form)"
_RM_R = r"\brm\s+(?:-\w*r\w*)"
_MUTATORS = r"(\btee\b|\bsed\s+-i\b|\bmv\b|\bcp\b|\brm\b|\bchmod\b|\bchown\b|\btruncate\b)"
_READ_SHAPE = re.compile(r"^(read|get|list|show|view|fetch|browse|browser|snapshot|search|"
                         r"find|grep|glob|scan|inspect|describe|status|check)_?|"
                         r"_(search|read|view|list|get|snapshot|status)$", re.I)
_CAP_HINT = re.compile(r"(?:^|_)(write|edit|patch|append|delete|remove|rename|mkdir|upload|"
                       r"deploy|install|exec|execute|push|send|post|email|overwrite|create|"
                       r"move|put|save|sync|publish)(?:_|$)", re.I)
_SHELL_SHAPE = re.compile(r"(?:^|_)(terminal|shell|bash|cmd|command|exec|execute)(?:_|$)", re.I)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _normalize_tool_name(name: str) -> str:
    split = _CAMEL_BOUNDARY.sub("_", str(name or ""))
    return re.sub(r"[^A-Za-z0-9]+", "_", split).strip("_").lower()


def _shlex(s: str) -> list[str]:
    try:
        return shlex.split(s)
    except Exception:
        return s.split()


def _redirect_targets(command: str) -> list[str]:
    """Return shell file-redirection targets outside quoted regions.

    The scanner recognises ``>``, ``>>``, ``>|``, ``<>``, numbered-fd and
    ``&>`` forms while excluding fd duplication such as ``2>&1`` and ``>&2``.
    It deliberately does not descend into a nested ``sh -c`` program; those
    commands are blocked separately because a string classifier cannot safely
    reproduce a second shell's parser.
    """
    targets: list[str] = []
    n = len(command)
    i = 0

    def skip_quote(pos: int, quote: str) -> int:
        pos += 1
        while pos < n:
            if quote == '"' and command[pos] == "\\" and pos + 1 < n:
                pos += 2
            elif command[pos] == quote:
                return pos + 1
            else:
                pos += 1
        return pos

    def read_target(pos: int) -> tuple[str, int]:
        while pos < n and command[pos].isspace():
            pos += 1
        start = pos
        out: list[str] = []
        while pos < n:
            c = command[pos]
            if c in "'\"":
                end = skip_quote(pos, c)
                out.append(command[pos + 1:max(pos + 1, end - 1)])
                pos = end
                continue
            if c == "\\" and pos + 1 < n:
                out.append(command[pos + 1])
                pos += 2
                continue
            if c.isspace() or c in ";|&":
                break
            out.append(c)
            pos += 1
        return "".join(out) if pos > start else "", pos

    while i < n:
        c = command[i]
        if c in "'\"":
            i = skip_quote(i, c)
            continue
        if c == "\\" and i + 1 < n:
            i += 2
            continue

        op_end = -1
        fd_dup = False
        if command.startswith("<>", i):
            op_end = i + 2
        elif c == ">":
            if command.startswith(">&", i):
                op_end = i + 2
                fd_dup = True
            elif command.startswith(">>", i) or command.startswith(">|", i):
                op_end = i + 2
            else:
                op_end = i + 1

        if op_end < 0:
            i += 1
            continue

        target, end = read_target(op_end)
        if target and not (fd_dup and (target.isdigit() or target == "-")):
            targets.append(target)
        i = max(end, op_end)

    return targets


class Inspector:
    def __init__(self, cfg):
        self.protected = [Path(os.path.expanduser(p)).resolve() for p in cfg.get("protected_paths", [])]
        self.home = cfg.home if hasattr(cfg, "home") else Path(os.path.expanduser(cfg["home"])).resolve()
        self.egress = list(cfg.get("egress_allowlist", []))
        self.recipients = [str(r) for r in cfg.get("allowed_recipients", [])]

    def _is_protected(self, path_str: str) -> bool:
        try:
            rp = Path(os.path.expanduser(str(path_str))).resolve()
        except Exception:
            return True  # unresolvable -> fail closed
        return any(rp == b or str(rp).startswith(str(b) + os.sep) for b in self.protected)

    def _classify_write(self, tn: str, args: dict[str, Any]) -> tuple[str, str]:
        for key in WRITE_TOOLS[tn]:
            if args.get(key) and self._is_protected(args[key]):
                return "block", f"write to protected path: {args[key]}"
        return "allow", ""

    def _classify_message(self, args: dict[str, Any]) -> tuple[str, str]:
        recipient = str(args.get("recipient") or args.get("to") or
                        args.get("chat_id") or args.get("channel") or "")
        if recipient and recipient in self.recipients:
            return "allow", ""
        return "block", f"message to unverified recipient: {recipient or 'unknown'}"

    @staticmethod
    def _restricted_command(cmd: str) -> str:
        for pattern in _CMD_BLOCK:
            if re.search(pattern, cmd):
                return f"restricted command ({pattern}): {cmd[:120]}"
        return ""

    def _blocked_egress(self, cmd: str) -> str:
        if not re.search(_EGRESS, cmd):
            return ""
        domain = re.search(r"https?://([^/\s'\"]+)", cmd)
        if domain and any(domain.group(1).endswith(item) for item in self.egress):
            return ""
        return f"raw egress not allowlisted: {cmd[:120]}"

    def _blocked_recursive_delete(self, cmd: str) -> str:
        if not re.search(_RM_R, cmd):
            return ""
        for match in re.finditer(r"\brm\b(?:\s+-\S+)*\s+(.+?)(?:;|\||&|$)", cmd):
            reason = self._check_delete_targets(cmd, _shlex(match.group(1)))
            if reason:
                return reason
        return ""

    def _check_delete_targets(self, cmd: str, targets: list[str]) -> str:
        for token in targets:
            if token.startswith("-"):
                continue
            if any(mark in token for mark in ("$", "`", "*", "?", "..")):
                return f"unresolvable rm target: {cmd[:120]}"
            path = os.path.expanduser(token)
            path = path if os.path.isabs(path) else str(self.home / path)
            try:
                resolved = str(Path(path).resolve())
            except Exception:
                return f"unresolvable rm target: {cmd[:120]}"
            if not (resolved == str(self.home) or resolved.startswith(str(self.home) + os.sep)):
                return f"recursive delete outside home: {cmd[:120]}"
        return ""

    def _blocked_file_mutation(self, cmd: str) -> str:
        targets = [target for target in _redirect_targets(cmd) if target != "/dev/null"]
        for target in targets:
            if any(mark in target for mark in ("$", "`", "*", "?")):
                return f"unresolvable redirect target: {cmd[:120]}"
            path = os.path.expanduser(target)
            path = path if os.path.isabs(path) else str(self.home / path)
            if self._is_protected(path):
                return f"file redirect writes protected path {path}"
        if targets or re.search(_MUTATORS, cmd):
            for path in re.findall(r"[~/][\w.@/~-]+", cmd):
                if self._is_protected(path):
                    return f"mutating command touches protected path {path}"
        return ""

    def _classify_shell(self, args: dict[str, Any]) -> tuple[str, str]:
        cmd = str(args.get("command") or args.get("cmd") or args.get("input") or "")
        checks = (self._restricted_command, self._blocked_egress,
                  self._blocked_recursive_delete, self._blocked_file_mutation)
        for check in checks:
            reason = check(cmd)
            if reason:
                return "block", reason
        return "allow", ""

    def classify(self, tool_name: str, args: dict[str, Any] | None) -> tuple[str, str]:
        args = args if isinstance(args, dict) else {}
        tn = _normalize_tool_name(tool_name)

        if tn in WRITE_TOOLS:
            return self._classify_write(tn, args)

        if tn in MSG_TOOLS:
            return self._classify_message(args)

        if tn in SHELL_TOOLS or _SHELL_SHAPE.search(tn):
            return self._classify_shell(args)

        if tn in READ_SAFE or _READ_SHAPE.search(tn):
            return "allow", ""
        if _CAP_HINT.search(tn):
            return "block", f"unknown tool '{tn}' with a write/egress-shaped name"
        return "allow", ""
