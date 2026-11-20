"""The load agent's spec: the contracts example (stub model) under its own name and trigger.

The live `issue-triage` and `aws_demo.py`'s `demo-stub` are never touched, and no webhook sends
`manual.load.run`, so real events never start this agent.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXAMPLE_SPEC = REPO / "contracts" / "examples" / "agent-spec.valid.yaml"
AGENT = "load-stub"
TRIGGER = "manual.load.run"
RUN_INPUT = {
    "repository": {"full_name": "acme/payments"},
    "issue": {"number": 42, "title": "Refund fails for EUR cards"},
}


def load_spec(text: str, name: str = AGENT, trigger: str = TRIGGER) -> bytes:
    """`text` with its one top-level `name:` and `trigger:` lines replaced."""
    lines = text.splitlines(keepends=True)
    for key, value in (("name", name), ("trigger", trigger)):
        found = [i for i, line in enumerate(lines) if line.startswith(f"{key}: ")]
        if len(found) != 1:
            raise ValueError(f"expected one top-level '{key}:' line, found {len(found)}")
        lines[found[0]] = f"{key}: {value}\n"
    return "".join(lines).encode()
