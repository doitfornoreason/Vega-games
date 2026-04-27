from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
from numpy import mean

TOLERANCE = 1

class Utils:
    """Utility class for common functions."""

    @staticmethod
    def get_bottom_level_unweighted_average(order_depth: OrderDepth) -> float:
        # Sensitive to noise from outliers, but there were none of these in days -2 and -1.
        return mean([next(reversed(order_depth.buy_orders)), next(reversed(order_depth.sell_orders))])

    @staticmethod
    def get_microprice(order_depth: OrderDepth) -> float:
        # Cross-weighted average of the top level of the order book.
        best_bid, best_ask = next(iter(order_depth.buy_orders)), next(iter(order_depth.sell_orders))
        return (best_bid * order_depth.sell_orders[best_ask] + best_ask * order_depth.buy_orders[best_bid]) \
            / (order_depth.buy_orders[best_bid] + order_depth.sell_orders[best_ask])
        
    @staticmethod
    def get_decayed_vwap(order_depth: OrderDepth, decay_factor=0.5) -> float:
        # VWAP of all levels of the order book, with a geometric decay factor.
        # The weight for the ith (1-indexed) level is decay_factor^(i-1).
        denom = weighted_sum = 0
        for i, bid in enumerate(order_depth.buy_orders):
            weighted_sum += bid * order_depth.buy_orders[bid] * decay_factor**i
            denom += order_depth.buy_orders[bid] * decay_factor**i
        vwap_bid = weighted_sum / denom if denom != 0 else None

        weighted_sum = denom = 0
        for i, ask in enumerate(order_depth.sell_orders):
            weighted_sum += ask * order_depth.sell_orders[ask] * decay_factor**i
            denom += order_depth.sell_orders[ask] * decay_factor**i
        vwap_ask = weighted_sum / denom if denom != 0 else None

        return mean([vwap_bid, vwap_ask])
        
class Trader:

    def run(self, state: TradingState):
        products = ["TOMATOES", "EMERALDS"]
        result = {}
        for product in products:

            orders: List[Order] = []
    
            if product in state.order_depths:
                order_depth: OrderDepth = state.order_depths[product]
                fair_value = Utils.get_bottom_level_unweighted_average(order_depth)
                print(fair_value)
    
                
                position = state.position.get(product, 0)
    
                if position > 0:
                    if len(order_depth.buy_orders) != 0:
                        best_bid = max(order_depth.buy_orders.keys())
                        best_bid_vol = order_depth.buy_orders[best_bid]
                        if best_bid >= fair_value - TOLERANCE:
                            orders.append(Order(product, best_bid, -min(best_bid_vol, position)))
        
                if position < 0:
                    if len(order_depth.buy_orders) != 0:
                        best_ask = min(order_depth.sell_orders.keys())
                        best_ask_vol = order_depth.sell_orders[best_ask]
                        if best_ask <= fair_value + TOLERANCE:
                            orders.append(Order(product, best_ask, -max(best_ask_vol, position)))
                        
        
                        
               # Best bid: highest buy price (buy_orders is dict {price: volume}, volume positive)
                    
                if len(order_depth.buy_orders) != 0:
                    best_bid = max(order_depth.buy_orders.keys())
                    best_bid_vol = order_depth.buy_orders[best_bid]
                    market_bid = list(order_depth.buy_orders.keys())[-2]
                    market_bid_vol = order_depth.buy_orders[market_bid]
                    if best_bid > fair_value:
                        orders.append(Order(product, best_bid, -best_bid_vol))
                    # Improve bid by 1 tick, same volume (positive = buy)
                    if (market_bid + 1) < fair_value:
                        orders.append(Order(product, market_bid + 1, market_bid_vol))
                        
                # Best ask: lowest sell price (sell_orders is dict {price: volume}, volume negative)
                if len(order_depth.sell_orders) != 0:
                    best_ask = min(order_depth.sell_orders.keys())
                    best_ask_vol = order_depth.sell_orders[best_ask]
                    market_ask = list(order_depth.sell_orders.keys())[-2]
                    market_ask_vol = order_depth.sell_orders[market_ask]
                    if best_ask < fair_value:
                        orders.append(Order(product, best_ask, -best_ask_vol))
                    # Improve ask by 1 tick, same volume (negative = sell)
                    if (market_ask - 1) > fair_value:
                        orders.append(Order(product, market_ask - 1, market_ask_vol))
                print(orders)
             
    
            result[product] = orders

        traderData = ""
        conversions = 0
        return result, conversions, traderData