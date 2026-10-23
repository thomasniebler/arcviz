import pytest

from arcviz.store import StoreError, create_store
from arcviz.store.factory import parse_store_spec
from arcviz.store.file import FileStore
from arcviz.store.sqlite import SqliteStore


def test_default_is_file_store_in_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("ARCVIZ_STORE", raising=False)
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path / "d"))
    store = create_store()
    assert isinstance(store, FileStore) and store.name == "file"
    assert store.root == tmp_path / "d"


def test_sqlite_store_lives_in_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_STORE", "sqlite")
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path / "d"))
    store = create_store()
    assert isinstance(store, SqliteStore) and store.name == "sqlite"
    assert store.path == tmp_path / "d" / "arcviz.sqlite3"


def test_unknown_backend_names_valid_values(monkeypatch):
    monkeypatch.setenv("ARCVIZ_STORE", "mongo")
    with pytest.raises(StoreError, match="file, sqlite, cosmos"):
        create_store()


def test_cosmos_requires_url(monkeypatch):
    monkeypatch.setenv("ARCVIZ_STORE", "cosmos")
    monkeypatch.delenv("ARCVIZ_STORE_URL", raising=False)
    with pytest.raises(StoreError, match="ARCVIZ_STORE_URL"):
        create_store()


def test_missing_cosmos_extra_is_reported(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("azure"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setenv("ARCVIZ_STORE", "cosmos")
    monkeypatch.setenv("ARCVIZ_STORE_URL", "https://x.documents.azure.com:443/")
    with pytest.raises(StoreError, match=r"arcviz-agent\[cosmos\]"):
        create_store()


def test_parse_store_spec(tmp_path):
    assert parse_store_spec("file:data") == ("file", "data")
    assert parse_store_spec("sqlite:/tmp/x.sqlite3") == ("sqlite", "/tmp/x.sqlite3")
    assert parse_store_spec("cosmos:https://acc.documents.azure.com:443/") == (
        "cosmos",
        "https://acc.documents.azure.com:443/",
    )
    with pytest.raises(StoreError):
        parse_store_spec("data")
    with pytest.raises(StoreError):
        parse_store_spec("mongo:x")
