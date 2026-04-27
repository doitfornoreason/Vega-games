from datamodel import OrderDepth, UserId, TradingState, Order, Symbol
from typing import List
from numpy import mean, sign

THEO_EMERALDS = 10000
POSITION_MAX = {'TOMATOES': 80, 'EMERALDS': 80}
SKEW_MAX = 1
EDGE = 1

class Product:
    """Product container for trading data."""

    def __init__(self, state: TradingState, symbol: Symbol):
        self.symbol = symbol
        self.orders = []
        self.buy_orders = state.order_depths[symbol].buy_orders.copy()
        self.sell_orders = state.order_depths[symbol].sell_orders.copy()
        self.position = state.position.get(symbol, 0)
        self.position_max = POSITION_MAX[symbol]
        self.theo = self.get_theo()

    def place_order(self, price: int, volume: int):
        # Place an order, updating the order books and position accordingly.
        if volume == 0: return
        self.orders.append(Order(self.symbol, price, volume))
        if volume > 0:
            if price in self.sell_orders:
                self.sell_orders[price] += volume
                if self.sell_orders[price] == 0:
                    del self.sell_orders[price]
            if price in self.buy_orders:
                self.buy_orders[price] += volume
        else: # volume < 0
            if price in self.buy_orders:
                self.buy_orders[price] += volume
                if self.buy_orders[price] == 0:
                    del self.buy_orders[price]
            if price in self.sell_orders:
                self.sell_orders[price] += volume
        self.position += volume

    def take_favorable_trades(self):
        # Take all favorable trades for the product, regardless of position limits.
        for bid in self.buy_orders.copy():
            if bid > self.theo + EDGE:
                trade_vol = -min(self.buy_orders[bid], self.position + self.position_max)
                self.place_order(bid, trade_vol)
        for ask in self.sell_orders.copy():
            if ask < self.theo - EDGE:
                trade_vol = min(-self.sell_orders[ask], self.position_max - self.position)
                self.place_order(ask, trade_vol)

    def reduce_absolute_position(self):
        # Bring position closer to zero by making trades at the theo.
        if self.position > 0:
            for bid in self.buy_orders.copy():
                if self.theo - 1 < bid <= self.theo:
                    trade_vol = -min(self.buy_orders[bid], self.position)
                    self.place_order(bid, trade_vol)
        elif self.position < 0:
            for ask in self.sell_orders.copy():
                if self.theo <= ask < self.theo + 1:
                    trade_vol = min(-self.sell_orders[ask], -self.position)
                    self.place_order(ask, trade_vol)

    def make_a_market(self):
        # Make a market by improving orders on both sides of the theo.
        market_bid = next((bid for bid in self.buy_orders if bid + 1 < self.theo), None)
        market_bid_vol = self.buy_orders[market_bid] if market_bid is not None else 0
        self.place_order(market_bid + 1, market_bid_vol)

        market_ask = next((ask for ask in self.sell_orders if ask - 1 > self.theo), None)
        market_ask_vol = self.sell_orders[market_ask] if market_ask is not None else 0
        self.place_order(market_ask - 1, market_ask_vol)

    def get_theo(self):
        match self.symbol:
            case 'TOMATOES':
                return Market.get_bottom_2_levels_mean(self) - SKEW_MAX * (self.position / self.position_max)
            case 'EMERALDS':
                return THEO_EMERALDS
            
    def get_skew(self):
        return

class Market:
    """Utility class for market price functions."""

    @staticmethod
    def get_bottom_2_levels_mean(product: Product) -> float:
        # Heuristic for market price of tomatoes and emeralds.
        return mean(list(product.buy_orders)[-2:] + list(product.sell_orders)[-2:])
        
class Trader:

    def run(self, state: TradingState):
        symbols = ('TOMATOES', 'EMERALDS')
        result = {}
        for symbol in symbols:    
            product = Product(state, symbol)

            product.take_favorable_trades()
            product.reduce_absolute_position()
            product.make_a_market()
    
            result[symbol] = product.orders

        traderData = ''
        conversions = 0
        return result, conversions, traderData