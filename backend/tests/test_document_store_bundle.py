import json

import pytest


class RecordingPipeline:
    def __init__(self):
        self.commands = []

    def delete(self, *keys):
        self.commands.append(("delete", keys))
        return self

    def set(self, key, value, **kwargs):
        self.commands.append(("set", key, value, kwargs))
        return self

    def sadd(self, key, value):
        self.commands.append(("sadd", key, value))
        return self

    def expire(self, key, value):
        self.commands.append(("expire", key, value))
        return self

    async def execute(self):
        return []


class RecordingClient:
    def __init__(self):
        self.pipe = RecordingPipeline()
        self.transaction = None

    def pipeline(self, transaction=True):
        self.transaction = transaction
        return self.pipe


class RecordingMetaClient:
    async def smembers(self, _key):
        return {"pdf", "docx"}


@pytest.mark.asyncio
async def test_document_bundle_replacement_is_one_redis_transaction():
    from app.core.document_store import DocumentStore, DOCUMENT_TTL_SECONDS

    client = RecordingClient()
    store = DocumentStore.__new__(DocumentStore)
    store.client = client
    store.meta_client = RecordingMetaClient()

    manifest = await store.replace_documents(
        "deal-1",
        {"docx": b"new-docx", "pdf": b"new-pdf"},
        {"report_version": "v2", "release_status": "pending_review"},
    )

    assert client.transaction is True
    assert client.pipe.commands[0][0] == "delete"
    assert set(client.pipe.commands[0][1]) == {
        "doc:deal-1:pdf", "docmeta:deal-1:pdf", "doc:deal-1:docx", "docmeta:deal-1:docx",
    }
    assert client.pipe.commands[1] == ("delete", ("docindex:deal-1",))
    assert [item["format"] for item in manifest] == ["docx", "pdf"]
    assert all(item["report_version"] == "v2" for item in manifest)
    assert all(item["release_status"] == "pending_review" for item in manifest)
    meta_command = next(command for command in client.pipe.commands if command[0] == "set" and "docmeta" in command[1])
    stored_meta = json.loads(meta_command[2])
    assert stored_meta["format"] == "docx"
    assert stored_meta["size_bytes"] == len(b"new-docx")
    assert client.pipe.commands[-1] == ("expire", "docindex:deal-1", DOCUMENT_TTL_SECONDS)


@pytest.mark.asyncio
async def test_document_bundle_replacement_rejects_empty_or_invalid_artifacts():
    from app.core.document_store import DocumentStore

    store = DocumentStore.__new__(DocumentStore)
    with pytest.raises(ValueError, match="At least one"):
        await store.replace_documents("deal-1", {})
    with pytest.raises(ValueError, match="non-empty bytes"):
        await store.replace_documents("deal-1", {"docx": b""})
