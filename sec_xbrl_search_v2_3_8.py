"""SEC XBRL resolver V2.3.8.

Targeted Standard-sector fallbacks validated against 2025 SEC filings:
- Newmont total inventory uses InventoryOtherThanOreStockpilesNetOfReserves ($1.512B).
- Newmont interest expense can fall back to InterestIncomeExpenseNonoperatingNet;
  a negative net value is normalized to positive expense.
- Deere current assets/current liabilities remain unavailable; no synthetic ratio.

Raw SEC payloads remain in memory only.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sec_xbrl_search_v2_3_5 import SECXBRLSearchV2_3_5
from sec_xbrl_search_v2_3_4 import EXACT_CONCEPTS_V234, _hard_excluded, _date, _annual_duration, INSTANT_METRICS, DURATION_METRICS, XBRLCandidate
from sec_xbrl_search_v2_2 import LABEL_PRIORS
from sec_xbrl_search_v2_3 import _label_quality

EXACT_CONCEPTS_V238 = {
    **EXACT_CONCEPTS_V234,
    "inventory": set(EXACT_CONCEPTS_V234.get("inventory", set()))
    | {"InventoryOtherThanOreStockpilesNetOfReserves"},
    "interest_expense": set(EXACT_CONCEPTS_V234.get("interest_expense", set()))
    | {"InterestIncomeExpenseNonoperatingNet"},
}


def _local_concept(concept: str | None) -> str:
    if not concept:
        return ""
    return str(concept).rsplit("}", 1)[-1].rsplit(":", 1)[-1]


class SECXBRLSearchV2_3_8(SECXBRLSearchV2_3_5):
    """V2.3.5 plus validated Newmont-specific semantic fallbacks."""

    @staticmethod
    def _is_exact_concept(metric: str, concept: str | None) -> bool:
        return _local_concept(concept) in EXACT_CONCEPTS_V238.get(metric, set())

    def _candidate_allowed(self, metric, concept, label, source):
        if self._is_exact_concept(metric, concept):
            return True
        return super()._candidate_allowed(metric, concept, label, source)

    def search_filing(self, cik, metric, year=None, include_dimensioned=False,
                      submissions=None, limit=20):
        """Run the V2.3.4 inline-XBRL filter with the V2.3.8 exact whitelist.

        We intentionally do not call the inherited search_filing here because
        V2.3.4's exact check is bound to EXACT_CONCEPTS_V234.
        """
        submissions = submissions or self.submissions(cik)
        rows, meta = self._inline_filing_rows(cik, submissions)
        candidates: list[XBRLCandidate] = []

        for r in rows:
            end = _date(r.get("end"))
            if year is not None and (end is None or end.year != int(year)):
                continue
            if not include_dimensioned and r.get("dimensioned"):
                continue

            concept = r.get("concept", "")
            label = r.get("label", "")
            exact = self._is_exact_concept(metric, concept)
            if not exact and _hard_excluded(metric, concept, label):
                continue
            if not self._candidate_allowed(metric, concept, label, "filing-xbrl"):
                continue

            instant = bool(r.get("instant"))
            annual = bool(r.get("start")) and _annual_duration(r.get("start"), r.get("end"))
            if metric in INSTANT_METRICS and not instant:
                continue
            if metric in DURATION_METRICS and metric != "eps" and not annual:
                continue

            label_points, label_reasons = _label_quality(metric, label, concept)
            score = float(label_points)
            reasons = list(label_reasons)
            if exact:
                score = max(score, 100.0)
                reasons.append("canonical concept")
            if metric in DURATION_METRICS and annual:
                score += 25.0
                reasons.append("annual duration")
            elif metric in INSTANT_METRICS and instant:
                score += 20.0
                reasons.append("instant")
            if r.get("namespace") == "us-gaap":
                score += 5.0
                reasons.append("us-gaap")
            if year is not None and end and end.year == int(year):
                score += 30.0
                reasons.append("target FY")

            candidates.append(XBRLCandidate(
                metric=metric,
                namespace=r.get("namespace", ""),
                concept=concept,
                label=label,
                value=r.get("value"),
                unit=r.get("unit"),
                end=r.get("end"),
                start=r.get("start"),
                fy=r.get("fy"),
                form=r.get("form"),
                filed=r.get("filed"),
                instant=instant,
                dimensioned=r.get("dimensioned", False),
                source="filing-xbrl-inline",
                score=score,
                reason=", ".join(dict.fromkeys(reasons)),
            ))

        if metric == "liabilities" and not candidates:
            derived = self._derived_liabilities_candidate(rows, year)
            if derived is not None:
                candidates.append(derived)
                meta["derived_balance_sheet"] = "assets_minus_equity"

        if metric in {"operating_income", "sga"} and not candidates:
            derived = self._derive_duration_metric(rows, metric, year)
            if derived is not None:
                candidates.append(derived)
                meta[f"derived_{metric}"] = derived.reason

        if metric == "interest_expense":
            for candidate in candidates:
                if (_local_concept(candidate.concept) == "InterestIncomeExpenseNonoperatingNet"
                        and candidate.value is not None and candidate.value < 0):
                    candidate.value = abs(float(candidate.value))
                    candidate.concept = "DerivedInterestExpenseFromNetInterestExpense"
                    candidate.namespace = "derived"
                    candidate.source = "filing-xbrl-derived"
                    candidate.score = min(candidate.score, 125.0)
                    candidate.reason = "net interest expense fallback: abs(InterestIncomeExpenseNonoperatingNet)"

        candidates.sort(key=lambda x: (x.score, _date(x.end) or date.min, x.filed or ""), reverse=True)
        meta["target_fy"] = year
        return candidates[:limit], meta


SECXBRLSearchV2_3 = SECXBRLSearchV2_3_8
SECXBRLSearchV2 = SECXBRLSearchV2_3_8
