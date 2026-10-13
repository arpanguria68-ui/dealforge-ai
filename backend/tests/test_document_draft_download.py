import asyncio

import pytest
from fastapi import HTTPException

import app.core.document_store as ds_module
from app import main


class FakeStore:
    def __init__(self, release_status):
        self.meta = {"report_version": "v1", "analysis_fingerprint": "f", "safe_filename": "Acme",
                     "release_status": release_status}

    async def get_document(self, deal_id, fmt):
        return b"PK-bytes"

    async def get_document_meta(self, deal_id, fmt):
        return self.meta


def _download(monkeypatch, release_status, draft):
    store = FakeStore(release_status)
    monkeypatch.setattr(ds_module.DocumentStore, "get_instance", classmethod(lambda cls: store))
    return asyncio.run(main.download_deal_document("deal-1", "docx", draft=draft, _=True))


def test_unapproved_report_is_blocked_without_the_draft_flag(monkeypatch):
    with pytest.raises(HTTPException) as err:
        _download(monkeypatch, "pending_review", draft=False)
    assert err.value.status_code == 409


def test_draft_download_is_allowed_but_marked(monkeypatch):
    response = _download(monkeypatch, "pending_review", draft=True)
    assert response.body == b"PK-bytes"
    assert "DRAFT_not_approved" in response.headers["content-disposition"]
    assert response.headers["x-release-status"] == "draft-not-approved"


def test_approved_report_downloads_normally_even_with_draft_flag(monkeypatch):
    response = _download(monkeypatch, "approved", draft=True)
    assert "DRAFT" not in response.headers["content-disposition"]
    assert "x-release-status" not in response.headers


def test_on_the_fly_export_is_a_draft_until_the_reports_are_approved():
    draft = main.export_download_headers("Acme", "docx", approved=False)
    assert draft["Content-Disposition"] == 'attachment; filename="DealForge_Acme_DRAFT_not_approved.docx"'
    assert draft["X-Release-Status"] == "draft-not-approved"
    final = main.export_download_headers("Acme", "docx", approved=True)
    assert final["Content-Disposition"] == 'attachment; filename="DealForge_Acme.docx"'
    assert "X-Release-Status" not in final
