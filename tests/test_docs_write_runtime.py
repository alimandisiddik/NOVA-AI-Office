from __future__ import annotations

from app.google_workspace.docs.service import LazyDocsWriteService
from app.workspace_actions.service import UnavailableDocsWriter, WorkspaceActionError


def test_unavailable_docs_writer_fails_closed_without_factory_access():
    try:
        UnavailableDocsWriter().create_private_document("Memo", "Body")
    except WorkspaceActionError:
        pass
    else:
        raise AssertionError("Unavailable writer must fail closed")


def test_lazy_docs_writer_defers_factory_resolution_until_execution():
    calls = []
    class Factory: pass
    def supplier():
        calls.append("factory")
        return Factory()
    writer = LazyDocsWriteService(supplier)
    assert calls == []
    class Stub:
        def __init__(self, factory): calls.append(factory)
        def create_private_document(self, title, body): return (title, body)
    import app.google_workspace.docs.service as docs_module
    original = docs_module.DocsWriteService
    docs_module.DocsWriteService = Stub
    try:
        assert writer.create_private_document("Memo", "Body") == ("Memo", "Body")
    finally:
        docs_module.DocsWriteService = original
    assert calls == ["factory", calls[1]]


def test_runtime_selection_uses_canonical_auth_and_factory_without_loading_credentials(monkeypatch, tmp_path):
    import app.main as main
    calls = []

    class Auth:
        def __init__(self, *args): calls.append(("auth", args))
        def get_credentials(self, *args): raise AssertionError("startup must not load credentials")

    class Factory:
        def __init__(self, authenticator): calls.append(("factory", authenticator))

    monkeypatch.setattr(main, "GoogleAuthenticator", Auth)
    monkeypatch.setattr(main, "GoogleClientFactory", Factory)
    settings = type("Settings", (), {
        "google_client_secrets_path": tmp_path / "client.json",
        "google_token_storage_path": tmp_path / "token.json",
        "google_oauth_port": 0,
    })()
    writer = main._docs_writer_for_settings(settings)
    assert isinstance(writer, LazyDocsWriteService)
    assert [item[0] for item in calls] == ["auth", "factory"]
