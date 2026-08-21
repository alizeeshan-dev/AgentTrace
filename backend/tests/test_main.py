import json
import logging

from fastapi.testclient import TestClient

from app.config import Settings
from app.logging import JsonFormatter
from app.main import create_app


def test_health_endpoint_uses_injected_settings() -> None:
    client = TestClient(create_app(Settings(app_name="AgentTrace Test", environment="test")))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "AgentTrace Test"}


def test_json_formatter_emits_structured_attributes() -> None:
    record = logging.LogRecord(
        name="agenttrace.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="workspace created",
        args=(),
        exc_info=None,
    )
    record.run_id = "run-001"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "workspace created"
    assert payload["attributes"]["run_id"] == "run-001"
