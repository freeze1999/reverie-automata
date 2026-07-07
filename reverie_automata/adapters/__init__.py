"""Pluggable integrations: agent backends, approval transports, context sources.

Nothing provider-specific lives in the core — it all lives here, behind the small
interfaces in ``base.py``. Add an integration by subclassing; never by forking.
"""
from .agents import build_agent, REGISTRY as AGENTS
from .transports import build_transport, REGISTRY as TRANSPORTS
from .sources import build_source, REGISTRY as SOURCES

__all__ = ["build_agent", "build_transport", "build_source", "AGENTS", "TRANSPORTS", "SOURCES"]
