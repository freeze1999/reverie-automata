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
              r"\bchmod\s+-?R?\s*777\b", r"\bgit\s+push\b", r"\bmkfs\b", r"\bshutdown\b",
              r"\breboot\b", r"\bdd\b[^|]*of=/dev/")
_EGRESS = r"\b(curl|wget)\b[^|;&]*(\s-(d|F|T|X\s*(POST|PUT|DELETE))|--data|--upload-file|--form)"
_RM_R = r"\brm\s+(?:-\w*r\w*)"
_MUTATORS = r"(>{1,2}|\btee\b|\bsed\s+-i\b|\bmv\b|\bcp\b|\brm\b|\bchmod\b|\bchown\b|\btruncate\b)"
_READ_SHAPE = re.compile(r"^(read|get|list|show|view|fetch|browse|browser|snapshot|search|"
                         r"find|grep|glob|scan|inspect|describe|status|check)_?|"
                         r"_(search|read|view|list|get|snapshot|status)$", re.I)
_CAP_HINT = re.compile(r"(?:^|_)(write|edit|patch|append|delete|remove|rename|mkdir|upload|"
                       r"deploy|install|exec|execute|push|send|post|email|overwrite|create|"
                       r"move|put)(?:_|$)", re.I)


def _shlex(s: str) -> list[str]:
    try:
        return shlex.split(s)
    except Exception:
        return s.split()


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

    def classify(self, tool_name: str, args: dict[str, Any] | None) -> tuple[str, str]:
        args = args if isinstance(args, dict) else {}
        tn = tool_name or ""

        if tn in WRITE_TOOLS:
            for k in WRITE_TOOLS[tn]:
                if args.get(k) and self._is_protected(args[k]):
                    return "block", f"write to protected path: {args[k]}"
            return "allow", ""

        if tn in MSG_TOOLS:
            r = str(args.get("recipient") or args.get("to") or args.get("chat_id") or args.get("channel") or "")
            return ("allow", "") if r and r in self.recipients else ("block", f"message to unverified recipient: {r or 'unknown'}")

        if tn in SHELL_TOOLS:
            cmd = str(args.get("command") or args.get("cmd") or args.get("input") or "")
            for pat in _CMD_BLOCK:
                if re.search(pat, cmd):
                    return "block", f"restricted command ({pat}): {cmd[:120]}"
            if re.search(_EGRESS, cmd):
                dom = re.search(r"https?://([^/\s'\"]+)", cmd)
                if not (dom and any(dom.group(1).endswith(d) for d in self.egress)):
                    return "block", f"raw egress not allowlisted: {cmd[:120]}"
            if re.search(_RM_R, cmd):
                for m in re.finditer(r"\brm\b(?:\s+-\S+)*\s+(.+?)(?:;|\||&|$)", cmd):
                    for tok in _shlex(m.group(1)):
                        if tok.startswith("-"):
                            continue
                        if any(x in tok for x in ("$", "`", "*", "?", "..")):
                            return "block", f"unresolvable rm target: {cmd[:120]}"
                        rp = os.path.expanduser(tok)
                        rp = rp if os.path.isabs(rp) else str(self.home / rp)
                        try:
                            rp = str(Path(rp).resolve())
                        except Exception:
                            return "block", f"unresolvable rm target: {cmd[:120]}"
                        if not (rp == str(self.home) or rp.startswith(str(self.home) + os.sep)):
                            return "block", f"recursive delete outside home: {cmd[:120]}"
            if re.search(_MUTATORS, cmd):
                for p in re.findall(r"[~/][\w.@/~-]+", cmd):
                    if self._is_protected(p):
                        return "block", f"mutating command touches protected path {p}"
            return "allow", ""

        if tn in READ_SAFE or _READ_SHAPE.search(tn):
            return "allow", ""
        if _CAP_HINT.search(tn):
            return "block", f"unknown tool '{tn}' with a write/egress-shaped name"
        return "allow", ""
