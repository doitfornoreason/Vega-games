from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List

class Trader:

    def run(self, state: TradingState):
        products = ["TOMATOES", "EMERALDS"]
        result = {}
        for product in products:

            orders: List[Order] = []
    
            if product in state.order_depths:
                order_depth: OrderDepth = state.order_depths[product]
                fair_value = (sum(list(order_depth.buy_orders.keys())[-2:])+sum(list(order_depth.sell_orders.keys())[-2:]))/4
                print(fair_value)
    
                
                position = state.position.get(product, 0)
    
                if position>0:
                    if len(order_depth.buy_orders) != 0:
                        best_bid = max(order_depth.buy_orders.keys())
                        best_bid_vol = order_depth.buy_orders[best_bid]
                        if best_bid == fair_value:
                            orders.append(Order(product, best_bid, -min(best_bid_vol, position)))
        
                if position<0:
                    if len(order_depth.buy_orders) != 0:
                        best_ask = min(order_depth.sell_orders.keys())
                        best_ask_vol = order_depth.sell_orders[best_ask]
                        if best_ask == fair_value:
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