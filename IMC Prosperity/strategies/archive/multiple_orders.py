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

            # VWAP fair value
            fair_value = (best_bid * best_ask_vol_abs + best_ask * best_bid_vol) / (best_bid_vol + best_ask_vol_abs)

            position = state.position.get(product, 0)

            # Skew quotes based on position
            bid_offset = 1 + max(0, position // 10)
            ask_offset = 1 + max(0, -position // 10)

            # Position flattening
            if position > 0:
                if best_bid >= fair_value:
                    orders.append(Order(product, best_bid, -min(best_bid_vol, position)))
            if position < 0:
                if best_ask <= fair_value:
                    buy_qty = min(best_ask_vol_abs, abs(position))
                    orders.append(Order(product, best_ask, buy_qty))

            # Take liquidity: sell into bids above fair value
            if best_bid > fair_value:
                orders.append(Order(product, best_bid, -best_bid_vol))

            # Take liquidity: buy asks below fair value
            best_ask_vol = order_depth.sell_orders[best_ask]
            if best_ask < fair_value:
                orders.append(Order(product, best_ask, -best_ask_vol))

            # Multi-level bid quoting (high/mid/low volume)
            bid_vol_high = max(best_bid_vol, 5)
            bid_vol_mid = max(int(best_bid_vol * 0.5), 3)
            bid_vol_low = max(int(best_bid_vol * 0.25), 1)

            # Dime by 1: high volume, Match: mid volume, Step back: low volume
            # All subject to skew offset and fair value guard
            for price, vol in [
                (best_bid + bid_offset, bid_vol_high),
                (best_bid, bid_vol_mid),
                (best_bid - 1, bid_vol_low),
            ]:
                if price < fair_value:
                    orders.append(Order(product, price, vol))

            # Multi-level ask quoting (high/mid/low volume)
            ask_vol_high = max(best_ask_vol_abs, 5)
            ask_vol_mid = max(int(best_ask_vol_abs * 0.5), 3)
            ask_vol_low = max(int(best_ask_vol_abs * 0.25), 1)

            for price, vol in [
                (best_ask - ask_offset, ask_vol_high),
                (best_ask, ask_vol_mid),
                (best_ask + 1, ask_vol_low),
            ]:
                if price > fair_value:
                    orders.append(Order(product, price, -vol))

        result[product] = orders

        traderData = ""
        conversions = 0
        return result, conversions, traderData
