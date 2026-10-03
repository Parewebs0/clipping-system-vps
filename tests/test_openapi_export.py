"""frontend/openapi.json must match the live FastAPI schema (issue #7)."""
import json
from pathlib import Path

from app.main import app

OPENAPI_FILE = Path(__file__).resolve().parents[1] / "frontend" / "openapi.json"


def test_openapi_export_up_to_date():
    assert OPENAPI_FILE.exists(), "run: python scripts/export_openapi.py"
    committed = json.loads(OPENAPI_FILE.read_text(encoding="utf-8"))
    live = json.loads(json.dumps(app.openapi(), sort_keys=True))
    assert committed == live, "frontend/openapi.json is stale: run python scripts/export_openapi.py"


def test_mission_control_endpoints_are_typed():
    paths = app.openapi()["paths"]
    mc = {p: v for p, v in paths.items() if p.startswith("/mission-control/")}
    assert mc, "mission control paths missing from OpenAPI"
    for path, ops in mc.items():
        schema = ops["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert "$ref" in schema, f"{path} has no response_model"


def test_campaign_status_is_enum_in_schema():
    from app.models.campaign import CAMPAIGN_STATUS_VALUES
    comps = app.openapi()["components"]["schemas"]
    st = comps["CampaignListItem"]["properties"]["status"]
    assert set(st["enum"]) == set(CAMPAIGN_STATUS_VALUES)
    sp = comps["CampaignListItem"]["properties"]["source_provider"]
    assert set(sp["enum"]) == {"whop", "manual"}
