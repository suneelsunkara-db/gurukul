"""Compatibility wrapper for the policy-driven grounding subsystem.

New code should import from `agent_server.grounding`. This module remains so
existing callers keep working while the architecture moves away from ad hoc
seed-resolution behavior.
"""

from agent_server.grounding import format_grounding_context, resolve_seed, split_seed

__all__ = ["format_grounding_context", "resolve_seed", "split_seed"]

