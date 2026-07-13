"""Documentation contract tests."""

import json
import re
from pathlib import Path

from reviewmymcp.config import AuditConfig


def test_readme_configuration_example_validates():
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    match = re.search(r"## Configuration.*?```json\n(.*?)\n```", readme, re.DOTALL)

    assert match is not None
    AuditConfig.model_validate(json.loads(match.group(1)))
