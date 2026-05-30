from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RecommendationRule:
    patterns: tuple[re.Pattern[str], ...]
    recommendations: tuple[str, ...]


_RULES: tuple[RecommendationRule, ...] = (
    RecommendationRule(
        patterns=(
            re.compile(r"\b(order\s*status|where\s+is\s+my|tracking|shipment|delivery)\b", re.I),
        ),
        recommendations=(
            "Publish proactive order-status notifications (SMS/email) at each fulfillment milestone.",
            "Add self-service order lookup on the website and IVR deflection before queue.",
            "Align agent macros with real-time WMS/OMS status so callbacks are not required for routine updates.",
        ),
    ),
    RecommendationRule(
        patterns=(
            re.compile(r"\b(install|installation|measure|mount|bracket|hardware)\b", re.I),
        ),
        recommendations=(
            "Expand installation FAQs and short video guides for top SKUs linked from product pages.",
            "Offer scheduled callback windows for install support instead of live queue during peak.",
            "Audit installer/dealer disposition paths so misrouted calls do not reach consumer queues.",
        ),
    ),
    RecommendationRule(
        patterns=(
            re.compile(r"\b(warranty|defect|damage|broken|repair|replacement)\b", re.I),
        ),
        recommendations=(
            "Streamline warranty eligibility checks in Zendesk (visible to agents before answer).",
            "Create a guided warranty submission form that captures photos and serial numbers up front.",
            "Track repeat warranty contacts per product line to flag quality or packaging issues.",
        ),
    ),
    RecommendationRule(
        patterns=(
            re.compile(r"\b(bill|billing|charge|refund|payment|invoice|credit)\b", re.I),
        ),
        recommendations=(
            "Surface billing history and refund status in the customer portal to reduce status calls.",
            "Standardize refund SLA messaging on the IVR and in confirmation emails.",
            "Route billing disputes to a dedicated skill with dispute macros and finance escalation paths.",
        ),
    ),
    RecommendationRule(
        patterns=(
            re.compile(r"\b(cancel|return|exchange|wrong\s+(product|item|order))\b", re.I),
        ),
        recommendations=(
            "Enable self-service cancellation/return initiation when within policy windows.",
            "Clarify return policy on order confirmation pages to set expectations before contact.",
            "Review pick/pack accuracy metrics if wrong-item reasons cluster on specific SKUs or DCs.",
        ),
    ),
    RecommendationRule(
        patterns=(
            re.compile(r"\b(price|pricing|quote|discount|promo|coupon)\b", re.I),
        ),
        recommendations=(
            "Keep current promotions and price-match rules on a single internal knowledge article.",
            "Add chatbot/IVR answers for common pricing questions on top product families.",
            "Train dealers on published price lists to reduce consumer calls about dealer quotes.",
        ),
    ),
    RecommendationRule(
        patterns=(
            re.compile(r"\b(account|login|password|register|profile|website)\b", re.I),
        ),
        recommendations=(
            "Improve account recovery flows (password reset, verification) with clear error messages.",
            "Monitor failed-login spikes and publish status when auth or site issues occur.",
            "Add contextual help links on login and checkout pages for the top failure modes.",
        ),
    ),
    RecommendationRule(
        patterns=(
            re.compile(r"\b(product\s*spec|how\s*to|producthowto|product\s*info|production\s*question)\b", re.I),
        ),
        recommendations=(
            "Publish SKU-level spec sheets and install guides on the product page and dealer portal.",
            "Expand chatbot answers for top product-how-to intents before queue transfer.",
            "Tag tickets with product family so reporting can spot documentation gaps by line.",
        ),
    ),
    RecommendationRule(
        patterns=(
            re.compile(r"\b(remake|repair|replacement\s*part|parts\s*order)\b", re.I),
        ),
        recommendations=(
            "Clarify remake eligibility and required photos in the IVR and Zendesk form.",
            "Track remake rate by product line and supplier to address root quality issues.",
            "Offer proactive status updates when a remake order is created.",
        ),
    ),
    RecommendationRule(
        patterns=(
            re.compile(r"\b(consumer\s*support|consumer\s*inquiry)\b", re.I),
        ),
        recommendations=(
            "Split generic consumer support into specific IVR intents (order, product, warranty).",
            "Require reason-for-contact on the parent ticket before solve to improve reporting.",
        ),
    ),
    RecommendationRule(
        patterns=(
            re.compile(r"\b(dealer|installer|wholesale|trade)\b", re.I),
        ),
        recommendations=(
            "Ensure dealer-specific IVR and forms route to the dealer skill with dedicated dispositions.",
            "Provide dealers a portal for order lookup and RMA creation without calling consumer support.",
            "Review overlap between consumer and dealer reason codes to prevent mis-tagged tickets.",
        ),
    ),
)

_GENERIC_RECOMMENDATIONS: tuple[str, ...] = (
    "Document the top customer questions for this reason in Zendesk help center articles and link them from relevant IVR options.",
    "Add a targeted IVR or chatbot intent for this reason to deflect repeat contacts.",
    "Review agent handle time and first-contact resolution for this bucket; update macros and training where gaps appear.",
)


def recommendations_for_reason(reason: str, *, max_items: int = 4) -> list[str]:
    text = reason.strip()
    if not text or text.startswith("(no call reason"):
        return [
            "Require reason-for-contact and disposition on parent tickets before solve.",
            "Validate Zendesk-CXone linking so promoted fields populate on combined_interactions.",
            "Use segment summaries to backfill missing dispositions during QA sampling.",
        ][:max_items]

    matched: list[str] = []
    for rule in _RULES:
        if any(pattern.search(text) for pattern in rule.patterns):
            matched.extend(rule.recommendations)

    if not matched:
        matched.extend(_GENERIC_RECOMMENDATIONS)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in matched:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= max_items:
            break
    return deduped
