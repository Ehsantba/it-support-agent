import json

import pytest

from app import audit
from app.directory.base import DirectoryError
from app.policy import Policy, TicketState
from app.tools import build_tools

TOOLS = build_tools(("example.test",))
ANNA, CARLA, DAVID, HENRY = ("anna.becker@example.test", "carla.dietz@example.test", "david.erdem@example.test", "henry.ivanov@example.test")


# ---------------- Audit-Log ----------------

def test_audit_chain_is_valid_and_ordered(store, database):
    tid = store.create_ticket(ANNA, "Betreff", "Text hier")
    store.log(tid, "agent", "irgendwas", {"b": 2, "a": 1})
    with database.connection() as conn:
        assert audit.verify(conn) == {"ok": True, "entries": 2, "first_bad_id": None}
        rows = audit.entries(conn, tid)
    assert [r["event"] for r in rows] == ["ticket_created", "irgendwas"]


def test_audit_detects_changed_entry(store, database):
    tid = store.create_ticket(ANNA, "Betreff", "Text hier")
    store.log(tid, "agent", "a", {"x": 1})
    store.log(tid, "agent", "b", {"x": 2})
    with database.connection() as conn:
        conn.execute("UPDATE audit_log SET details = %s WHERE id = 2", (json.dumps({"x": 99}),))
        assert audit.verify(conn)["first_bad_id"] == 2


def test_audit_detects_deleted_entry(store, database):
    tid = store.create_ticket(ANNA, "Betreff", "Text hier")
    for i in range(3):
        store.log(tid, "agent", f"e{i}", {})
    with database.connection() as conn:
        conn.execute("DELETE FROM audit_log WHERE id = 2")
        res = audit.verify(conn)
    assert res["ok"] is False and res["first_bad_id"] == 3


def test_audit_detects_changed_actor(store, database):
    store.log(None, "agent", "a", {})
    with database.connection() as conn:
        conn.execute("UPDATE audit_log SET actor = 'reviewer:jemand'")
        assert not audit.verify(conn)["ok"]


# ---------------- Simulierte Umgebung ----------------

def test_unlock_and_state_changes(directory):
    assert directory.get_account_status(CARLA)["locked_out"] is True
    directory.unlock_account(CARLA)
    assert directory.get_account_status(CARLA)["locked_out"] is False
    assert directory.get_account_status(CARLA)["failed_logons"] == 0


def test_disabled_account_cannot_be_unlocked(directory):
    with pytest.raises(DirectoryError, match="deaktiviert"):
        directory.unlock_account("eva.fischer@example.test")


def test_unknown_objects_raise(directory):
    for call in (lambda: directory.get_account_status("niemand@example.test"), lambda: directory.get_group_info("Gibt es nicht"),
                 lambda: directory.get_device_owner("DEV-9999"), lambda: directory.add_group_member(ANNA, "Gibt es nicht")):
        with pytest.raises(DirectoryError):
            call()


def test_groups_devices_mailbox(directory):
    directory.add_group_member(ANNA, "Projekt-Alpha")
    assert "Projekt-Alpha" in directory.get_group_memberships(ANNA)
    directory.remove_group_member(ANNA, "Projekt-Alpha")
    assert "Projekt-Alpha" not in directory.get_group_memberships(ANNA)
    assert directory.is_privileged(HENRY) and not directory.is_privileged(ANNA)

    felix = "felix.gross@example.test"
    assert directory.get_device_status(felix)[0]["compliance"] == "noncompliant"
    directory.trigger_device_sync("DEV-1002")
    assert directory.get_device_status(felix)[0]["compliance"] == "compliant"
    directory.trigger_device_sync("DEV-1005")                              # dieses Gerät wird durch Sync nicht konform
    assert directory.get_device_status("leon.maier@example.test")[0]["compliance"] == "noncompliant"

    res = directory.set_mailbox_forwarding(ANNA, "x@extern.example")
    assert res["previous"] is None and directory.get_mailbox_info(ANNA)["forwarding_address"] == "x@extern.example"


def test_password_reset_link_needs_mfa_and_active_account(directory):
    assert directory.send_password_reset_link(ANNA)["sent"] is True
    with pytest.raises(DirectoryError):
        directory.send_password_reset_link("ida.jung@example.test")        # keine MFA registriert
    with pytest.raises(DirectoryError):
        directory.send_password_reset_link("eva.fischer@example.test")     # deaktiviert


# ---------------- Richtlinie ----------------

@pytest.fixture()
def policy(directory, settings):
    return Policy(directory, settings)


def level(policy, tool, args, requester=ANNA, **state):
    return policy.assess(TOOLS[tool], args, TicketState(requester, **state))


def test_read_is_level_0(policy):
    assert level(policy, "get_account_status", {"upn": CARLA}).level == 0


def test_self_service_reversible_is_level_1(policy):
    assert level(policy, "unlock_account", {"upn": CARLA}, requester=CARLA).level == 1
    assert level(policy, "send_password_reset_link", {"upn": ANNA}).level == 1


def test_other_person_raises_to_level_2(policy):
    risk = level(policy, "unlock_account", {"upn": CARLA}, requester=ANNA)
    assert risk.level == 2 and "andere Person" in risk.reason


def test_many_failed_logons_raise_to_level_2(policy):
    risk = level(policy, "unlock_account", {"upn": DAVID}, requester=DAVID)
    assert risk.level == 2 and "Fehlversuche" in risk.reason


def test_privileged_account_raises_to_level_2(policy, directory):
    directory.lock_account(HENRY)
    risk = level(policy, "unlock_account", {"upn": HENRY}, requester=HENRY)
    assert risk.level == 2 and "privilegiert" in risk.reason


def test_critical_tools_are_always_level_2(policy):
    assert level(policy, "add_group_member", {"upn": ANNA, "group": "Projekt-Alpha"}).level == 2
    assert level(policy, "wipe_device", {"device_id": "DEV-1001"}).level == 2
    assert level(policy, "set_mailbox_forwarding", {"upn": ANNA, "address": "a@b.example"}).level == 2
    assert level(policy, "disable_account", {"upn": ANNA}).level == 2


def test_injection_flag_raises_everything_to_level_2(policy):
    assert level(policy, "unlock_account", {"upn": CARLA}, requester=CARLA, injection_flag=True).level == 2


def test_mass_changes_raise_to_level_2(policy):
    assert level(policy, "unlock_account", {"upn": CARLA}, requester=CARLA, write_count=2).level == 1
    assert level(policy, "unlock_account", {"upn": CARLA}, requester=CARLA, write_count=3).level == 2


def test_unknown_target_is_level_2(policy):
    assert level(policy, "unlock_account", {"upn": "niemand@example.test"}, requester=ANNA).level == 2


def test_policy_can_only_raise_never_lower(policy):
    # Auch bei ganz "harmlosen" Umständen bleibt eine kritische Aktion kritisch.
    assert level(policy, "add_group_member", {"upn": ANNA, "group": "Alle-Mitarbeiter"}, requester=ANNA).level == 2


def test_reads_about_other_people_are_denied(policy):
    risk = level(policy, "get_mailbox_info", {"upn": "greta.hahn@example.test"}, requester=ANNA)
    assert risk.denied and "Auskunft" in risk.reason
    assert not level(policy, "get_mailbox_info", {"upn": ANNA}, requester=ANNA).denied
    assert not level(policy, "get_group_info", {"group": "Vertrieb"}, requester=ANNA).denied     # keine Personendaten
