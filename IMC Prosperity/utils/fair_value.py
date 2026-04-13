"""
Fair Value Calculator — Multiple methods for estimating fair value from order books.

Stdlib-only. All functions take an OrderDepth and return float or None.

Usage in Trader.run():
    from fair_value import simple_mid, weighted_mid, vwap_mid, microprice
    fv = weighted_mid(state.order_depths["RAINFOREST_RESIN"])
"""

from typing import List, Optional

from datamodel import OrderDepth, Trade


def simple_mid(order_depth: OrderDepth) -> Optional[float]:
    """
    Simple mid-price: (best_bid + best_ask) / 2.

    Returns None if either side of the book is empty.
    """
    if not order_depth.buy_orders or not order_depth.sell_orders:
        return None
    best_bid = max(order_depth.buy_orders.keys())
    best_ask = min(order_depth.sell_orders.keys())
    return (best_bid + best_ask) / 2.0


def weighted_mid(order_depth: OrderDepth) -> Optional[float]:
    """
    Volume-weighted mid-price at the top of book (classic microprice).

    Formula: (bid_price * ask_volume + ask_price * bid_volume) / (bid_volume + ask_volume)

    Intuition: if there's more volume on the bid, the price is likely to move up
    (toward the ask), so we weight the ask more heavily.
    """
    if not order_depth.buy_orders or not order_depth.sell_orders:
        return None
    best_bid = max(order_depth.buy_orders.keys())
    best_ask = min(order_depth.sell_orders.keys())
    bid_vol = order_depth.buy_orders[best_bid]               # positive
    ask_vol = abs(order_depth.sell_orders[best_ask])          # sell_orders stores negative volumes
    total_vol = bid_vol + ask_vol
    if total_vol == 0:
        return (best_bid + best_ask) / 2.0
    return (best_bid * ask_vol + best_ask * bid_vol) / total_vol


def microprice(order_depth: OrderDepth) -> Optional[float]:
    """
    Microprice — alias for weighted_mid.
    Named separately for clarity in code that references microstructure literature.
    """
    return weighted_mid(order_depth)


def vwap_mid(order_depth: OrderDepth, depth: int = 3) -> Optional[float]:
    """
    Volume-weighted average price across N levels on each side, averaged.

    Computes VWAP of the top `depth` bid levels and top `depth` ask levels,
    then returns the average of both VWAPs.

    Useful for illiquid books where the best bid/ask may not be representative.
    """
    if not order_depth.buy_orders or not order_depth.sell_orders:
        return None

    # Bids: sorted descending (best first)
    bid_prices = sorted(order_depth.buy_orders.keys(), reverse=True)[:depth]
    bid_vwap_num = 0.0
    bid_vwap_den = 0.0
    for price in bid_prices:
        vol = order_depth.buy_orders[price]  # positive
        bid_vwap_num += price * vol
        bid_vwap_den += vol

    # Asks: sorted ascending (best first)
    ask_prices = sorted(order_depth.sell_orders.keys())[:depth]
    ask_vwap_num = 0.0
    ask_vwap_den = 0.0
    for price in ask_prices:
        vol = abs(order_depth.sell_orders[price])  # make positive
        ask_vwap_num += price * vol
        ask_vwap_den += vol

    if bid_vwap_den == 0 or ask_vwap_den == 0:
        return simple_mid(order_depth)

    bid_vwap = bid_vwap_num / bid_vwap_den
    ask_vwap = ask_vwap_num / ask_vwap_den
    return (bid_vwap + ask_vwap) / 2.0


def decayed_vwap_mid(order_depth: OrderDepth, decay: float = 0.5) -> Optional[float]:
    """
    Volume-weighted average price with geometric decay across levels.

    Each level deeper gets weight decay^i (i=0 for best level):
        Level 1 (best): weight = 1.0
        Level 2:        weight = 0.5
        Level 3:        weight = 0.25
        ...

    This trusts the top of the book more than deeper levels, which may be
    stale or speculative. A good middle ground between weighted_mid (top only)
    and vwap_mid (equal weight across levels).

    Ported from 58839.py's Utils.get_decayed_vwap().

    Args:
        order_depth: current order book
        decay: geometric decay factor (0 < decay <= 1).
            1.0 = equal weight (same as vwap_mid with all levels).
            0.5 = each level matters half as much as the previous.

    Returns:
        Decayed VWAP mid-price, or None if book is empty.
    """
    if not order_depth.buy_orders or not order_depth.sell_orders:
        return None

    # Bids: sorted descending (best first)
    bid_num = 0.0
    bid_den = 0.0
    for i, price in enumerate(sorted(order_depth.buy_orders.keys(), reverse=True)):
        vol = order_depth.buy_orders[price]  # positive
        w = decay ** i
        bid_num += price * vol * w
        bid_den += vol * w

    # Asks: sorted ascending (best first)
    ask_num = 0.0
    ask_den = 0.0
    for i, price in enumerate(sorted(order_depth.sell_orders.keys())):
        vol = abs(order_depth.sell_orders[price])  # make positive
        w = decay ** i
        ask_num += price * vol * w
        ask_den += vol * w

    if bid_den == 0 or ask_den == 0:
        return simple_mid(order_depth)

    return (bid_num / bid_den + ask_num / ask_den) / 2.0


