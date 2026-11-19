from pathlib import Path

import pytest
import yaml

from evals.task import EVALS_DIR, TaskError, load_task, task_errors

EXAMPLES = EVALS_DIR / "examples"
TASKS = sorted((EVALS_DIR / "tasks").glob("*/*.yaml"))
INVALID = yaml.safe_load((EXAMPLES / "task.invalid.yaml").read_text())


def test_valid_example_and_every_committed_task_load():
    assert load_task(EXAMPLES / "task.valid.yaml").checks == ({"status": "SUCCEEDED"},)
    assert TASKS
    for path in TASKS:
        assert load_task(path).spec_name == path.parent.name


@pytest.mark.parametrize("case", INVALID, ids=[c["case"] for c in INVALID])
def test_invalid_examples_fail_the_schema(case):
    assert task_errors(case["task"])


def _write(tmp_path: Path, **changes) -> Path:
    task = yaml.safe_load((EXAMPLES / "task.valid.yaml").read_text()) | changes
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(task))
    return path


def test_unknown_spec_fails_at_load(tmp_path):
    with pytest.raises(TaskError, match="Spec 'no-such-agent' not found"):
        load_task(_write(tmp_path, spec="no-such-agent"))


def test_check_naming_a_tool_the_spec_lacks_fails_at_load(tmp_path):
    path = _write(tmp_path, checks=[{"tool_called": {"tool": "slack.post_message"}}])

    with pytest.raises(TaskError, match="slack.post_message is not one of the spec's tools"):
        load_task(path)


def test_output_pattern_that_does_not_compile_fails_at_load(tmp_path):
    with pytest.raises(TaskError, match="does not compile"):
        load_task(_write(tmp_path, checks=[{"output": {"matches": ["(unclosed"]}}]))


def test_invalid_task_names_the_field(tmp_path):
    with pytest.raises(TaskError, match="checks"):
        load_task(_write(tmp_path, checks=[]))


def test_invalid_spec_fails_at_load(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "issue-triage.yaml").write_text("name: issue-triage\n")

    with pytest.raises(TaskError, match="not a valid spec"):
        load_task(EXAMPLES / "task.valid.yaml", specs_dir=specs)
