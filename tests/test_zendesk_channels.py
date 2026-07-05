from orchestration.zendesk.channels import (
    AGENT_CREATED_PHONE_CHANNEL,
    has_agent_created_tag,
    is_phone_bridge_ticket,
    resolve_ticket_via_channel,
)


def test_has_agent_created_tag() -> None:
    assert has_agent_created_tag(["agent created", "other"])
    assert has_agent_created_tag(["Agent Created"])
    assert has_agent_created_tag(["agent_created"])
    assert not has_agent_created_tag(["created by agent"])
    assert not has_agent_created_tag([])


def test_resolve_ticket_via_channel_agent_created_overrides_raw() -> None:
    assert (
        resolve_ticket_via_channel(tags=["agent created"], raw_via_channel="web")
        == AGENT_CREATED_PHONE_CHANNEL
    )
    assert (
        resolve_ticket_via_channel(tags=["agent created"], raw_via_channel="mail")
        == AGENT_CREATED_PHONE_CHANNEL
    )


def test_resolve_ticket_via_channel_keeps_stated_channel() -> None:
    assert resolve_ticket_via_channel(tags=["consumer"], raw_via_channel="mail") == "mail"
    assert resolve_ticket_via_channel(tags=[], raw_via_channel="web") == "web"
    assert resolve_ticket_via_channel(tags=[], raw_via_channel="chat") == "chat"
    assert resolve_ticket_via_channel(tags=[], raw_via_channel="voice") == "voice"


def test_resolve_ticket_via_channel_empty_raw() -> None:
    assert resolve_ticket_via_channel(tags=[], raw_via_channel=None) is None
    assert resolve_ticket_via_channel(tags=[], raw_via_channel="  ") is None


def test_is_phone_bridge_ticket_agent_created() -> None:
    assert is_phone_bridge_ticket(tags=["agent created"], ticket_form_name="Consumer")


def test_is_phone_bridge_ticket_phone_call_form() -> None:
    assert is_phone_bridge_ticket(
        tags=[],
        ticket_form_id=41936749648916,
        phone_call_form_ids=frozenset({41936749648916}),
    )
    assert is_phone_bridge_ticket(tags=[], ticket_form_name="Phone Call")


def test_is_phone_bridge_ticket_parent_detail() -> None:
    assert not is_phone_bridge_ticket(
        tags=["consumer"],
        ticket_form_name="Consumer",
        ticket_form_id=123,
        phone_call_form_ids=frozenset({41936749648916}),
    )
