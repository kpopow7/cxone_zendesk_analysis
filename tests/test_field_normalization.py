from pathlib import Path

from orchestration.linking.field_normalization import (
    DEFAULT_FIELD_NORMALIZATION_CONFIG,
    FieldNormalizationConfig,
    normalize_zendesk_fields,
)


def test_normalizes_consumer_reason_and_dealer_disposition() -> None:
    config = FieldNormalizationConfig(
        reason_fields=DEFAULT_FIELD_NORMALIZATION_CONFIG.reason_fields,
        disposition_fields=DEFAULT_FIELD_NORMALIZATION_CONFIG.disposition_fields,
        disposition_label_map_path="config/disposition_label_map.json",
        humanize_reason_codes=True,
        disposition_fallback_humanize=True,
        fallback_to_ticket_subject=False,
    )
    promoted = {
        "cf_reason_for_contact_consumer": "reasoncontactdi_product_info",
        "cf_disposition_dealer": "dispdealer__ordersupport_product_info",
    }

    result = normalize_zendesk_fields(
        promoted,
        ticket_subject=None,
        config=config,
        project_root=Path(__file__).resolve().parents[1],
    )

    assert result.call_reason_source == "cf_reason_for_contact_consumer"
    assert result.call_reason_code == "reasoncontactdi_product_info"
    assert result.call_reason == "product info"
    assert result.disposition_source == "cf_disposition_dealer"
    assert result.disposition_code == "dispdealer__ordersupport_product_info"
    assert result.disposition_label == "Dealer: Order support - product information"


def test_skips_empty_form_fields_and_uses_next_reason_field() -> None:
    config = DEFAULT_FIELD_NORMALIZATION_CONFIG
    promoted = {
        "cf_reason_for_contact_consumer": "",
        "cf_reason_for_contact_installerdealer": "Order status inquiry",
        "cf_disposition": "dispcon_product_info",
    }

    result = normalize_zendesk_fields(
        promoted,
        ticket_subject=None,
        config=config,
        project_root=Path(__file__).resolve().parents[1],
    )

    assert result.call_reason_source == "cf_reason_for_contact_installerdealer"
    assert result.call_reason == "Order status inquiry"
    assert result.disposition_source == "cf_disposition"


def test_returns_nulls_when_no_promoted_values() -> None:
    result = normalize_zendesk_fields(
        {},
        ticket_subject=None,
        config=DEFAULT_FIELD_NORMALIZATION_CONFIG,
        project_root=Path(__file__).resolve().parents[1],
    )

    assert result.call_reason is None
    assert result.call_reason_code is None
    assert result.disposition_label is None
