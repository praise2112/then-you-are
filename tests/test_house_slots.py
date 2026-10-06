import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_the_house_slot_count_agrees_across_the_server_the_service_and_the_model_spec():
    dockerfile = (ROOT / "deploy" / "house" / "Dockerfile").read_text().replace("\\\n", "")
    cmd = json.loads(next(line for line in dockerfile.splitlines() if line.startswith("CMD "))[4:])
    server = int(cmd[cmd.index("-np") + 1])
    service = yaml.safe_load((ROOT / "deploy" / "house" / "service.yaml").read_text())
    concurrency = service["spec"]["template"]["spec"]["containerConcurrency"]
    models = yaml.safe_load((ROOT / "arena_judge" / "models.yaml").read_text())
    slots = models["student-cloud"]["slots"]
    assert server == concurrency == slots, (
        f"House slots disagree: Dockerfile -np {server}, "
        f"service.yaml containerConcurrency {concurrency}, models.yaml student-cloud slots {slots}"
    )
