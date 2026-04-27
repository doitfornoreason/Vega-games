from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import json

class Trader:

    def run(self, state: TradingState):
        result = {}
        product = "TOMATOES"

        # --- Restore state across ticks ---
        if state.traderData:
            data = json.loads(state.traderData)
        else:
            data = {"mid_prices": [], "vwap_num": 0.0, "vwap_den": 0.0}

        mid_prices: list = data["mid_prices"]
        vwap_num: float = data["vwap_num"]
        vwap_den: float = data["vwap_den"]

        orders: List[Order] = []

        if product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]

            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

            if best_bid is not None and best_ask is not None:
                mid = (best_bid + best_ask) / 2

                # --- 1) Weighted Mid: skew toward side with more volume ---
                bid_vol = order_depth.buy_orders[best_bid]                  # positive
                ask_vol = abs(order_depth.sell_orders[best_ask])            # make positive
                total_vol = bid_vol + ask_vol
                weighted_mid = (best_bid * ask_vol + best_ask * bid_vol) / total_vol if total_vol > 0 else mid

                # --- 2) SMA of mid prices (rolling window) ---
                SMA_WINDOW = 20
                mid_prices.append(mid)
                if len(mid_prices) > SMA_WINDOW:
                    mid_prices = mid_prices[-SMA_WINDOW:]
                sma = sum(mid_prices) / len(mid_prices)

                # --- 3) VWAP from market trades (cumulative) ---
                VWAP_DECAY = 0.995  # slowly forget old trades
                vwap_num *= VWAP_DECAY
                vwap_den *= VWAP_DECAY
                if product in state.market_trades:
                    for trade in state.market_trades[product]:
                        vwap_num += trade.price * trade.quantity
                        vwap_den += trade.quantity
                vwap = vwap_num / vwap_den if vwap_den > 0 else mid

                # --- Combine into fair value ---
                # Weighted average: SMA anchors trend, VWAP reflects real flow,
                # weighted mid captures current book pressure
                fair_value = 0.4 * sma + 0.3 * vwap + 0.3 * weighted_mid

                # --- Place orders: buy below fair value, sell above ---
                # Improve best bid/ask by 1 tick, but only on our side of fair value
                new_bid = best_bid + 1
                new_ask = best_ask - 1

                # Safety: don't cross fair value (avoid buying above / selling below)
                if new_bid < fair_value:
                    orders.append(Order(product, new_bid, bid_vol))         # buy
                if new_ask > fair_value:
                    orders.append(Order(product, new_ask, -ask_vol))        # sell

        result[product] = orders

        # --- Save state ---
        traderData = json.dumps({
            "mid_prices": mid_prices,
            "vwap_num": vwap_num,
            "vwap_den": vwap_den,
        })
        conversions = 0
        return result, conversions, traderData
