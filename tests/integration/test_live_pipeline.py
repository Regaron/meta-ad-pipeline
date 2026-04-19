import json

import pytest

pytestmark = pytest.mark.integration_live


def _load_json_blob(text: str):
    stripped = text.strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char not in "[{":
            continue
        try:
            payload, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if not stripped[index + end :].strip():
            return payload
    raise AssertionError(f"Expected JSON payload in text:\n{stripped}")


def test_live_paused_campaign_creation(live_settings, live_build_result):
    assert live_build_result.session_id
    assert live_build_result.creative_agent_invocations >= 1
    assert isinstance(live_build_result.creative_payload, list)
    assert len(live_build_result.creative_payload) >= 1

    for creative in live_build_result.creative_payload:
        assert {"variant_id", "variant_note", "png_url"} <= set(creative.keys())
        assert creative["png_url"].startswith(
            f"https://{live_settings.tigris_storage_bucket}.{live_settings.tigris_public_host}/creatives/"
        )
        assert creative["png_url"].endswith(".png")

    assert live_build_result.media_buyer_invocations >= 1
    assert live_build_result.campaign_id
    assert live_build_result.adset_id
    assert live_build_result.creative_ids
    assert live_build_result.ad_ids
    assert live_build_result.status == "PAUSED"
    assert live_build_result.final_text.strip()

    assert live_build_result.daily_budget_cents is not None
    assert 500 <= int(live_build_result.daily_budget_cents) <= 5000


def test_live_campaign_analytics_followup(live_build_result, run_query_capture):
    prompt = f"How is campaign {live_build_result.campaign_id} doing?"
    capture = run_query_capture(prompt, resume_session_id=live_build_result.session_id)

    assert capture.session_id == live_build_result.session_id
    assert capture.agent_tool_use_ids["creative-director"] == []
    assert capture.agent_tool_use_ids["media-buyer"]
    assert capture.assistant_text.strip()
    assert capture.assistant_text.strip() != prompt
    assert len(capture.assistant_text.splitlines()) <= 12


def test_live_campaign_activation_override(
    live_settings, live_build_result, run_query_capture
):
    if not live_settings.test_allow_activation:
        pytest.skip("Set TEST_ALLOW_ACTIVATION=1 to exercise the activation path.")

    prompt = f"Go live on campaign {live_build_result.campaign_id}."
    capture = run_query_capture(prompt, resume_session_id=live_build_result.session_id)

    assert capture.session_id == live_build_result.session_id
    assert capture.agent_tool_use_ids["media-buyer"]

    media_text = "\n".join(
        capture.subagent_text_by_parent[parent_id]
        for parent_id in capture.agent_tool_use_ids["media-buyer"]
        if capture.subagent_text_by_parent.get(parent_id)
    )
    payload = _load_json_blob(media_text)

    assert payload["status"] == "ACTIVE"
    if live_build_result.daily_budget_cents is not None and "daily_budget_cents" in payload:
        assert int(payload["daily_budget_cents"]) == int(
            live_build_result.daily_budget_cents
        )
