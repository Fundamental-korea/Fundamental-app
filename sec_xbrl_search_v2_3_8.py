"""SEC XBRL resolver V2.3.8.

Targeted Standard-sector fallbacks validated against 2025 SEC filings:
- Newmont total inventory uses the disclosed non-ore inventory components ($1.512B).
- Newmont interest expense can fall back to InterestIncomeExpenseNonoperatingNet;
  a negative net value is normalized to positive expense.
- Newmont SG&A uses GeneralAndAdministrativeExpense as a validated proxy.
- Deere current assets/current liabilities remain unavailable; no synthetic ratio.

Raw SEC payloads remain in memory only.
"""
from __future__ import annotations

from datetime import date

from sec_xbrl_search_v2_3_5 import SECXBRLSearchV2_3_5
from sec_xbrl_search_v2_3_4 import (
    EXACT_CONCEPTS_V234,
    _hard_excluded,
    _date,
    _annual_duration,
    INSTANT_METRICS,
    DURATION_METRICS,
    XBRLCandidate,
)
from sec_xbrl_search_v2_3 import _label_quality

EXACT_CONCEPTS_V238 = {
    **EXACT_CONCEPTS_V234,
    "inventory": set(EXACT_CONCEPTS_V234.get("inventory", set()))
    | {"InventoryOtherThanOreStockpilesNetOfReserves"},
    "interest_expense": set(EXACT_CONCEPTS_V234.get("interest_expense", set()))
    | {"InterestIncomeExpenseNonoperatingNet"},
    "sga": set(EXACT_CONCEPTS_V234.get("sga", set()))
    | {"GeneralAndAdministrativeExpense"},
}


def _local_concept(concept: str | None) -> str:
    if not concept:
        return ""
    return str(concept).rsplit("}", 1)[-1].rsplit(":", 1)[-1]


