import pytest

from loadtest.spec import AGENT, EXAMPLE_SPEC, TRIGGER, load_spec


def test_example_spec_gets_load_name_and_trigger():
    text = load_spec(EXAMPLE_SPEC.read_text()).decode()
    assert f"name: {AGENT}\n" in text
    assert f"trigger: {TRIGGER}\n" in text
    assert "name: issue-triage" not in text
    assert "github.issue.opened" not in text
    assert "model: stub/demo" in text


def test_only_top_level_lines_change():
    spec = "name: a\ntools:\n  - name: keep\ntrigger: t\n"
    assert load_spec(spec, "b", "u") == b"name: b\ntools:\n  - name: keep\ntrigger: u\n"


def test_refuses_missing_or_repeated_lines():
    with pytest.raises(ValueError):
        load_spec("trigger: t\n")
    with pytest.raises(ValueError):
        load_spec("name: a\nname: b\ntrigger: t\n")
