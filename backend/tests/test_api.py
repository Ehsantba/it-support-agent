import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.llm.mock import MockLLM

AUTH = {"X-Reviewer-Token": "demo-reviewer", "X-Reviewer": "ben.krause"}
ANNA, CARLA = "anna.becker@example.test", "carla.dietz@example.test"


@pytest.fixture()
def client(database, directory, make_agent, settings):
    agent = make_agent(MockLLM(), auto_process=True)
    from dataclasses import replace
    return TestClient(create_app(replace(settings, auto_process=True), database, directory, agent))


def new_ticket(client, requester=CARLA, subject="Konto gesperrt", body="Ich bin ausgesperrt."):
    r = client.post("/api/tickets", json={"requester_upn": requester, "subject": subject, "body": body})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_ticket_is_created_and_processed(client):
    tid = new_ticket(client)
    data = client.get(f"/api/tickets/{tid}").json()
    assert data["ticket"]["status"] == "REVIEW" and data["ticket"]["category"] == "KONTOSPERRE"
    assert [a["tool"] for a in data["actions"]][:2] == ["classify_ticket", "get_account_status"]
    assert any(t["id"] == tid for t in client.get("/api/tickets").json())


@pytest.mark.parametrize("payload", [
    {"requester_upn": "x@gmail.com", "subject": "Hallo", "body": "Text hier"},          # falsche Domäne
    {"requester_upn": "niemand@example.test", "subject": "Hallo", "body": "Text hier"},  # unbekannt
    {"requester_upn": CARLA, "subject": "", "body": "Text hier"},
    {"requester_upn": CARLA, "subject": "Hallo", "body": "x" * 5000},
    {"requester_upn": CARLA, "subject": "Hallo"},
])
def test_ticket_validation(client, payload):
    assert client.post("/api/tickets", json=payload).status_code in (422,)


def test_unknown_ticket_is_404(client):
    assert client.get("/api/tickets/9999").status_code == 404


def test_reviewer_endpoints_need_token_and_name(client):
    assert client.get("/api/approvals").status_code == 401
    assert client.get("/api/approvals", headers={"X-Reviewer-Token": "falsch", "X-Reviewer": "ben"}).status_code == 401
    assert client.get("/api/approvals", headers={"X-Reviewer-Token": "demo-reviewer"}).status_code == 422
    assert client.get("/api/audit").status_code == 401
    assert client.post("/api/actions/1/decision", json={"approve": True}).status_code == 401
    assert client.post("/api/tickets/1/review", json={"verdict": "approve"}).status_code == 401
    assert client.post("/api/simulation/reset").status_code == 401


def test_approval_flow_over_the_api(client):
    tid = new_ticket(client, ANNA, "Zugriff", "Bitte nehmt mich in die Gruppe Projekt-Alpha auf.")
    assert client.get(f"/api/tickets/{tid}").json()["ticket"]["status"] == "AWAITING_APPROVAL"
    pending = client.get("/api/approvals", headers=AUTH).json()
    assert len(pending) == 1 and pending[0]["tool"] == "add_group_member" and pending[0]["requester_upn"] == ANNA
    aid = pending[0]["id"]
    assert client.post(f"/api/actions/{aid}/decision", headers=AUTH, json={"approve": False}).status_code == 422
    r = client.post(f"/api/actions/{aid}/decision", headers=AUTH, json={"approve": True, "comment": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "EXECUTED"
    assert client.post(f"/api/actions/{aid}/decision", headers=AUTH, json={"approve": True}).status_code == 409
    assert client.post(f"/api/tickets/{tid}/review", headers=AUTH, json={"verdict": "approve"}).json()["status"] == "CLOSED"
    sim = client.get("/api/simulation").json()
    assert {"upn": ANNA, "group_name": "Projekt-Alpha"} in sim["memberships"]


def test_requester_name_cannot_approve(client):
    tid = new_ticket(client, ANNA, "Zugriff", "Bitte nehmt mich in die Gruppe Projekt-Alpha auf.")
    aid = client.get("/api/approvals", headers=AUTH).json()[0]["id"]
    r = client.post(f"/api/actions/{aid}/decision", headers={**AUTH, "X-Reviewer": ANNA}, json={"approve": True})
    assert r.status_code == 403


def test_undo_over_the_api(client):
    tid = new_ticket(client)
    aid = next(a["id"] for a in client.get(f"/api/tickets/{tid}").json()["actions"] if a["tool"] == "unlock_account")
    assert client.post(f"/api/actions/{aid}/undo", headers=AUTH).json()["status"] == "UNDONE"
    user = next(u for u in client.get("/api/simulation").json()["users"] if u["upn"] == CARLA)
    assert user["locked_out"] is True


def test_audit_endpoints_and_chain_verification(client):
    tid = new_ticket(client)
    entries = client.get(f"/api/audit?ticket_id={tid}", headers=AUTH).json()
    assert entries[0]["event"] == "ticket_created"
    assert client.get("/api/audit/verify", headers=AUTH).json()["ok"] is True


def test_metrics_and_config(client):
    new_ticket(client)
    m = client.get("/api/metrics").json()
    assert m["tickets"] == 1 and m["by_status"]["REVIEW"] == 1 and m["actions_by_status"]["EXECUTED"] >= 2
    c = client.get("/api/config").json()
    assert c["llm"] == "mock" and c["directory"] == "simulated"


def test_reprocessing_rules(client):
    tid = new_ticket(client)
    assert client.post(f"/api/tickets/{tid}/process", headers=AUTH).status_code == 409
    assert client.post("/api/tickets/9999/process", headers=AUTH).status_code == 404


def test_simulation_reset(client):
    new_ticket(client)
    assert client.post("/api/simulation/reset", headers=AUTH).json() == {"ok": True}
    user = next(u for u in client.get("/api/simulation").json()["users"] if u["upn"] == CARLA)
    assert user["locked_out"] is True


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}
