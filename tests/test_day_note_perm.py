"""ACL order for one sentence a day. Non-members stay on the existing 403 without a code."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.constants import FORBIDDEN
from app.main import app
from app.services.clock import shanghai_today
from tests.test_acceptance import auth
from tests.test_journal import _family, _invite


def test_viewer_delete_missing_is_readonly_not_404(client: TestClient):
    _family(client, "dnp-ro@example.com", "妈妈", "只读缺号家")
    viewer = _invite(client, "dnp-view@example.com", "viewer", "外婆")
    missing = viewer.delete("/api/day-notes/999999")
    assert missing.status_code == 403
    assert missing.json()["code"] == "day_note_readonly"
    assert missing.json()["detail"] != "找不到"


def test_writer_missing_is_404_after_role_check(client: TestClient):
    child_id = _family(client, "dnp-miss@example.com", "妈妈", "缺失家")
    missing = client.delete("/api/day-notes/999999")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "找不到", "code": "day_note_not_found"}
    outsider = TestClient(app)
    auth(outsider, "dnp-out@example.com", "外人")
    hidden = outsider.delete("/api/day-notes/999999")
    assert hidden.status_code == 403
    assert hidden.json() == {"detail": FORBIDDEN}
    assert child_id
