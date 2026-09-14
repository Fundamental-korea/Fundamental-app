"""Compatibility launcher for the V2.3 regression test using V2.3.1.

The original test file is intentionally left unchanged. This launcher swaps
its imported resolver class before calling its existing main() so the same
regression output can be compared without duplicating the test logic.
"""

import importlib

import sec_xbrl_search_v2_3 as legacy_module
from sec_xbrl_search_v2_3_1 import SECXBRLSearchV2_3 as fixed_resolver

legacy_module.SECXBRLSearchV2_3 = fixed_resolver

test_module = importlib.import_module("test_sec_xbrl_search_v2_3")
test_module.SECXBRLSearchV2_3 = fixed_resolver

test_module.main()
