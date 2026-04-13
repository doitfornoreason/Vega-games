"""
Execution Helpers — Order generation functions for Prosperity trading.

All functions return list[Order], ready to be added to the result dict.
All Order.price and Order.quantity values are int (enforced by runner type checks).

No latency/slippage concerns — Prosperity is snapshot-based.

Usage in Trader.run():
    from execution import market_take, passive_quote, flatten_position, clip_to_limit

    orders = []
    orders += market_take(order_depth, "buy", fair_value, threshold=2, position=pos, limit=50, symbol="KELP")
    orders += passive_quote(fair_value, spread=4, position=pos, limit=50, symbol="KELP")
    result["KELP"] = orders
"""

from typing import List

from datamodel import Order, OrderDepth


def clip_to_limit(desired_qty: int, position: int, limit: int) -> int:
    """
    Clip desired_qty so that position + desired_qty stays within [-limit, +limit].

    Critical safety function — ensures no order violates position limits.

    Args:
        desired_qty: how many to trade (positive = buy, negative = sell)
        position: current position
        limit: maximum absolute position allowed

    Returns:
        Clipped quantity. May be 0 if already at limit.

    Examples:
        clip_to_limit(20, 40, 50)  → 10  (can only buy 10 more)
        clip_to_limit(-20, -40, 50) → -10 (can only sell 10 more)
        clip_to_limit(10, 50, 50)  → 0   (at limit, can't buy)
    """
    if desired_qty > 0:
        # Buying: position + qty <= limit
        max_buy = limit - position
        return min(desired_qty, max(0, max_buy))
    elif desired_qty < 0:
        # Selling: position + qty >= -limit
        max_sell = -limit - position  # this is negative
        return max(desired_qty, min(0, max_sell))
    return 0


def market_take(order_depth: OrderDepth, side: str, fair_value: float,
                threshold: float, position: int, limit: int,
                symbol: str) -> List[Order]:
    """
    Cross the spread when prices deviate from fair value by more than threshold.

    For side='buy': lifts ask orders priced below fair_value - threshold.
    For side='sell': hits bid orders priced above fair_value + threshold.

    This is the aggressive execution strategy — it takes liquidity when
    the price is sufficiently mispriced.

    Args:
        order_depth: current order book
        side: 'buy' or 'sell' (or 'both' to do both)
        fair_value: estimated fair value
        threshold: minimum deviation from fair value to trigger taking
        position: current position
        limit: position limit
        symbol: product symbol

    Returns:
        List of Order objects.
    """
    orders: List[Order] = []
    running_position = position

    if side in ("buy", "both"):
        # Buy: take asks below fair_value - threshold
        if order_depth.sell_orders:
            sorted_asks = sorted(order_depth.sell_orders.keys())
            for ask_price in sorted_asks:
                if ask_price > fair_value - threshold:
                    break  # no more mispriced asks

                # sell_orders volumes are negative, so negate for quantity we can buy
                available = -order_depth.sell_orders[ask_price]  # positive
                qty = clip_to_limit(available, running_position, limit)
                if qty > 0:
                    orders.append(Order(symbol, ask_price, qty))
                    running_position += qty

    if side in ("sell", "both"):
        # Sell: take bids above fair_value + threshold
        if order_depth.buy_orders:
            sorted_bids = sorted(order_depth.buy_orders.keys(), reverse=True)
            for bid_price in sorted_bids:
                if bid_price < fair_value + threshold:
                    break  # no more mispriced bids

                available = order_depth.buy_orders[bid_price]  # positive
                qty = clip_to_limit(-available, running_position, limit)  # negative = sell
                if qty < 0:
                    orders.append(Order(symbol, bid_price, qty))
                    running_position += qty

    return orders


def passive_quote(fair_value: float, spread: float, position: int, limit: int,
                  symbol: str, skew_factor: float = 0.0,
                  base_qty: int = 0) -> List[Order]:
    """
    Post passive bid and ask around fair value with position-based skew.

    The skew mechanism shifts both quotes to favor reducing inventory:
    - Long position → skew quotes down (less aggressive buying, more aggressive selling)
    - Short position → skew quotes up (more aggressive buying, less aggressive selling)

    Args:
        fair_value: estimated fair value
        spread: total bid-ask spread to quote (bid and ask are each spread/2 from mid)
        position: current position
        limit: position limit
        symbol: product symbol
        skew_factor: how aggressively to skew (0 = no skew, 1 = full skew)
        base_qty: quantity per side. If 0, uses remaining capacity to limit.

    Returns:
        List of Orders (0, 1, or 2 orders).
    """
    orders: List[Order] = []

    # Compute skew: shifts both bid and ask
    skew = 0.0
    if skew_factor != 0 and limit != 0:
        skew = -skew_factor * (position / limit) * (spread / 2.0)

    bid_price = int(round(fair_value - spread / 2.0 + skew))
    ask_price = int(round(fair_value + spread / 2.0 + skew))

    # Ensure bid < ask
    if bid_price >= ask_price:
        ask_price = bid_price + 1

    # Compute quantities
    if base_qty <= 0:
        buy_qty = clip_to_limit(limit - abs(position), position, limit)
        sell_qty = clip_to_limit(-(limit - abs(position)), position, limit)
    else:
        buy_qty = clip_to_limit(base_qty, position, limit)
        sell_qty = clip_to_limit(-base_qty, position, limit)

    if buy_qty > 0:
        orders.append(Order(symbol, bid_price, buy_qty))
    if sell_qty < 0:
        orders.append(Order(symbol, ask_price, sell_qty))

    return orders


