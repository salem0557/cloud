import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import uw


def test_occ_parsing():
    p = uw.parse_occ("AAPL240202P00185000")
    assert p == {"ticker": "AAPL", "expiry": "2024-02-02", "type": "put", "strike": 185.0}
    assert uw.parse_occ("UBER260911C00076000")["strike"] == 76.0
    assert uw.parse_occ("garbage") is None


def test_uw_string_numbers_are_coerced():
    """UW sends '4.05' not 4.05 — the whole pipeline breaks without this."""
    assert uw._num("4.05") == 4.05
    assert uw._num(None) == 0.0
    assert uw._num("") == 0.0
    assert uw._num("not-a-number") == 0.0


def test_contract_normalisation_maps_uw_field_names():
    row = {"option_symbol": "AAPL231020C00185000", "strike": 185, "expires": "2023-10-20",
           "option_type": "call", "nbbo_bid": "4.35", "nbbo_ask": "4.45",
           "delta": "0.4573", "open_interest": 1200, "volume": 842}
    c = uw._normalise_contract(row)
    assert c["bid"] == 4.35 and c["ask"] == 4.45      # nbbo_bid/ask -> bid/ask
    assert c["type"] == "call"                        # option_type -> type
    assert c["expiry"] == "2023-10-20"                # expires -> expiry
    assert c["delta"] == 0.4573


def test_normalisation_falls_back_to_the_occ_symbol():
    c = uw._normalise_contract({"option_symbol": "UBER260911C00076000"})
    assert c["strike"] == 76.0 and c["type"] == "call" and c["expiry"] == "2026-09-11"
