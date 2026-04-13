from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List

class Trader:

    def run(self, state: TradingState):
        result = {}

        product = "TOMATOES"
        orders: List[Order] = []

        if product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]

            # Best bid: highest buy price (buy_orders is dict {price: volume}, volume positive)
            if len(order_depth.buy_orders) != 0:
                best_bid = max(order_depth.buy_orders.keys())
                best_bid_vol = order_depth.buy_orders[best_bid]
                # Improve bid by 1 tick, same volume (positive = buy)
                orders.append(Order(product, best_bid + 2, best_bid_vol))

            # Best ask: lowest sell price (sell_orders is dict {price: volume}, volume negative)
            if len(order_depth.sell_orders) != 0:
                best_ask = min(order_depth.sell_orders.keys())
                best_ask_vol = order_depth.sell_orders[best_ask]
                # Improve ask by 1 tick, same volume (negative = sell)
                orders.append(Order(product, best_ask - 2, best_ask_vol))

        result[product] = orders

        traderData = ""
        conversions = 0
        return result, conversions, traderData
