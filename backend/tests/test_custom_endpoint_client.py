import asyncio

from app.core.llm import get_llm_client
from app.core.llm.model_router import CLOUD_PROVIDERS, ModelRouter


def test_custom_endpoint_is_an_openai_compatible_client_with_its_own_label():
    from app.core.settings_service import SettingsService

    settings = SettingsService.get_instance()
    original = {k: settings.get(k) for k in ("custom_endpoint_base_url", "custom_endpoint_model", "custom_endpoint_api_key")}
    try:
        settings.update({"custom_endpoint_base_url": "http://127.0.0.1:9/v1/", "custom_endpoint_model": "m1",
                         "custom_endpoint_api_key": ""})
        client = get_llm_client("custom_endpoint")
        assert client.provider == "custom_endpoint" and client.model == "m1"
        assert str(client.client.base_url).rstrip("/") == "http://127.0.0.1:9/v1"
        assert client.client.timeout <= 155
        assert ModelRouter()._provider_credentialed("custom_endpoint")  # url + model is enough
        settings.update({"custom_endpoint_base_url": ""})
        assert not ModelRouter()._provider_credentialed("custom_endpoint")
    finally:
        settings.update({k: v or "" for k, v in original.items()})
    assert "custom_endpoint" in CLOUD_PROVIDERS
