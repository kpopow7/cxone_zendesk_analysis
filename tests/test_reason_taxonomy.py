from pathlib import Path

from orchestration.analysis.reason_taxonomy import (
    ReasonTaxonomy,
    TaxonomyCategory,
    load_reason_taxonomy,
    parse_reason_taxonomy,
)


def _sample_taxonomy() -> ReasonTaxonomy:
    return parse_reason_taxonomy(
        {
            "fallback_canonical": "Other / Uncategorized",
            "categories": [
                {"canonical": "Order status", "aliases": ["order status", "where is my order", "tracking"]},
                {"canonical": "Remake / replacement", "aliases": ["remake", "replacement"]},
            ],
        }
    )


def test_canonicalize_matches_alias_substring() -> None:
    tax = _sample_taxonomy()
    assert tax.canonicalize("Where is my order??") == "Order status"
    assert tax.canonicalize("order status check") == "Order status"
    assert tax.canonicalize("Customer wants a REMAKE") == "Remake / replacement"


def test_canonicalize_matches_canonical_label_without_explicit_alias() -> None:
    tax = parse_reason_taxonomy(
        {"categories": [{"canonical": "Warranty / claims", "aliases": []}]}
    )
    assert tax.canonicalize("Warranty / claims") == "Warranty / claims"


def test_canonicalize_unmatched_falls_back() -> None:
    tax = _sample_taxonomy()
    assert tax.canonicalize("Something totally unrelated") == "Other / Uncategorized"


def test_canonicalize_empty_and_placeholder_returns_none() -> None:
    tax = _sample_taxonomy()
    assert tax.canonicalize(None) is None
    assert tax.canonicalize("") is None
    assert tax.canonicalize("   ") is None
    assert tax.canonicalize("(no call reason captured)") is None
    assert tax.canonicalize("unknown") is None


def test_first_matching_category_wins() -> None:
    tax = parse_reason_taxonomy(
        {
            "categories": [
                {"canonical": "Order status", "aliases": ["order"]},
                {"canonical": "Order placement", "aliases": ["place order"]},
            ]
        }
    )
    # "place order" contains "order" too, but the first listed category wins.
    assert tax.canonicalize("place order") == "Order status"


def test_load_reason_taxonomy_reads_example_file() -> None:
    # No config/reason_taxonomy.json by default -> falls back to the bundled .example.
    tax = load_reason_taxonomy(Path("config/reason_taxonomy.json"))
    assert tax.categories
    assert tax.canonicalize("where is my order") == "Order status"
    assert tax.canonicalize("warranty claim") == "Warranty / claims"


def test_empty_taxonomy_maps_everything_to_fallback() -> None:
    tax = ReasonTaxonomy(categories=(), fallback_canonical="Other / Uncategorized")
    assert tax.canonicalize("anything at all") == "Other / Uncategorized"
    assert tax.canonicalize("") is None


def test_category_dataclass_shape() -> None:
    cat = TaxonomyCategory(canonical="X", aliases=("a", "b"))
    assert cat.canonical == "X"
    assert cat.aliases == ("a", "b")
