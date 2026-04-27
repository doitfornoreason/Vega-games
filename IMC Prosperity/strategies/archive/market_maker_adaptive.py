from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import json

class Trader:

    SMA_WINDOW = 20
    VWAP_DECAY = 0.995
    ERROR_DECAY = 0.95  # exponential decay on prediction errors (recent errors matter more)
    MIN_WEIGHT = 0.05   # floor so no signal gets fully zeroed out

    def run(self, state: TradingState):
        result = {}
        product = "TOMATOES"

        # --- Restore state ---
        if state.traderData:
            data = json.loads(state.traderData)
        else:
            data = {
                "mid_prices": [],
                "vwap_num": 0.0,
                "vwap_den": 0.0,
                "prev_sma": None,
                "prev_vwap": None,
                "prev_wmid": None,
                "err_sma": 1.0,   # start equal (1.0 each)
                "err_vwap": 1.0,
                "err_wmid": 1.0,
            }

        mid_prices: list = data["mid_prices"]
        vwap_num: float = data["vwap_num"]
        vwap_den: float = data["vwap_den"]
        prev_sma = data["prev_sma"]
        prev_vwap = data["prev_vwap"]
        prev_wmid = data["prev_wmid"]
        err_sma: float = data["err_sma"]
        err_vwap: float = data["err_vwap"]
        err_wmid: float = data["err_wmid"]

        orders: List[Order] = []

        if product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]

            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

            if best_bid is not None and best_ask is not None:
                mid = (best_bid + best_ask) / 2
                bid_vol = order_depth.buy_orders[best_bid]
                ask_vol = abs(order_depth.sell_orders[best_ask])
                total_vol = bid_vol + ask_vol

                # --- Compute three signals ---
                # 1) SMA
                mid_prices.append(mid)
                if len(mid_prices) > self.SMA_WINDOW:
                    mid_prices = mid_prices[-self.SMA_WINDOW:]
                sma = sum(mid_prices) / len(mid_prices)

                # 2) VWAP (decayed cumulative)
                vwap_num *= self.VWAP_DECAY
                vwap_den *= self.VWAP_DECAY
                if product in state.market_trades:
                    for trade in state.market_trades[product]:
                        vwap_num += trade.price * trade.quantity
                        vwap_den += trade.quantity
                vwap = vwap_num / vwap_den if vwap_den > 0 else mid

                # 3) Weighted mid
                wmid = (best_bid * ask_vol + best_ask * bid_vol) / total_vol if total_vol > 0 else mid

                # --- Update prediction errors using previous tick's signals ---
                if prev_sma is not None:
                    err_sma = self.ERROR_DECAY * err_sma + (1 - self.ERROR_DECAY) * abs(prev_sma - mid)
                    err_vwap = self.ERROR_DECAY * err_vwap + (1 - self.ERROR_DECAY) * abs(prev_vwap - mid)
                    err_wmid = self.ERROR_DECAY * err_wmid + (1 - self.ERROR_DECAY) * abs(prev_wmid - mid)

                # --- Compute adaptive weights (inverse error) ---
                inv_sma = 1.0 / max(err_sma, 1e-9)
                inv_vwap = 1.0 / max(err_vwap, 1e-9)
                inv_wmid = 1.0 / max(err_wmid, 1e-9)
                inv_total = inv_sma + inv_vwap + inv_wmid

                w_sma = max(inv_sma / inv_total, self.MIN_WEIGHT)
                w_vwap = max(inv_vwap / inv_total, self.MIN_WEIGHT)
                w_wmid = max(inv_wmid / inv_total, self.MIN_WEIGHT)
                # Re-normalize after applying floor
                w_total = w_sma + w_vwap + w_wmid
                w_sma /= w_total
                w_vwap /= w_total
                w_wmid /= w_total

                # --- Fair value ---
                fair_value = w_sma * sma + w_vwap * vwap + w_wmid * wmid

                # --- Place orders: improve by 1 tick, but don't cross fair value ---
                new_bid = best_bid + 1
                new_ask = best_ask - 1

                if new_bid < fair_value:
                    orders.append(Order(product, new_bid, bid_vol))
                if new_ask > fair_value:
                    orders.append(Order(product, new_ask, -ask_vol))

                # --- Save current signals for next tick's error calculation ---
                prev_sma = sma
                prev_vwap = vwap
                prev_wmid = wmid

        result[product] = orders

        traderData = json.dumps({
            "mid_prices": mid_prices,
            "vwap_num": vwap_num,
            "vwap_den": vwap_den,
            "prev_sma": prev_sma,
            "prev_vwap": prev_vwap,
            "prev_wmid": prev_wmid,
            "err_sma": err_sma,
            "err_vwap": err_vwap,
            "err_wmid": err_wmid,
        })
        conversions = 0
        return result, conversions, traderData