def wall_mid(order_depth: OrderDepth) -> Optional[float]:
    """
    Wall mid-price: average of the prices at the largest-volume levels.

    Identifies the "wall" (deepest liquidity) on each side and averages those prices.
    Useful when large resting orders signal where institutional interest lies.
    """
    if not order_depth.buy_orders or not order_depth.sell_orders:
        return None

    # Find bid price with largest volume
    best_bid_wall = max(order_depth.buy_orders.items(), key=lambda x: x[1])[0]

    # Find ask price with largest absolute volume (sell_orders are negative)
    best_ask_wall = min(order_depth.sell_orders.items(), key=lambda x: x[1])[0]
    # Note: min because sell volumes are negative, so the most negative = largest

    return (best_bid_wall + best_ask_wall) / 2.0


def averaged_worst_case(order_depth: OrderDepth, n: int = 2) -> Optional[float]:
    """
    Average of the n best bid prices + n best ask prices / (2n).

    This replicates the fair value logic from 61722.py:
        fair = (sum of 2 best bids + sum of 2 best asks) / 4

    The idea: by averaging multiple levels, the estimate is more robust to
    single-level manipulation or thin books.
    """
    if not order_depth.buy_orders or not order_depth.sell_orders:
        return None

    # Best n bid prices (highest)
    bid_prices = sorted(order_depth.buy_orders.keys(), reverse=True)[:n]
    # Best n ask prices (lowest)
    ask_prices = sorted(order_depth.sell_orders.keys())[:n]

    total_prices = len(bid_prices) + len(ask_prices)
    if total_prices == 0:
        return None

    return (sum(bid_prices) + sum(ask_prices)) / total_prices


def trade_adjusted_mid(order_depth: OrderDepth, recent_trades: List[Trade],
                       decay: float = 0.9) -> Optional[float]:
    """
    Mid-price adjusted toward recent trade direction using exponentially-decayed imbalance.

    If recent trades are buyer-initiated (near the ask), shifts the fair value up.
    If seller-initiated (near the bid), shifts down.

    Args:
        order_depth: current order book
        recent_trades: list of Trade objects from the current tick
        decay: weight given to each successive trade (most recent = 1.0, previous = decay, etc.)

    Returns:
        Adjusted mid-price, or None if book is empty.
    """
    mid = simple_mid(order_depth)
    if mid is None:
        return None

    if not recent_trades:
        return mid

    best_bid = max(order_depth.buy_orders.keys())
    best_ask = min(order_depth.sell_orders.keys())
    spread = best_ask - best_bid
    if spread <= 0:
        return mid

    # Compute trade imbalance: positive = net buying pressure, negative = net selling
    imbalance = 0.0
    weight = 1.0
    for trade in reversed(recent_trades):
        # Classify: closer to ask = buyer-initiated, closer to bid = seller-initiated
        if trade.price >= best_ask:
            signed_qty = abs(trade.quantity)    # buyer-initiated
        elif trade.price <= best_bid:
            signed_qty = -abs(trade.quantity)   # seller-initiated
        else:
            # In the spread — use distance to classify
            mid_spread = (best_bid + best_ask) / 2.0
            signed_qty = abs(trade.quantity) if trade.price >= mid_spread else -abs(trade.quantity)

        imbalance += weight * signed_qty
        weight *= decay

    # Normalize imbalance and scale by a fraction of the spread
    # Max adjustment = 0.25 * spread
    total_qty = sum(abs(t.quantity) for t in recent_trades) or 1.0
    normalized = imbalance / total_qty  # in [-1, +1] roughly
    adjustment = normalized * 0.25 * spread

    return mid + adjustment


def ema_fair_value(current_mid: float, prev_ema: float, alpha: float = 0.1) -> float:
    """
    Exponential moving average fair value (stateless one-step update).

    EMA(t) = alpha * current_mid + (1 - alpha) * prev_ema

    The caller manages state persistence via traderData. For the first tick
    when there is no prev_ema, pass prev_ema = current_mid.

    Args:
        current_mid: current mid-price (from any of the above functions)
        prev_ema: previous EMA value (from traderData)
        alpha: smoothing factor (0 < alpha <= 1). Smaller = smoother.

    Returns:
        Updated EMA value.
    """
    return alpha * current_mid + (1.0 - alpha) * prev_ema
