"""Shared harness for the AI Capability & Coordination research program.

Studies (``studies/*``) plug a substrate, a trusted proxy, a ground-truth oracle,
and a dissociation metric into this harness. The harness owns the ~70% that every
study shares: the run/trial data contract, the ground-truth interface, the policy
ladder runner, the LLM adapter with cost accounting, and aggregation.

See ``README.md`` and ``CLAUDE.md``.
"""

__version__ = "0.0.1"
