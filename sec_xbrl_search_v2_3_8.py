"""SEC XBRL resolver V2.3.8.

Targeted Standard-sector fallbacks validated against 2025 SEC filings:
- Newmont total inventory uses InventoryOtherThanOreStockpilesNetOfReserves
  ($1.512B), not the underlying component rows or stockpiles.
- Newmont interest expense can fall back to InterestIncomeExpenseNonoperatingNet;
  a negative net value represents net interest expense and is normalized positive.
- Deere current assets/current liabilities remain unavailable because its 2025
  consolidated balance sheet does not classify the balance sheet into current vs
  non-current totals; no synthetic ratio is fabricated.

Raw SEC payloads remain in memory only.
"""
from __future__ import annotations

from sec_xbrl_search_v2_3_5 import SECXBRLSearchV2_3_5
from sec_xbrl_search_v2_3_4 import EXACT_CONCEPTS_V234

EXACT_CONCEPTS_V238 = {
    **EXACT_CONCEPTS_V234,
    "inventory": set(EXACT_CONCEPTS_V234.get("inventory", set()))
    | {"InventoryOtherThanOreStockpilesNetOfReserves"},
    "interest_expense": set(EXACT_CONCEPTS_V234.get("interest_expense", set()))
    | {"InterestIncomeExpenseNonoperatingNet"},
}


class SECXBRLSearchV2_3_8(SECXBRLSearchV2_3_5):
    """V2.3.5 plus validated Newmont-specific semantic fallbacks."""

    def _candidate_allowed(self, metric, concept, label, source):
        local = (concept or "").rsplit("}", 1)[-1].rsplit(":", 1)[-1]
        if local in EXACT_CONCEPTS_V238.get(metric, set()):
            return True
        return super()._candidate_allowed(metric, concept, label, source)

    def search_filing(self, cik, metric, year=None, include_dimensioned=False,
                      submissions=None, limit=20):
        candidates, meta = super().search_filing(
            cik, metric, year=year, include_dimensioned=include_dimensioned,
            submissions=submissions, limit=limit,
        )

        # Newmont's 2025 filing reports "interest expense, net of capitalized
        # interest" as a negative InterestIncomeExpenseNonoperatingNet fact.
        # Normalize the sign because the scoring layer expects expense > 0.
        if metric == "interest_expense":
            for candidate in candidates:
                local = (candidate.concept or "").rsplit("}", 1)[-1].rsplit(":", 1)[-1]
                if local == "InterestIncomeExpenseNonoperatingNet" and candidate.value is not None and candidate.value < 0:
                    candidate.value = abs(float(candidate.value))
                    candidate.concept = "DerivedInterestExpenseFromNetInterestExpense"
                    candidate.namespace = "derived"
                    candidate.source = "filing-xbrl-derived"
                    candidate.score = min(candidate.score, 125.0)
                    candidate.reason = "net interest expense fallback: abs(InterestIncomeExpenseNonoperatingNet)"

        return candidates[:limit], meta


SECXBRLSearchV2_3 = SECXBRLSearchV2_3_8
SECXBRLSearchV2 = SECXBRLSearchV2_3_8
