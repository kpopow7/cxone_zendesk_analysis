from orchestration.analysis.channels import channel_label, transcript_label
from orchestration.analysis.transcript_reason_llm import _build_classification_prompt


def test_channel_label_known_types() -> None:
    assert channel_label("PhoneCall") == "phone call"
    assert channel_label("Email") == "email"
    assert channel_label("Chat") == "chat"
    assert channel_label("livechat") == "chat"
    assert channel_label("SMS") == "text message conversation"


def test_channel_label_unknown_and_empty_fall_back() -> None:
    assert channel_label(None) == "customer interaction"
    assert channel_label("") == "customer interaction"
    assert channel_label("HologramCall") == "customer interaction"


def test_transcript_label_per_channel() -> None:
    assert transcript_label("PhoneCall") == "call transcript"
    assert transcript_label("Email") == "email"
    assert transcript_label("Chat") == "chat transcript"
    assert transcript_label(None) == "transcript"


def test_normalization_is_punctuation_insensitive() -> None:
    assert channel_label("phone-call") == "phone call"
    assert channel_label("Phone Call") == "phone call"


def test_classification_prompt_is_channel_aware() -> None:
    email_prompt = _build_classification_prompt(
        transcript="Hello, where is my order?",
        segment_summary=None,
        client_sentiment=None,
        skill_name=None,
        agent_name=None,
        media_type="Email",
    )
    assert email_prompt.startswith("Analyze this email.")
    assert "Channel: Email" in email_prompt

    phone_prompt = _build_classification_prompt(
        transcript="Hello, where is my order?",
        segment_summary=None,
        client_sentiment=None,
        skill_name=None,
        agent_name=None,
        media_type="PhoneCall",
    )
    assert phone_prompt.startswith("Analyze this call transcript.")


def test_classification_prompt_without_media_type_is_generic() -> None:
    prompt = _build_classification_prompt(
        transcript="X",
        segment_summary=None,
        client_sentiment=None,
        skill_name=None,
        agent_name=None,
    )
    assert prompt.startswith("Analyze this transcript.")
    assert "Channel:" not in prompt
