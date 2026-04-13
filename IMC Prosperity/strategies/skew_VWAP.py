from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List

class Trader:

    def run(self, state: TradingState):
        result = {}

        product = "TOMATOES"
        orders: List[Order] = []

        if product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]

            best_bid = max(order_depth.buy_orders.keys())
            best_bid_vol = order_depth.buy_orders[best_bid]
            best_ask = min(order_depth.sell_orders.keys())
            best_ask_vol_abs = abs(order_depth.sell_orders[best_ask])

            # Strategy 6: VWAP (volume-weighted mid) as fair value
            fair_value = (best_bid * best_ask_vol_abs + best_ask * best_bid_vol) / (best_bid_vol + best_ask_vol_abs)

            position = state.position.get(product, 0)

            # Strategy 4: skew quotes based on position
            bid_offset = 1 + max(0, position // 10)
            ask_offset = 1 + max(0, -position // 10)

            # Position flattening
            if position > 0:
                if best_bid >= fair_value:
                    orders.append(Order(product, best_bid, -min(best_bid_vol, position)))
            if position < 0:
                best_ask_vol = order_depth.sell_orders[best_ask]
                if best_ask <= fair_value:
                    buy_qty = min(best_ask_vol_abs, abs(position))
                    orders.append(Order(product, best_ask, buy_qty))

            # Take liquidity: sell into bids above fair value
            if best_bid > fair_value:
                orders.append(Order(product, best_bid, -best_bid_vol))
            # Improve bid with skewed offset
            if (best_bid + bid_offset) < fair_value:
                orders.append(Order(product, best_bid + bid_offset, best_bid_vol))

            # Take liquidity: buy asks below fair value
            best_ask_vol = order_depth.sell_orders[best_ask]
            if best_ask < fair_value:
                orders.append(Order(product, best_ask, -best_ask_vol))
            # Improve ask with skewed offset
            if (best_ask - ask_offset) > fair_value:
                orders.append(Order(product, best_ask - ask_offset, best_ask_vol))

        result[product] = orders

        traderData = ""
        conversions = 0
        return result, conversions, traderData
