import json
import subprocess

import httpx
import pytest

from app.config import Settings
from app.directory.base import DirectoryError
from app.directory.composite import CompositeDirectory
from app.directory.graph import GraphClient, GraphDirectory
from app.directory.powershell import PowerShellDirectory, PowerShellRunner
from app.llm import LLMError, MockLLM, OpenAICompatLLM, build_llm


# ---------------- PowerShell ----------------

def test_powershell_command_is_built_without_a_shell():
    cmd = PowerShellRunner().build_command("Unlock-Account.ps1", {"Upn": "anna@example.test"})
    assert cmd[:4] == ["pwsh", "-NoProfile", "-NonInteractive", "-File"]
    assert cmd[4].endswith("powershell/Unlock-Account.ps1") and cmd[5:] == ["-Upn", "anna@example.test"]


@pytest.mark.parametrize("script,params", [
    ("Evil.ps1", {"Upn": "a@example.test"}),                       # nicht in der Liste
    ("../x.ps1", {}),
    ("Unlock-Account.ps1", {"Upn": "-Force"}),                     # sähe aus wie ein weiterer Parameter
    ("Unlock-Account.ps1", {"Upn; Remove-Item": "x"}),             # Parametername
    ("Unlock-Account.ps1", {"Upn": "a" * 500}),
    ("Unlock-Account.ps1", {"Upn": "a\x00b"}),
])
def test_powershell_rejects_unsafe_input(script, params):
    with pytest.raises(DirectoryError):
        PowerShellRunner().build_command(script, params)


def test_powershell_run_parses_json_and_reports_errors(monkeypatch):
    calls = {}

    def fake_run(cmd, **kw):
        calls["kw"] = kw
        return subprocess.CompletedProcess(cmd, 0, stdout='{"upn": "a@example.test", "locked_out": false}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert PowerShellRunner().run("Unlock-Account.ps1", {"Upn": "a@example.test"})["locked_out"] is False
    assert calls["kw"]["shell"] is False and calls["kw"]["timeout"] == 30

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Benutzer nicht gefunden\nStack..."))
    with pytest.raises(DirectoryError, match="Benutzer nicht gefunden"):
        PowerShellRunner().run("Unlock-Account.ps1", {"Upn": "a@example.test"})
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="kein json", stderr=""))
    with pytest.raises(DirectoryError, match="JSON"):
        PowerShellRunner().run("Unlock-Account.ps1", {"Upn": "a@example.test"})

    def boom(cmd, **kw):
        raise FileNotFoundError("pwsh")
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(DirectoryError, match="nicht ausgeführt"):
        PowerShellRunner().run("Unlock-Account.ps1", {"Upn": "a@example.test"})


def test_powershell_directory_maps_to_scripts():
    class Fake:
        def run(self, script, params):
            return {"script": script, "params": params}
    d = PowerShellDirectory(Fake())
    assert d.unlock_account("a@example.test")["script"] == "Unlock-Account.ps1"
    assert d.set_mailbox_forwarding("a@example.test", "b@extern.example")["params"] == {"Upn": "a@example.test", "Address": "b@extern.example"}
    assert d.set_mailbox_forwarding("a@example.test", None)["params"] == {"Upn": "a@example.test", "Clear": "yes"}
    with pytest.raises(DirectoryError):
        d.lock_account("a@example.test")


# ---------------- Graph ----------------

def graph_directory(handler):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return GraphDirectory(GraphClient("tenant", "cid", "secret", client=http))


def test_graph_token_is_requested_once_and_used(monkeypatch):
    seen = {"tokens": 0, "auth": []}

    def handler(request):
        if "oauth2" in request.url.path:
            seen["tokens"] += 1
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        seen["auth"].append(request.headers["authorization"])
        return httpx.Response(200, json={"value": [{"displayName": "Vertrieb"}, {"displayName": "Domain Admins"}]})

    d = graph_directory(handler)
    assert d.get_group_memberships("a@example.test") == ["Domain Admins", "Vertrieb"]
    assert d.is_privileged("a@example.test") is True
    assert seen["tokens"] == 1 and set(seen["auth"]) == {"Bearer tok"}


def test_graph_group_and_device_operations():
    log = []

    def handler(request):
        if "oauth2" in request.url.path:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        log.append((request.method, request.url.path))
        p = request.url.path
        if p.endswith("/groups"):
            return httpx.Response(200, json={"value": [{"id": "g1", "displayName": "Projekt-Alpha", "description": "d"}]})
        if "/users/" in p and request.method == "GET":
            return httpx.Response(200, json={"id": "u1"})
        return httpx.Response(204)

    d = graph_directory(handler)
    d.add_group_member("a@example.test", "Projekt-Alpha")
    d.trigger_device_sync("dev-id")
    d.wipe_device("dev-id")
    d.disable_account("a@example.test")
    methods = [m + " " + p.split("/v1.0")[-1] for m, p in log]
    assert "POST /groups/g1/members/$ref" in methods and "POST /deviceManagement/managedDevices/dev-id/syncDevice" in methods
    assert "POST /deviceManagement/managedDevices/dev-id/wipe" in methods and "PATCH /users/a@example.test" in methods


