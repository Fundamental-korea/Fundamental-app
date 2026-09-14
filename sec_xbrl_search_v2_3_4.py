"""SEC XBRL resolver V2.3.4.

Targeted follow-up to V2.3.3 regression:
- exact concepts must bypass hard-exclusion rules as well as candidate filters;
- broad total concepts such as AccountsNotesAndLoansReceivableNetCurrent are
  explicitly accepted when they represent the requested total;
- filing fallback can derive operating income from Revenue - CostsAndExpenses
  and SG&A from G&A + SellingAndMarketing when a direct total is absent.

Raw SEC payloads remain in memory only.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sec_xbrl_search_v2_3_3 import SECXBRLSearchV2_3_3, _local_concept
from sec_xbrl_search_v2_2 import EXACT_CONCEPTS
from sec_xbrl_search_v2_3 import _hard_excluded, XBRLCandidate, INSTANT_METRICS, DURATION_METRICS, _annual_duration, _date


# Issuer filings sometimes use a broader total receivable concept whose name
# contains "notes" or "loans". It is still a total current receivables fact.
EXACT_CONCEPTS_V234 = {
    **EXACT_CONCEPTS,
    "receivables": set(EXACT_CONCEPTS.get("receivables", set()))
    | {"AccountsNotesAndLoansReceivableNetCurrent"},
}


class SECXBRLSearchV2_3_4(SECXBRLSearchV2_3_3):
    """V2.3.3 with exact-first filtering and generic filing derivations."""

    @staticmethod
    def _is_exact_concept(metric: str, concept: str | None) -> bool:
        return _local_concept(concept) in EXACT_CONCEPTS_V234.get(metric, set())

    def search_filing(
        self,
        cik: str | int,
        metric: str,
        year: int | None = None,
        include_dimensioned: bool = False,
        submissions: dict[str, Any] | None = None,
        limit: int = 20,
    ):
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
            # Exact concepts are trusted even if a generic negative term would
            # otherwise match the concept name (e.g. receivables containing
            # "notesandloans").
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

            score = 100.0 if exact else 0.0
            reasons = ["canonical concept"] if exact else []
            if metric in DURATION_METRICS and annual:
                score += 25.0; reasons.append("annual duration")
            elif metric in INSTANT_METRICS and instant:
                score += 20.0; reasons.append("instant")
            if r.get("namespace") == "us-gaap":
                score += 5.0; reasons.append("us-gaap")
            if year is not None and end and end.year == int(year):
                score += 30.0; reasons.append("target FY")

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

        candidates.sort(key=lambda x: (x.score, _date(x.end) or date.min, x.filed or ""), reverse=True)
        meta["target_fy"] = year
        return candidates[:limit], meta

    @staticmethod
    def _same_context_duration(rows: list[dict[str, Any]], concepts: set[str], year: int | None):
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            if r.get("dimensioned"):
                continue
            if r.get("instant"):
                continue
            if year is not None:
                d = _date(r.get("end"))
                if not d or d.year != int(year):
                    continue
            if not _annual_duration(r.get("start"), r.get("end")):
                continue
            local = _local_concept(r.get("concept"))
            if local in concepts:
                key = r.get("contextRef") or f"{local}:{r.get('start')}:{r.get('end')}"
                # Prefer us-gaap over custom duplicates when available.
                if key not in out or r.get("namespace") == "us-gaap":
                    out[key] = r
        return out

    def _derive_duration_metric(self, rows: list[dict[str, Any]], metric: str, year: int | None):
        if metric == "sga":
            # Meta/NEM-style filings can split SG&A into G&A + selling/marketing.
            ga = self._same_context_duration(rows, {"GeneralAndAdministrativeExpense"}, year)
            sm = self._same_context_duration(rows, {"SellingAndMarketingExpense"}, year)
            for key, g in ga.items():
                if key in sm:
                    s = sm[key]
                    return XBRLCandidate(
                        metric="sga", namespace="derived", concept="DerivedSGAFromGeneralAndAdministrativePlusSellingAndMarketing",
                        label="Selling, general and administrative expenses (derived)", value=float(g["value"]) + float(s["value"]),
                        unit=g.get("unit") or s.get("unit") or "", end=g.get("end"), start=g.get("start"), fy=g.get("fy"),
                        form=g.get("form"), filed=g.get("filed"), instant=False, dimensioned=False, source="filing-xbrl-derived",
                        score=110.0, reason="same-context duration identity: GeneralAndAdministrativeExpense + SellingAndMarketingExpense",
                    )
            return None

        if metric == "operating_income":
            rev = self._same_context_duration(rows, {"RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"}, year)
            costs = self._same_context_duration(rows, {"CostsAndExpenses"}, year)
            for key, r in rev.items():
                if key in costs:
                    c = costs[key]
                    return XBRLCandidate(
                        metric="operating_income", namespace="derived", concept="DerivedOperatingIncomeFromRevenueMinusCostsAndExpenses",
                        label="Operating income (derived)", value=float(r["value"]) - float(c["value"]),
                        unit=r.get("unit") or c.get("unit") or "", end=r.get("end"), start=r.get("start"), fy=r.get("fy"),
                        form=r.get("form"), filed=r.get("filed"), instant=False, dimensioned=False, source="filing-xbrl-derived",
                        score=105.0, reason="same-context duration identity: Revenue - CostsAndExpenses",
                    )
            return None
        return None


SECXBRLSearchV2_3 = SECXBRLSearchV2_3_4
SECXBRLSearchV2 = SECXBRLSearchV2_3_4
