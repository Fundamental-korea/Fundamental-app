"""SEC XBRL resolver V2.3.3.

Targeted fix after V2.3.2 filter diagnostics:
- Inline XBRL concepts are namespace-qualified (e.g. ``us-gaap:AssetsCurrent``)
  while EXACT_CONCEPTS stores local concept names (e.g. ``AssetsCurrent``).
- V2.3.2 therefore rejected every filing candidate when labels were empty.

V2.3.3 normalizes the concept name only for semantic matching. Raw SEC payloads
remain in memory only.
"""
from __future__ import annotations

from sec_xbrl_search_v2_3_2 import SECXBRLSearchV2_3_2
from sec_xbrl_search_v2_2 import EXACT_CONCEPTS


def _local_concept(concept: str | None) -> str:
    """Return the XBRL local concept name from ``us-gaap:Foo`` / ``nem:Foo``."""
    if not concept:
        return ""
    return str(concept).rsplit(":", 1)[-1]


class SECXBRLSearchV2_3_3(SECXBRLSearchV2_3_2):
    """V2.3.2 with namespace-aware exact-concept matching."""

    @staticmethod
    def _is_exact_concept(metric: str, concept: str | None) -> bool:
        return _local_concept(concept) in EXACT_CONCEPTS.get(metric, set())

    def _candidate_allowed(self, metric: str, concept: str, label: str, source: str) -> bool:
        # Exact concepts must win before broad negative filters. This matters
        # for totals whose concept names contain words that also appear in
        # component concepts.
        if self._is_exact_concept(metric, concept):
            return True
        return super()._candidate_allowed(metric, concept, label, source)


SECXBRLSearchV2_3 = SECXBRLSearchV2_3_3
SECXBRLSearchV2 = SECXBRLSearchV2_3_3
