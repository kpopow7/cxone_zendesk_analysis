from orchestration.analysis.tagging_qa import (
    MismatchPair,
    MismatchSample,
    ReasonAccuracyRow,
    build_tagging_qa_report,
)

FALLBACK = "Other / Uncategorized"


def _rows() -> list[ReasonAccuracyRow]:
    return [
        ReasonAccuracyRow("Order status", comparable=100, agree=75, disagree=25),
        ReasonAccuracyRow("Billing / payment", comparable=40, agree=4, disagree=36),
        # Below the volume floor (won't appear in worst list).
        ReasonAccuracyRow("Repair", comparable=5, agree=1, disagree=4),
        # Taxonomy fallback — excluded from confident totals and worst list.
        ReasonAccuracyRow(FALLBACK, comparable=200, agree=0, disagree=200),
    ]


def _report():
    return build_tagging_qa_report(
        generated_at="2026-06-30T00:00:00Z",
        timeframe={"label": "all time"},
        min_volume=20,
        per_reason_rows=_rows(),
        mismatch_pairs=[
            MismatchPair("Billing / payment", "Order status", 30),
            MismatchPair("Order status", "Remake / replacement", 20),
        ],
        sample_mismatches=[
            MismatchSample("seg-1", 123, "2026-06-29T10:00:00Z", "Levolor", "Billing / payment", "Order status", "open")
        ],
        top_n=15,
        fallback_label=FALLBACK,
    )


def test_overall_totals_include_all_comparable_rows() -> None:
    report = _report()
    assert report.comparable_calls == 345  # 100 + 40 + 5 + 200
    assert report.agree_count == 80  # 75 + 4 + 1 + 0
    assert report.disagree_count == 265


def test_confident_totals_exclude_fallback_rows() -> None:
    report = _report()
    # Excludes the 200-row fallback bucket.
    assert report.confident_comparable_calls == 145
    assert report.confident_agree_count == 80
    assert report.confident_disagree_count == 65
    assert report.taxonomy_gap_calls == 200


def test_worst_tagged_respects_volume_floor_and_excludes_fallback() -> None:
    report = _report()
    names = [r.tagged_reason_canonical for r in report.worst_tagged_reasons]
    assert FALLBACK not in names  # fallback excluded even though it has 100% disagree
    assert "Repair" not in names  # below min_volume
    # Highest disagree_pct first: Billing (90%) before Order status (25%).
    assert names == ["Billing / payment", "Order status"]


def test_reason_accuracy_pct_helpers() -> None:
    row = ReasonAccuracyRow("X", comparable=40, agree=4, disagree=36)
    assert row.disagree_pct == 90.0
    assert row.agree_pct == 10.0


def test_to_dict_attaches_derived_percentages() -> None:
    data = _report().to_dict()
    assert data["disagree_pct"] == _report().disagree_pct
    assert data["worst_tagged_reasons"][0]["disagree_pct"] == 90.0
    assert "confident_agree_pct" in data
