"""Crypto scalping bot: an analyst agent that scores setups and a trader
agent that executes only what survives the risk gate.

Nothing in this package places a real order unless CRYPTO_LIVE_TRADING=1 and
CRYPTO_LIVE_CONFIRM is set to the exact confirmation phrase; the default is
paper trading against live prices.
"""
