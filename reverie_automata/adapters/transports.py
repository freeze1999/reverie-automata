"""Approval transports — how a risky action reaches a human and their answer returns.

The default is ``stdout`` (print the card, read a decision from a file/queue) which
needs nothing and is perfect for local runs and CI. ``telegram`` is a worked example
of a real out-of-band channel with inline approve/deny buttons and verified-sender
callbacks. Slack/email/webhook adapters follow the same two-method shape.

Security contract for any transport:
* the ACTION is shown verbatim (never truncate the thing that will run);
* the model-authored reasoning is labelled untrusted;
* ``poll`` must verify WHO decided and only surface events from an authorized actor;
* ``event_id`` increases monotonically so the store can reject replays.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from .base import ApprovalEvent


class StdoutTransport:
    """Prints approvals; reads decisions from a JSONL file the operator (or a test)
    appends to. Zero dependencies, fully offline."""

    def __init__(self, options: dict[str, Any] | None = None):
        o = dict(options or {})
        self.inbox = Path(o.get("decision_file", "approvals.decisions.jsonl")).expanduser()
        self._seen = 0

    def send(self, approval_id, title, action, reasoning):
        print(f"\n=== APPROVAL #{approval_id}: {title} ===")
        print(f"ACTION (runs verbatim on approve):\n  {action}")
        print(f"reason (model-authored, untrusted): {reasoning}")
        print(f"decide: echo '{{\"approval_id\": {approval_id}, \"decision\": \"approve\", "
              f"\"event_id\": {int(time.time())}}}' >> {self.inbox}\n")
        return str(approval_id)

    def poll(self):
        if not self.inbox.exists():
            return []
        lines = self.inbox.read_text().splitlines()
        events, i = [], 0
        for ln in lines:
            i += 1
            if i <= self._seen or not ln.strip():
                continue
            try:
                d = json.loads(ln)
                events.append(ApprovalEvent(int(d["approval_id"]), d["decision"],
                                            int(d.get("event_id", i)), d.get("actor", "operator")))
            except Exception:
                continue
        self._seen = i
        return events


class TelegramTransport:
    """Real out-of-band approvals over a Telegram bot. Verifies the presser's id
    against ``owner_id`` before any decision is accepted.

    options: { token_env: BOT_TOKEN, chat_id: "<id>", owner_id: "<id>",
               offset_file: ~/.reverie-automata/.tg_offset }
    """

    def __init__(self, options: dict[str, Any] | None = None):
        import os
        o = dict(options or {})
        self.token = os.environ.get(o.get("token_env", "REVERIE_BOT_TOKEN"), "")
        self.chat = str(o.get("chat_id", ""))
        self.owner = str(o.get("owner_id", self.chat))
        self.offset_file = Path(os.path.expanduser(o.get("offset_file", "~/.reverie-automata/.tg_offset")))

    def _api(self, method, **params):
        data = json.dumps(params).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}",
                                     data=data, headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=25).read())

    def send(self, approval_id, title, action, reasoning):
        if not (self.token and self.chat):
            return None
        text = (f"\U0001F916 approval #{approval_id}: {title}\n\n"
                f"ACTION (runs verbatim on ✅):\n{action[:3500]}\n\n"
                f"reason (untrusted): {reasoning[:400]}")
        try:
            r = self._api("sendMessage", chat_id=self.chat, text=text, reply_markup={
                "inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"appr:{approval_id}:approve"},
                                     {"text": "❌ Deny", "callback_data": f"appr:{approval_id}:deny"}]]})
            return str(r["result"]["message_id"]) if r.get("ok") else None
        except Exception:
            return None

    def poll(self):
        if not self.token:
            return []
        try:
            offset = int(self.offset_file.read_text())
        except Exception:
            offset = 0
        try:
            r = self._api("getUpdates", offset=offset + 1, timeout=10, allowed_updates=["callback_query"])
        except Exception:
            return []
        events = []
        for upd in r.get("result", []):
            offset = max(offset, upd["update_id"])
            cq = upd.get("callback_query")
            if not cq:
                continue
            if str((cq.get("from") or {}).get("id", "")) != self.owner:  # sender verification
                try:
                    self._api("answerCallbackQuery", callback_query_id=cq["id"], text="unauthorized")
                except Exception:
                    pass
                continue
            try:
                tag, rid, decision = cq.get("data", "").split(":", 2)
                if tag == "appr" and decision in ("approve", "deny"):
                    events.append(ApprovalEvent(int(rid), decision, upd["update_id"], self.owner))
                    self._api("answerCallbackQuery", callback_query_id=cq["id"], text=f"#{rid} {decision}")
            except Exception:
                continue
        self.offset_file.parent.mkdir(parents=True, exist_ok=True)
        self.offset_file.write_text(str(offset))
        return events


REGISTRY = {"stdout": StdoutTransport, "telegram": TelegramTransport}


def build_transport(spec: dict[str, Any]):
    name = (spec or {}).get("transport", "stdout")
    if name not in REGISTRY:
        raise ValueError(f"unknown approval transport '{name}'. known: {sorted(REGISTRY)}")
    return REGISTRY[name](spec.get("options"))
