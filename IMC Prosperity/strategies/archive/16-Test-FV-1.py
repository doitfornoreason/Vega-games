from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List

class Trader:

    def run(self, state: TradingState):
        result = {}

        product = "TOMATOES"
        orders: List[Order] = []

        if product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]

            # Bug 1 fix: sort explicitly instead of relying on dict insertion order
            top_2_bids = sorted(order_depth.buy_orders.keys(), reverse=True)[:2]
            top_2_asks = sorted(order_depth.sell_orders.keys())[:2]
            fair_value = (sum(top_2_bids) + sum(top_2_asks)) / (len(top_2_bids) + len(top_2_asks))
            print(fair_value)

            position = state.position.get(product, 0)

            # Bug 2 fix: use >= and <= instead of == so flattening actually triggers
            if position > 0:
                if len(order_depth.buy_orders) != 0:
                    best_bid = max(order_depth.buy_orders.keys())
                    best_bid_vol = order_depth.buy_orders[best_bid]
                    if best_bid >= fair_value:
                        orders.append(Order(product, best_bid, -min(best_bid_vol, position)))
            if position < 0:
                if len(order_depth.sell_orders) != 0:
                    best_ask = min(order_depth.sell_orders.keys())
                    best_ask_vol = order_depth.sell_orders[best_ask]
                    # Bug 3 fix: use abs() for correct volume calculation
                    if best_ask <= fair_value:
                        buy_qty = min(abs(best_ask_vol), abs(position))
                        orders.append(Order(product, best_ask, buy_qty))

            # Take liquidity: sell into bids above fair value
            if len(order_depth.buy_orders) != 0:
                best_bid = max(order_depth.buy_orders.keys())
                best_bid_vol = order_depth.buy_orders[best_bid]
                if best_bid > fair_value:
                    orders.append(Order(product, best_bid, -best_bid_vol))
                # Improve bid by 1 tick, same volume (positive = buy)
                if (best_bid + 1) < fair_value:
                    orders.append(Order(product, best_bid + 1, best_bid_vol))

            # Take liquidity: buy asks below fair value
            if len(order_depth.sell_orders) != 0:
                best_ask = min(order_depth.sell_orders.keys())
                best_ask_vol = order_depth.sell_orders[best_ask]
                if best_ask < fair_value:
                    orders.append(Order(product, best_ask, -best_ask_vol))
                # Improve ask by 1 tick, same volume (negative = sell)
                if (best_ask - 1) > fair_value:
                    orders.append(Order(product, best_ask - 1, best_ask_vol))

        result[product] = orders

        traderData = ""
        conversions = 0
        return result, conversions, traderData