@pytest.mark.parametrize("status,message", [(404, "nicht gefunden"), (403, "Berechtigung"), (500, "Fehler 500")])
def test_graph_errors_become_directory_errors(status, message):
    def handler(request):
        if "oauth2" in request.url.path:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(status)
    with pytest.raises(DirectoryError, match=message):
        graph_directory(handler).get_account_status("a@example.test")


def test_graph_rejects_unsafe_names_before_any_request():
    def handler(request):
        raise AssertionError("es darf keine Anfrage gesendet werden")
    d = graph_directory(handler)
    with pytest.raises(DirectoryError):
        d.get_account_status("a@example.test/../../admin")
    with pytest.raises(DirectoryError):
        d.get_group_info("x' or displayName ne '")


def test_composite_routes_to_the_right_backend():
    class G:
        def get_group_memberships(self, upn): return ["g"]
    class P:
        def unlock_account(self, upn): return {"ps": True}
    c = CompositeDirectory(G(), P())
    assert c.get_group_memberships("x") == ["g"] and c.unlock_account("x") == {"ps": True}
    with pytest.raises(DirectoryError):
        c.send_password_reset_link("x")


# ---------------- Sprachmodell-Client ----------------

def llm_with(handler, **kw):
    sleeps = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    llm = OpenAICompatLLM("https://api.test/v1", "geheim", "modell-x", provider="gemini", client=client, sleep=sleeps.append, **kw)
    return llm, sleeps


def ok(text='{"action": {"tool": "finish"}}'):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def test_llm_request_format():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return ok("antwort")
    llm, _ = llm_with(handler)
    assert llm.complete("system", "user") == "antwort"
    assert seen["url"] == "https://api.test/v1/chat/completions" and seen["auth"] == "Bearer geheim"
    b = seen["body"]
    assert b["model"] == "modell-x" and b["temperature"] == 0 and b["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in b["messages"]] == ["system", "user"] and llm.name == "gemini:modell-x"


def test_llm_retries_on_rate_limit_with_retry_after():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(429, headers={"retry-after": "7"}) if len(attempts) < 3 else ok("fertig")
    llm, sleeps = llm_with(handler)
    assert llm.complete("s", "u") == "fertig" and len(attempts) == 3 and sleeps[:2] == [7.0, 7.0]


def test_llm_gives_up_after_retries():
    llm, _ = llm_with(lambda r: httpx.Response(503), max_retries=2)
    with pytest.raises(LLMError, match="503"):
        llm.complete("s", "u")


@pytest.mark.parametrize("response,message", [
    (httpx.Response(401), "401/403"), (httpx.Response(400, text="bad"), "400"),
    (httpx.Response(200, json={"unerwartet": 1}), "Antwortformat"), (ok(""), "Leere Antwort"),
])
def test_llm_errors_have_clear_messages_and_never_leak_the_key(response, message):
    llm, _ = llm_with(lambda r: response)
    with pytest.raises(LLMError, match=message) as e:
        llm.complete("s", "u")
    assert "geheim" not in str(e.value)


def test_llm_min_interval_throttles_calls():
    llm, sleeps = llm_with(lambda r: ok(), min_interval=5.0)
    llm.complete("s", "u")
    llm.complete("s", "u")
    assert sleeps and 0 < sleeps[-1] <= 5.0


def test_build_llm():
    assert isinstance(build_llm(Settings()), MockLLM)
    assert build_llm(Settings(llm_provider="mock-naiv")).name == "mock-naiv"
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        build_llm(Settings(llm_provider="gemini", llm_base_url="https://x"))
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):                  # Platzhalter aus .env.example zählt als fehlend
        build_llm(Settings(llm_provider="gemini", llm_api_key="HIER_DEIN_SCHLUESSEL_EINTRAGEN", llm_base_url="https://x"))
    assert build_llm(Settings(llm_provider="gemini", llm_api_key="k", llm_base_url="https://x/v1")).name == "gemini:gemini-2.5-flash"
    with pytest.raises(LLMError):
        build_llm(Settings(llm_provider="unbekannt"))


def test_settings_from_env_gemini_defaults(monkeypatch):
    for k in ("LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "platzhalter")
    s = Settings.from_env()
    assert s.llm_base_url == "https://generativelanguage.googleapis.com/v1beta/openai" and s.llm_api_key == "platzhalter"
