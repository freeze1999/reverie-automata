"""Where a delegated task goes, and how the answer finds its way home.

The engine knows a task should be handed off (see `routing`); it deliberately
knows nothing about who takes it or over what wire. A delegate here is two
methods: turn a task into a filed job, and hand back whatever answers have
arrived since last asked. Everything else, the transport, the acceptance
contract, the worker's identity, belongs to the deployment.

The interface is asymmetric on purpose. Filing happens inside a cycle, because
that is where the decision is made. Collecting happens OUTSIDE the cycle, in
the runner, before the gate is even consulted, because closing a job is
bookkeeping and bookkeeping that waits for the agent to remember it is
bookkeeping that stops happening. It also keeps the judging away from the party
that asked for the work.
"""
from __future__ import annotations

from typing import Any


class NullDelegate:
    """No worker configured. Routing still runs and still records what it would
    have done, and the task is executed locally anyway.

    Refusing to run the task would be the tidier failure and the wrong one: an
    engine that stops working because its helper is unconfigured has made a
    missing convenience into an outage."""

    name = "null"

    def __init__(self, options: dict | None = None):
        self.options = options or {}

    def file(self, task: dict, *, cycle: str = "") -> tuple[str, str]:
        return "", "no delegate configured; running locally"

    def collect(self) -> list[dict]:
        return []


REGISTRY: dict[str, Any] = {NullDelegate.name: NullDelegate}


def build_delegate(spec: dict | None):
    spec = spec or {}
    name = spec.get("backend", "null")
    if name not in REGISTRY:
        raise ValueError(f"unknown delegate backend '{name}'. known: {sorted(REGISTRY)}")
    return REGISTRY[name](spec.get("options"))
