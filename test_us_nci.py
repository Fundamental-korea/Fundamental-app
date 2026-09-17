from collector_us_fundamental import annual_metrics, build_fact_index


def _fact(tag, value, unit="USD"):
    return {
        "us-gaap": {
            tag: {
                "units": {
                    unit: [
                        {
                            "fy": 2025,
                            "form": "10-K",
                            "start": "2025-01-01",
                            "end": "2025-12-31",
                            "end": "2025-12-31",
                            "filed": "2026-02-01",
                            "val": value,
                            "frame": "CY2025",
                        }
                    ]
                }
            }
        }
    }


def _companyfacts(facts):
    merged = {"facts": {"us-gaap": {}}}
    for payload in facts:
        merged["facts"]["us-gaap"].update(payload["us-gaap"])
    return merged


def _base_index(net_income_value):
    return build_fact_index(
        _companyfacts(
            [
                _fact("RevenueFromContractWithCustomerExcludingAssessedTax", 10000),
                _fact("OperatingIncomeLoss", 2000),
                _fact("NetIncomeLoss", net_income_value),
                _fact("Assets", 20000),
                _fact("StockholdersEquity", 5000),
                _fact("Liabilities", 15000),
                _fact("CashAndCashEquivalentsAtCarryingValue", 1000),
                _fact("AssetsCurrent", 4000),
                _fact("LiabilitiesCurrent", 2000),
                _fact("NetCashProvidedByUsedInOperatingActivities", 1500),
                _fact("InterestExpenseNonOperating", 200),
                _fact("SellingGeneralAndAdministrativeExpense", 1000),
                _fact("EarningsPerShareDiluted", 2),
            ]
        )
    )


def test_nci_is_subtracted_from_consolidated_net_income():
    index = build_fact_index(
        _companyfacts(
            [
                _fact("RevenueFromContractWithCustomerExcludingAssessedTax", 10000),
                _fact("OperatingIncomeLoss", 2000),
                _fact("NetIncomeLoss", 4357),
                _fact("NetIncomeLossAttributableToNoncontrollingInterest", 3373),
                _fact("Assets", 20000),
                _fact("StockholdersEquity", 5000),
                _fact("Liabilities", 15000),
                _fact("CashAndCashEquivalentsAtCarryingValue", 1000),
            ]
        )
    )
    metrics = annual_metrics(index, 2025)
    assert metrics["net_income"] == 984


def test_explicit_parent_net_income_wins_over_subtraction():
    index = build_fact_index(
        _companyfacts(
            [
                _fact("RevenueFromContractWithCustomerExcludingAssessedTax", 10000),
                _fact("OperatingIncomeLoss", 2000),
                _fact("NetIncomeLoss", 4357),
                _fact("NetIncomeLossAttributableToNoncontrollingInterest", 3373),
                _fact("NetIncomeLossAttributableToOwnersOfParent", 984),
                _fact("Assets", 20000),
                _fact("StockholdersEquity", 5000),
                _fact("Liabilities", 15000),
                _fact("CashAndCashEquivalentsAtCarryingValue", 1000),
            ]
        )
    )
    metrics = annual_metrics(index, 2025)
    assert metrics["net_income"] == 984


def test_equity_including_nci_is_subtracted():
    index = build_fact_index(
        _companyfacts(
            [
                _fact("RevenueFromContractWithCustomerExcludingAssessedTax", 10000),
                _fact("OperatingIncomeLoss", 2000),
                _fact("NetIncomeLoss", 1000),
                _fact("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", 20472),
                _fact("MinorityInterest", 15109),
                _fact("Liabilities", 15000),
                _fact("CashAndCashEquivalentsAtCarryingValue", 1000),
            ]
        )
    )
    metrics = annual_metrics(index, 2025)
    assert metrics["net_income"] == 1000
    assert metrics["roic"] is not None
    assert metrics["debt_rate"] is not None
    assert metrics["roa"] is not None


def test_parent_stockholders_equity_is_not_double_subtracted():
    index = _base_index(1000)
    metrics = annual_metrics(index, 2025)
    assert metrics["debt_rate"] == 300.0


if __name__ == "__main__":
    test_nci_is_subtracted_from_consolidated_net_income()
    test_explicit_parent_net_income_wins_over_subtraction()
    test_equity_including_nci_is_subtracted()
    test_parent_stockholders_equity_is_not_double_subtracted()
    print("US NCI tests passed.")