def flatten_position(position: int, fair_value: float, symbol: str) -> List[Order]:
    """
    Generate an order to reduce position toward zero.

    If long: sell at fair_value (or slightly below to ensure fill).
    If short: buy at fair_value (or slightly above to ensure fill).
    If flat: no order.

    Args:
        position: current position
        fair_value: price to use for the flattening order
        symbol: product symbol

    Returns:
        List containing 0 or 1 Order.
    """
    if position == 0:
        return []

    price = int(round(fair_value))

    if position > 0:
        # Sell to flatten: negative quantity
        return [Order(symbol, price, -position)]
    else:
        # Buy to flatten: positive quantity (position is negative, so -position is positive)
        return [Order(symbol, price, -position)]


def penny_best(order_depth: OrderDepth, side: str, symbol: str,
               quantity: int) -> List[Order]:
    """
    Improve the best bid or ask by 1 tick.

    "Pennying" — posting an order 1 tick better than the current best to
    gain queue priority while minimizing adverse selection.

    Args:
        order_depth: current order book
        side: 'buy' or 'sell'
        symbol: product symbol
        quantity: absolute quantity to post (will be made negative for sells)

    Returns:
        List containing 0 or 1 Order.
    """
    if side == "buy" and order_depth.buy_orders:
        best_bid = max(order_depth.buy_orders.keys())
        return [Order(symbol, best_bid + 1, abs(quantity))]

    if side == "sell" and order_depth.sell_orders:
        best_ask = min(order_depth.sell_orders.keys())
        return [Order(symbol, best_ask - 1, -abs(quantity))]

    return []


def iceberg_orders(total_qty: int, slice_size: int, price: int,
                   symbol: str) -> List[Order]:
    """
    Split a large order into smaller slices at the same price.

    In Prosperity's snapshot-based model, all orders are submitted simultaneously,
    so this is primarily for code clarity and potential future extensions.

    Args:
        total_qty: total quantity to trade (positive = buy, negative = sell)
        slice_size: maximum quantity per individual order (positive)
        price: order price
        symbol: product symbol

    Returns:
        List of Order objects that sum to total_qty.
    """
    if total_qty == 0 or slice_size <= 0:
        return []

    orders: List[Order] = []
    remaining = total_qty
    sign = 1 if total_qty > 0 else -1

    while abs(remaining) > 0:
        this_slice = sign * min(abs(remaining), slice_size)
        orders.append(Order(symbol, price, this_slice))
        remaining -= this_slice

    return orders


def position_skew(position: int, limit: int, aggressiveness: float = 1.0) -> float:
    """
    Calculate a skew value based on current inventory.

    Returns a value in [-1, +1]:
    - Positive = we're short, want to buy more (skew toward buying)
    - Negative = we're long, want to sell more (skew toward selling)
    - Zero = flat position, no skew

    Usage: multiply this by a spread/offset to shift quotes.

    Args:
        position: current position
        limit: position limit
        aggressiveness: scaling factor (0 = no skew, 1 = linear, >1 = aggressive)

    Returns:
        Skew value in [-aggressiveness, +aggressiveness], typically [-1, +1].
    """
    if limit == 0:
        return 0.0
    raw = -position / limit  # negative when long, positive when short
    return max(-1.0, min(1.0, raw * aggressiveness))


def generate_grid_orders(fair_value: float, levels: int, spacing: float,
                         base_qty: int, position: int, limit: int,
                         symbol: str) -> List[Order]:
    """
    Generate a grid of buy and sell orders around fair value.

    Places orders at:
        Buy:  fair_value - 1*spacing, fair_value - 2*spacing, ...
        Sell: fair_value + 1*spacing, fair_value + 2*spacing, ...

    Each level gets base_qty units (clipped to position limits).
    The grid naturally provides liquidity across a range of prices.

    Args:
        fair_value: center price for the grid
        levels: number of levels on each side (e.g., 3 = 3 buys + 3 sells)
        spacing: price distance between levels
        base_qty: quantity per level (positive; will be negated for sells)
        position: current position
        limit: position limit
        symbol: product symbol

    Returns:
        List of Order objects (up to 2 * levels orders).
    """
    orders: List[Order] = []
    running_position = position

    for level in range(1, levels + 1):
        # Buy order
        buy_price = int(round(fair_value - level * spacing))
        buy_qty = clip_to_limit(base_qty, running_position, limit)
        if buy_qty > 0:
            orders.append(Order(symbol, buy_price, buy_qty))
            running_position += buy_qty

    # Reset for sells (both sides computed from same starting position)
    running_position = position

    for level in range(1, levels + 1):
        # Sell order
        sell_price = int(round(fair_value + level * spacing))
        sell_qty = clip_to_limit(-base_qty, running_position, limit)
        if sell_qty < 0:
            orders.append(Order(symbol, sell_price, sell_qty))
            running_position += sell_qty

    return orders
