"""SEC XBRL resolver V2.3.5.

Targeted fix after V2.3.4 regression:
- exact concepts must bypass both hard-exclusion and candidate-allowed filters;
- Inline XBRL rows preserve contextRef so same-context derivations can work;
- inherited generic derivations can now match Assets/Equity and duration facts.

Raw SEC payloads remain in memory only.
"""
from __future__ import annotations

from sec_xbrl_search_v2_3_4 import SECXBRLSearchV2_3_4, EXACT_CONCEPTS_V234


class SECXBRLSearchV2_3_5(SECXBRLSearchV2_3_4):
    """V2.3.4 with the exact-first acceptance gate fixed."""

    def _candidate_allowed(self, metric, concept, label, source):
        # Exact canonical concepts are authoritative. They must not be rejected
        # merely because their concept name contains a generic negative token
        # or because Inline XBRL label extraction is empty.
        local = (concept or "").rsplit("}", 1)[-1].rsplit(":", 1)[-1]
        if local in EXACT_CONCEPTS_V234.get(metric, set()):
            return True
        return super()._candidate_allowed(metric, concept, label, source)


SECXBRLSearchV2_3 = SECXBRLSearchV2_3_5
SECXBRLSearchV2 = SECXBRLSearchV2_3_5