class SECXBRLSearchV2_3_8(SECXBRLSearchV2_3_5):
    """V2.3.5 plus validated Standard-sector semantic fallbacks."""

    def _inline_filing_rows(self, cik, submissions):
        cache = getattr(self, "_inline_rows_cache", None)
        if cache is None:
            cache = {}
            self._inline_rows_cache = cache
        accession, _doc, _filed = self.latest_annual_filing(submissions)
        key = (str(int(cik)), accession)
        if accession and key in cache:
            return cache[key]
        result = super()._inline_filing_rows(cik, submissions)
        if accession:
            cache[key] = result
        return result

    @staticmethod
    def _is_exact_concept(metric: str, concept: str | None) -> bool:
        return _local_concept(concept) in EXACT_CONCEPTS_V238.get(metric, set())

    def _candidate_allowed(self, metric, concept, label, source):
        if self._is_exact_concept(metric, concept):
            return True
        return super()._candidate_allowed(metric, concept, label, source)

    @staticmethod
    def _make_exact_candidate(metric, r, reason="canonical concept"):
        end = r.get("end")
        instant = bool(r.get("instant"))
        return XBRLCandidate(
            metric=metric,
            namespace=r.get("namespace", ""),
            concept=r.get("concept", ""),
            label=r.get("label", ""),
            value=r.get("value"),
            unit=r.get("unit"),
            end=end,
            start=r.get("start"),
            fy=r.get("fy"),
            form=r.get("form"),
            filed=r.get("filed"),
            instant=instant,
            dimensioned=r.get("dimensioned", False),
            source="filing-xbrl-inline",
            score=155.0,
            reason=reason,
        )

    def _exact_rows_first(self, rows, metric, year):
        """Authoritative exact-concept path, before generic negative filters."""
        out = []
        for r in rows:
            if not self._is_exact_concept(metric, r.get("concept")):
                continue
            if r.get("dimensioned"):
                continue
            end = _date(r.get("end"))
            if year is not None and (end is None or end.year != int(year)):
                continue
            instant = bool(r.get("instant"))
            annual = bool(r.get("start")) and _annual_duration(r.get("start"), r.get("end"))
            if metric in INSTANT_METRICS and not instant:
                continue
            if metric in DURATION_METRICS and metric != "eps" and not annual:
                continue
            out.append(self._make_exact_candidate(metric, r))
        return out

    @staticmethod
    def _derive_validated_newmont_inventory(rows, year):
        """Derive NEM's non-ore inventory total from its four disclosed components.

        Newmont's 2025 filing exposes the non-ore inventory components but not
        the total concept through the Inline XBRL rows consumed here. The
        validated components are concentrate, materials/supplies/other,
        work-in-process, and precious metals. Their sum is the reported
        $1.512B inventory total. Ore stockpiles/leach pads are disclosed
        separately and are intentionally excluded.
        """
        component_names = {
            "ConcentrateInventoryNetOfReserves",
            "MaterialsSuppliesAndOtherInventoryNetOfReserves",
            "InventoryWorkInProcessNetOfReserves",
            "PreciousMetalsInventoryNetOfReserves",
        }
        groups = {}
        for r in rows:
            if r.get("dimensioned") or not r.get("instant"):
                continue
            end = _date(r.get("end"))
            if year is not None and (end is None or end.year != int(year)):
                continue
            local = _local_concept(r.get("concept"))
            if local not in component_names or r.get("value") is None:
                continue
            context = r.get("contextRef")
            if not context:
                continue
            groups.setdefault(context, {})[local] = r

        for context, facts in groups.items():
            if not component_names.issubset(facts):
                continue
            values = [float(facts[name]["value"]) for name in component_names]
            total = sum(values)
            anchor = facts["MaterialsSuppliesAndOtherInventoryNetOfReserves"]
            return XBRLCandidate(
                metric="inventory",
                namespace="derived",
                concept="DerivedInventoryOtherThanOreStockpilesNetOfReserves",
                label="Inventory other than ore stockpiles (derived from disclosed components)",
                value=total,
                unit=anchor.get("unit") or "",
                end=anchor.get("end"),
                start=None,
                fy=anchor.get("fy"),
                form=anchor.get("form"),
                filed=anchor.get("filed"),
                instant=True,
                dimensioned=False,
                source="filing-xbrl-derived",
                score=145.0,
                reason=(
                    "validated same-context inventory identity: "
                    "Concentrate + Materials/Supplies/Other + Work-in-Process + Precious Metals; "
                    "ore stockpiles/leach pads excluded"
                ),
            )
        return None

    def _derive_duration_metric(self, rows, metric, year):
        derived = super()._derive_duration_metric(rows, metric, year)
        if derived is not None or metric != "operating_income":
            return derived

        # Some issuers omit a GAAP OperatingIncomeLoss subtotal but disclose
        # GrossProfit and OperatingExpenses in the same XBRL context.
        gross = self._same_context_duration(rows, {"GrossProfit", "GrossProfitLoss"}, year)
        opex = self._same_context_duration(rows, {"OperatingExpenses", "OperatingExpense", "OperatingExpensesAndCostOfRevenue"}, year)
        for key, g in gross.items():
            if key not in opex:
                continue
            o = opex[key]
            return XBRLCandidate(
                metric="operating_income", namespace="derived",
                concept="DerivedOperatingIncomeFromGrossProfitMinusOperatingExpenses",
                label="Operating income (derived from gross profit and operating expenses)",
                value=float(g["value"]) - float(o["value"]),
                unit=g.get("unit") or o.get("unit") or "",
                end=g.get("end"), start=g.get("start"), fy=g.get("fy"),
                form=g.get("form"), filed=g.get("filed"), instant=False,
                dimensioned=False, source="filing-xbrl-derived", score=103.0,
                reason="same-context duration identity: GrossProfit - OperatingExpenses",
            )
        return None

    def search_filing(self, cik, metric, year=None, include_dimensioned=False,
                      submissions=None, limit=20):
        """Run Inline-XBRL filtering with a V2.3.8 exact-concept fast path."""
        submissions = submissions or self.submissions(cik)
        rows, meta = self._inline_filing_rows(cik, submissions)

        # Exact concepts are authoritative. This path deliberately happens
        # before V2.3.5/V2.3.4 generic exclusion and label gates.
        exact_candidates = self._exact_rows_first(rows, metric, year)

        # NEM's validated inventory fallback: the total concept may be absent
        # from Inline XBRL even though its four economic components are present.
        if not exact_candidates and metric == "inventory":
            derived_inventory = self._derive_validated_newmont_inventory(rows, year)
            if derived_inventory is not None:
                exact_candidates = [derived_inventory]
                meta["derived_inventory"] = "validated_newmont_components"

        if exact_candidates:
            # Deduplicate identical economic facts emitted more than once.
            dedup = {}
            for c in exact_candidates:
                key = (c.concept, c.value, c.start, c.end, c.unit)
                dedup[key] = c
            exact_candidates = list(dedup.values())

            if metric == "interest_expense":
                for candidate in exact_candidates:
                    if (_local_concept(candidate.concept) == "InterestIncomeExpenseNonoperatingNet"
                            and candidate.value is not None and candidate.value < 0):
                        candidate.value = abs(float(candidate.value))
                        candidate.concept = "DerivedInterestExpenseFromNetInterestExpense"
                        candidate.namespace = "derived"
                        candidate.source = "filing-xbrl-derived"
                        candidate.score = 125.0
                        candidate.reason = "net interest expense fallback: abs(InterestIncomeExpenseNonoperatingNet)"

            exact_candidates.sort(
                key=lambda x: (x.score, _date(x.end) or date.min, x.filed or ""),
                reverse=True,
            )
            meta["target_fy"] = year
            meta["exact_fast_path"] = True
            return exact_candidates[:limit], meta

        candidates: list[XBRLCandidate] = []
        for r in rows:
            end = _date(r.get("end"))
            if year is not None and (end is None or end.year != int(year)):
                continue
            if not include_dimensioned and r.get("dimensioned"):
                continue

            concept = r.get("concept", "")
            label = r.get("label", "")
            if _hard_excluded(metric, concept, label):
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

        candidates.sort(key=lambda x: (x.score, _date(x.end) or date.min, x.filed or ""), reverse=True)
        meta["target_fy"] = year
        return candidates[:limit], meta


SECXBRLSearchV2_3 = SECXBRLSearchV2_3_8
SECXBRLSearchV2 = SECXBRLSearchV2_3_8
