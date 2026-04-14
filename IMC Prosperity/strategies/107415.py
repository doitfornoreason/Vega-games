from datamodel import OrderDepth, TradingState, Order
from typing import List, Optional

POSITION_LIMIT = 80


def calc_theo(order_depth: OrderDepth) -> Optional[float]:
    """
    Theoretical value (VWAP / microprice style):

        theo = (best_bid * ask_volume + best_ask * bid_volume) / (bid_volume + ask_volume)

    Intuition: the side with LESS volume is the side that will get consumed
    first, so price is more likely to move toward the thinner side. Weighting
    each price by the OPPOSITE side's volume pulls theo toward the thin side.

        - Big asks (many sellers), small bids  -> theo pulled toward best_bid (down)
        - Big bids (many buyers), small asks   -> theo pulled toward best_ask (up)

    Corner cases:
        - No bids and no asks     -> None
        - Only bids, no asks      -> best bid
        - Only asks, no bids      -> best ask
        - bid_vol + ask_vol == 0  -> plain mid (shouldn't happen if both sides exist)
    """
    has_bids = len(order_depth.buy_orders) > 0
    has_asks = len(order_depth.sell_orders) > 0

    if not has_bids and not has_asks:
        return None
    if not has_bids:
        return float(min(order_depth.sell_orders.keys()))
    if not has_asks:
        return float(max(order_depth.buy_orders.keys()))

    best_bid = max(order_depth.buy_orders.keys())
    best_ask = min(order_depth.sell_orders.keys())
    bid_vol = order_depth.buy_orders[best_bid]           # positive
    ask_vol = -order_depth.sell_orders[best_ask]         # stored negative -> flip to positive

    total_vol = bid_vol + ask_vol
    if total_vol == 0:
        return (best_bid + best_ask) / 2

    return (best_bid * ask_vol + best_ask * bid_vol) / total_vol


class Trader:
    """
    Round 1 strategy v4.

    Identical to v3 except `calc_theo` now uses the microprice / volume-weighted
    formula instead of the "worst-bids-and-asks" average.

    ASH_COATED_OSMIUM: dime the market, gated by microprice theo.
    INTARIAN_PEPPER_ROOT: lift the ask book to +80 on tick 0 and hold.
    """

    def run(self, state: TradingState):
        result = {}

        # ---------- ASH_COATED_OSMIUM: dime the market, gated by theo ----------
        product = "ASH_COATED_OSMIUM"
        orders: List[Order] = []
        if product in state.order_depths:
            od = state.order_depths[product]
            position = state.position.get(product, 0)
            buy_cap = POSITION_LIMIT - position
            sell_cap = POSITION_LIMIT + position
            theo = calc_theo(od)

            if theo is not None:
                if len(od.buy_orders) > 0 and buy_cap > 0:
                    best_bid = max(od.buy_orders.keys())
                    improved_bid = best_bid + 1
                    if improved_bid < theo:
                        orders.append(Order(product, improved_bid, buy_cap))

                if len(od.sell_orders) > 0 and sell_cap > 0:
                    best_ask = min(od.sell_orders.keys())
                    improved_ask = best_ask - 1
                    if improved_ask > theo:
                        orders.append(Order(product, improved_ask, -sell_cap))

        result[product] = orders

        # ---------- INTARIAN_PEPPER_ROOT: max long, then hold ----------
        product = "INTARIAN_PEPPER_ROOT"
        orders = []
        if product in state.order_depths:
            od = state.order_depths[product]
            position = state.position.get(product, 0)
            buy_cap = POSITION_LIMIT - position

            if buy_cap > 0 and len(od.sell_orders) > 0:
                remaining = buy_cap
                for ask_price in sorted(od.sell_orders.keys()):
                    ask_vol_available = -od.sell_orders[ask_price]
                    qty = min(ask_vol_available, remaining)
                    if qty > 0:
                        orders.append(Order(product, ask_price, qty))
                        remaining -= qty
                    if remaining <= 0:
                        break

        result[product] = orders

        return result, 0, ""