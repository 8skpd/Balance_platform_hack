import pytest


@pytest.fixture(autouse=True)
def isolate_upload_storage(tmp_path, monkeypatch):
    # Lifespan cleanup must never inspect real uploads while tests use a temporary DB.
    from app import imports
    folder = tmp_path / 'test-upload-storage'
    folder.mkdir()
    monkeypatch.setattr(imports, 'UPLOADS', folder)
