from __future__ import annotations

from orchestration.zendesk.ticket_forms import TicketFormCatalog, parse_ticket_forms

RAW_FORMS = [
    {"id": 360001, "name": "Assist (Internal)", "active": True, "position": 1},
    {"id": 360002, "display_name": "Consumer", "active": True, "position": 2},
    {"id": 360003, "name": "Inactive Form", "active": False, "position": 3},
    {"name": "no id, skipped"},
]


def test_parse_ticket_forms_extracts_names() -> None:
    forms = parse_ticket_forms(RAW_FORMS)

    assert [f.form_id for f in forms] == [360001, 360002, 360003]
    by_id = {f.form_id: f for f in forms}
    assert by_id[360001].best_name == "Assist (Internal)"
    # falls back to display_name when name missing
    assert by_id[360002].best_name == "Consumer"
    assert by_id[360003].active is False


def test_catalog_name_lookup_and_records() -> None:
    catalog = TicketFormCatalog(parse_ticket_forms(RAW_FORMS))

    assert catalog.name_for(360001) == "Assist (Internal)"
    assert catalog.name_for(999999) is None
    assert catalog.name_for(None) is None

    records = catalog.to_records()
    assert {r["form_id"] for r in records} == {360001, 360002, 360003}
    first = next(r for r in records if r["form_id"] == 360001)
    assert first["name"] == "Assist (Internal)"
    assert "ticket_form" in first["raw_metadata"]


def test_catalog_orders_by_position() -> None:
    catalog = TicketFormCatalog(parse_ticket_forms(RAW_FORMS))
    ordered = [f.form_id for f in catalog.all_forms()]
    assert ordered == [360001, 360002, 360003]
