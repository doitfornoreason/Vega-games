from datamodel import OrderDepth, TradingState, Order
from typing import List, Tuple
import json
import sys
from py_vollib.black_scholes.greeks.analytical import delta
from py_vollib.black_scholes.implied_volatility import implied_volatility

# =================================================================
# Voucher MM configs
# =================================================================

VOUCHER_STRATEGY_CFGS = [
    {"symbol": "VEV_4000", "position_limit": 300, "max_order_size": 40,
     "transition_start": 80, "transition_end": 247,
     "skew_factor": 0.5, "flatten_position": 247, "flatten_slope": 0.5,
     "max_take_distance": 12, "anchor_alpha": 0.0005},
    {"symbol": "VEV_4500", "position_limit": 300, "max_order_size": 40,
     "transition_start": 80, "transition_end": 247,
     "skew_factor": 0.5, "flatten_position": 247, "flatten_slope": 0.5,
     "max_take_distance": 10, "anchor_alpha": 0.0005},
    {"symbol": "VEV_5000", "position_limit": 300, "max_order_size": 40,
     "transition_start": 112, "transition_end": 247,
     "skew_factor": 0.2, "flatten_position": 247, "flatten_slope": 0.2,
     "max_take_distance": 4, "anchor_alpha": 0.0005},
    {"symbol": "VEV_5100", "position_limit": 300, "max_order_size": 40,
     "transition_start": 80, "transition_end": 247,
     "skew_factor": 0.15, "flatten_position": 247, "flatten_slope": 0.15,
     "max_take_distance": 3, "anchor_alpha": 0.0005},
    {"symbol": "VEV_5200", "position_limit": 300, "max_order_size": 25,
     "transition_start": 112, "transition_end": 247,
     "skew_factor": 0.1, "flatten_position": 247, "flatten_slope": 0.1,
     "max_take_distance": 2, "anchor_alpha": 0.0005},
]

# =================================================================
# Butterfly-momentum taker on VEV_5300 / VEV_5400
# =================================================================

BFLY_CFGS = [
    {"center": 5300, "left": "VEV_5200", "mid": "VEV_5300", "right": "VEV_5400",
     "threshold": 1.5, "ewma_alpha": 0.001,
     "max_pos": 300, "take_size": 20},
    {"center": 5400, "left": "VEV_5300", "mid": "VEV_5400", "right": "VEV_5500",
     "threshold": 2.0, "ewma_alpha": 0.001,
     "max_pos": 300, "take_size": 20},
]

# =================================================================
# VELVET MM config (unchanged from gammascalp)
# =================================================================

VELVET_CFG = {
    "symbol": "VELVETFRUIT_EXTRACT",
    "position_limit": 200,
    "max_order_size": 50,
    "fair_value_method": "mid",
    "anchor_alpha": 0.0005,
    "transition_start": 75,
    "transition_end": 165,
    "skew_factor": 0.2,
    "max_take_distance": 4,
    "flatten_position": 165,
    "flatten_slope": 0.2,
}

# =================================================================
# HYDROGEL_PACK — optimised ACO constants (from r3-hydro-optimised)
# =================================================================

HYDRO_POSITION_LIMIT    = 200
HYDRO_MAX_ORDER_SIZE    = 30
HYDRO_TRANSITION_START  = 0
HYDRO_TRANSITION_END    = 180
HYDRO_FLATTEN_POSITION  = 180
HYDRO_MM_WIDTH          = 4

HYDRO_ANCHOR            = 10000
HYDRO_MIN_DEVIATION     = 0
HYDRO_SKEW_FACTOR       = 0.2
HYDRO_MAX_TAKE_DISTANCE = 8
HYDRO_FLATTEN_SLOPE     = 0.4
HYDRO_TAKE_BASE         = 0
HYDRO_TAKE_SLOPE        = 3
HYDRO_CAP_SLOPE         = 0.5
HYDRO_CAP_CONST         = 30

# =================================================================
# Misc constants
# =================================================================

TICKS_PER_DAY = 1_000_000
_os = sys.modules.get("os")
_env = getattr(_os, "environ", {}) if _os is not None else {}
DAY = int(_env.get("PROSPERITY4BT_DAY", 2))
DAY_START_TTE_DAYS = {0: 7, 1: 6, 2: 5}.get(DAY, 5)

RISK_FREE_RATE = 0.0

VEV_STRIKES = {
    "VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000,
    "VEV_5100": 5100, "VEV_5200": 5200, "VEV_5300": 5300,
    "VEV_5400": 5400, "VEV_5500": 5500,
}

SCALP_SYMBOL         = "VELVETFRUIT_EXTRACT"
SCALP_POSITION_LIMIT = 200
SCALP_MIN_TRADE      = 5
SCALP_MAX_SPREAD     = 2


def compute_T(timestamp: int) -> float:
    fraction_of_day = timestamp / TICKS_PER_DAY
    return max(DAY_START_TTE_DAYS - fraction_of_day, 0.0)


class Trader:

    # =========================================================================
    # Fair-value helpers — VELVET mid and maker-quote filter (vouchers)
    # =========================================================================

    def _fair_value_mid(self, order_depth: OrderDepth, prev_makers: dict) -> Tuple[float, dict]:
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return None, prev_makers
        fair = 0.5 * (max(order_depth.buy_orders) + min(order_depth.sell_orders))
        return fair, prev_makers

    def compute_fair_value(self, order_depth: OrderDepth, prev_makers: dict) -> Tuple[float, dict]:
        """Maker-quote filter with memory — used for vouchers."""
        sorted_bids = sorted(order_depth.buy_orders.items(), key=lambda x: -x[0])
        sorted_asks = sorted(order_depth.sell_orders.items(), key=lambda x: x[0])

        def level(lst, i, sign=1):
            if len(lst) > i:
                return lst[i][0], sign * lst[i][1]
            return None, None

        bid_price_1, bid_volume_1 = level(sorted_bids, 0)
        bid_price_2, bid_volume_2 = level(sorted_bids, 1)
        bid_price_3, bid_volume_3 = level(sorted_bids, 2)
        ask_price_1, ask_volume_1 = level(sorted_asks, 0, sign=-1)
        ask_price_2, ask_volume_2 = level(sorted_asks, 1, sign=-1)
        ask_price_3, ask_volume_3 = level(sorted_asks, 2, sign=-1)

        f_mbp1 = f_mbv1 = f_mbp2 = f_mbv2 = None
        f_map1 = f_mav1 = f_map2 = f_mav2 = None

        if bid_price_3 is not None:
            f_mbp1, f_mbv1 = bid_price_2, bid_volume_2
            f_mbp2, f_mbv2 = bid_price_3, bid_volume_3
        elif bid_price_2 is not None:
            if bid_volume_1 < 10 or abs(bid_price_1 - bid_price_2) >= 5:
                if bid_volume_2 >= 20:
                    f_mbp2, f_mbv2 = bid_price_2, bid_volume_2
                else:
                    f_mbp1, f_mbv1 = bid_price_2, bid_volume_2
            else:
                f_mbp1, f_mbv1 = bid_price_1, bid_volume_1
                f_mbp2, f_mbv2 = bid_price_2, bid_volume_2
        elif bid_price_1 is not None:
            if bid_volume_1 >= 20:
                f_mbp2, f_mbv2 = bid_price_1, bid_volume_1
            elif bid_volume_1 >= 10:
                f_mbp1, f_mbv1 = bid_price_1, bid_volume_1

        if ask_price_3 is not None:
            f_map1, f_mav1 = ask_price_2, ask_volume_2
            f_map2, f_mav2 = ask_price_3, ask_volume_3
        elif ask_price_2 is not None:
            if ask_volume_1 < 10 or abs(ask_price_1 - ask_price_2) >= 5:
                if ask_volume_2 >= 20:
                    f_map2, f_mav2 = ask_price_2, ask_volume_2
                else:
                    f_map1, f_mav1 = ask_price_2, ask_volume_2
            else:
                f_map1, f_mav1 = ask_price_1, ask_volume_1
                f_map2, f_mav2 = ask_price_2, ask_volume_2
        elif ask_price_1 is not None:
            if ask_volume_1 >= 20:
                f_map2, f_mav2 = ask_price_1, ask_volume_1
            elif ask_volume_1 >= 10:
                f_map1, f_mav1 = ask_price_1, ask_volume_1

        new_prev = dict(prev_makers)
        if f_mbp1 is not None:
            new_prev["maker_bid_price_1"]  = f_mbp1
            new_prev["maker_bid_volume_1"] = f_mbv1
        if f_mbp2 is not None:
            new_prev["maker_bid_price_2"]  = f_mbp2
            new_prev["maker_bid_volume_2"] = f_mbv2
        if f_map1 is not None:
            new_prev["maker_ask_price_1"]  = f_map1
            new_prev["maker_ask_volume_1"] = f_mav1
        if f_map2 is not None:
            new_prev["maker_ask_price_2"]  = f_map2
            new_prev["maker_ask_volume_2"] = f_mav2

        mbp1 = new_prev.get("maker_bid_price_1")
        mbp2 = new_prev.get("maker_bid_price_2")
        map1 = new_prev.get("maker_ask_price_1")
        map2 = new_prev.get("maker_ask_price_2")

        if (mbp1 is not None and mbp2 is not None
                and map1 is not None and map2 is not None):
            fair = (mbp1 + mbp2 + map1 + map2) / 4
        elif mbp1 is not None and map1 is not None:
            fair = (mbp1 + map1) / 2
        elif mbp2 is not None and map2 is not None:
            fair = (mbp2 + map2) / 2
        else:
            fair = None

        return fair, new_prev

    # =========================================================================
    # HYDROGEL_PACK fair-value (same maker-quote filter, separate entry point)
    # =========================================================================

    def compute_fair_value_hydro(self, order_depth: OrderDepth,
                                  prev_makers: dict) -> Tuple[float, dict]:
        return self.compute_fair_value(order_depth, prev_makers)

    # =========================================================================
    # Generic _mm — VELVET and all vouchers (unchanged from gammascalp)
    # =========================================================================

    def _mm(self, state: TradingState, cfg: dict, prev_makers: dict) -> Tuple[List[Order], dict]:
        """TAKE (anchor-blended ref) + FLATTEN + MAKE (penny-improve, FV-gated)."""
        symbol = cfg["symbol"]
        depth  = state.order_depths.get(symbol)
        if depth is None:
            return [], prev_makers

        orders: List[Order] = []

        if cfg.get("fair_value_method") == "mid":
            fair_value, new_prev = self._fair_value_mid(depth, prev_makers)
        else:
            fair_value, new_prev = self.compute_fair_value(depth, prev_makers)
        if fair_value is None:
            return orders, new_prev

        position       = state.position.get(symbol, 0)
        limit          = cfg["position_limit"]
        buy_capacity   = limit - position
        sell_capacity  = limit + position
        max_order_size = cfg["max_order_size"]

        anchor            = cfg["anchor"]
        trans_start       = cfg["transition_start"]
        trans_end         = cfg["transition_end"]
        skew_factor       = cfg["skew_factor"]
        max_take_distance = cfg["max_take_distance"]
        abs_pos           = abs(position)

        if abs_pos >= trans_end:
            ref_price  = fair_value
            units_past = abs_pos - trans_end
            if position > 0:
                buy_threshold  = units_past * skew_factor
                sell_threshold = 0
            else:
                buy_threshold  = 0
                sell_threshold = units_past * skew_factor
            ask_ceiling = min(ref_price - buy_threshold,
                              fair_value + max_take_distance)
            bid_floor   = max(ref_price + sell_threshold,
                              fair_value - max_take_distance)
        else:
            if abs_pos <= trans_start:
                ref_price = anchor
            else:
                t = (abs_pos - trans_start) / (trans_end - trans_start)
                ref_price = anchor * (1 - t) + fair_value * t
            ask_ceiling = min(ref_price, fair_value + max_take_distance)
            bid_floor   = max(ref_price, fair_value - max_take_distance)

        if depth.sell_orders and buy_capacity > 0:
            for ask_price in sorted(depth.sell_orders.keys()):
                if buy_capacity <= 0 or ask_price >= ask_ceiling:
                    break
                take_size = min(max_order_size, buy_capacity,
                                -depth.sell_orders[ask_price])
                if take_size > 0:
                    orders.append(Order(symbol, ask_price, take_size))
                    buy_capacity -= take_size
                    position     += take_size

        if depth.buy_orders and sell_capacity > 0:
            for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
                if sell_capacity <= 0 or bid_price <= bid_floor:
                    break
                take_size = min(max_order_size, sell_capacity,
                                depth.buy_orders[bid_price])
                if take_size > 0:
                    orders.append(Order(symbol, bid_price, -take_size))
                    sell_capacity -= take_size
                    position      -= take_size

        flatten_pos   = cfg["flatten_position"]
        flatten_slope = cfg["flatten_slope"]

        if position > flatten_pos and depth.buy_orders and sell_capacity > 0:
            units_past   = position - flatten_pos
            flatten_edge = units_past * flatten_slope
            for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
                if sell_capacity <= 0:
                    break
                if bid_price >= fair_value:
                    continue
                if bid_price <= fair_value - flatten_edge:
                    break
                cap = position - flatten_pos
                if cap <= 0:
                    break
                take_size = min(max_order_size, sell_capacity, cap,
                                depth.buy_orders[bid_price])
                if take_size > 0:
                    orders.append(Order(symbol, bid_price, -take_size))
                    sell_capacity -= take_size
                    position      -= take_size
        elif position < -flatten_pos and depth.sell_orders and buy_capacity > 0:
            units_past   = -position - flatten_pos
            flatten_edge = units_past * flatten_slope
            for ask_price in sorted(depth.sell_orders.keys()):
                if buy_capacity <= 0:
                    break
                if ask_price <= fair_value:
                    continue
                if ask_price >= fair_value + flatten_edge:
                    break
                cap = -position - flatten_pos
                if cap <= 0:
                    break
                take_size = min(max_order_size, buy_capacity, cap,
                                -depth.sell_orders[ask_price])
                if take_size > 0:
                    orders.append(Order(symbol, ask_price, take_size))
                    buy_capacity -= take_size
                    position     += take_size

        sorted_bid_prices = sorted(depth.buy_orders.keys(), reverse=True)
        sorted_ask_prices = sorted(depth.sell_orders.keys())

        our_bid = None
        if sorted_bid_prices and buy_capacity > 0:
            our_bid = sorted_bid_prices[0] + 1
            if our_bid >= fair_value and len(sorted_bid_prices) >= 2:
                our_bid = sorted_bid_prices[1] + 1

        our_ask = None
        if sorted_ask_prices and sell_capacity > 0:
            our_ask = sorted_ask_prices[0] - 1
            if our_ask <= fair_value and len(sorted_ask_prices) >= 2:
                our_ask = sorted_ask_prices[1] - 1

        if our_bid is not None and buy_capacity > 0 and our_bid < fair_value:
            orders.append(Order(symbol, our_bid,
                                min(max_order_size, buy_capacity)))
        if our_ask is not None and sell_capacity > 0 and our_ask > fair_value:
            orders.append(Order(symbol, our_ask,
                                -min(max_order_size, sell_capacity)))

        return orders, new_prev

    # =========================================================================
    # HYDROGEL_PACK — optimised ACO (ported from r3-hydro-optimised)
    # =========================================================================

    def _trade_hydro(self, order_depth: OrderDepth, fair_value: float,
                     position: int) -> List[Order]:
        """3-step ACO for HYDROGEL_PACK using the optimised constants."""
        orders: List[Order] = []
        buy_capacity  = HYDRO_POSITION_LIMIT - position
        sell_capacity = HYDRO_POSITION_LIMIT + position

        bids_exist = len(order_depth.buy_orders) > 0
        asks_exist = len(order_depth.sell_orders) > 0

        anchor  = HYDRO_ANCHOR
        abs_pos = abs(position)

        # Deadband: only lean against anchor when FV deviates meaningfully.
        effective_anchor = (anchor if abs(anchor - fair_value) > HYDRO_MIN_DEVIATION
                            else fair_value)

        if abs_pos >= HYDRO_TRANSITION_END:
            ref_price  = fair_value
            units_past = abs_pos - HYDRO_TRANSITION_END
            if position > 0:
                buy_threshold  = units_past * HYDRO_SKEW_FACTOR
                sell_threshold = 0
            else:
                buy_threshold  = 0
                sell_threshold = units_past * HYDRO_SKEW_FACTOR
            ask_ceiling = min(ref_price - buy_threshold,
                              fair_value + HYDRO_MAX_TAKE_DISTANCE)
            bid_floor   = max(ref_price + sell_threshold,
                              fair_value - HYDRO_MAX_TAKE_DISTANCE)
        else:
            if (abs_pos <= HYDRO_TRANSITION_START
                    or (fair_value > anchor and position > 0)
                    or (fair_value < anchor and position < 0)):
                ref_price = effective_anchor
            else:
                t = ((abs_pos - HYDRO_TRANSITION_START)
                     / (HYDRO_TRANSITION_END - HYDRO_TRANSITION_START))
                ref_price = effective_anchor * (1 - t) + fair_value * t
            ask_ceiling = min(ref_price, fair_value + HYDRO_MAX_TAKE_DISTANCE)
            bid_floor   = max(ref_price, fair_value - HYDRO_MAX_TAKE_DISTANCE)

        # --- STEP 1: TAKE (size scales with distance from anchor) ---
        if asks_exist:
            for ask_price in sorted(order_depth.sell_orders.keys()):
                if buy_capacity <= 0 or ask_price >= ask_ceiling:
                    break
                depth_val = max(0.0, anchor - ask_price)
                scaled    = int(HYDRO_TAKE_BASE + HYDRO_TAKE_SLOPE * depth_val)
                take_size = min(HYDRO_MAX_ORDER_SIZE, buy_capacity, scaled,
                                -order_depth.sell_orders[ask_price])
                if take_size > 0:
                    orders.append(Order("HYDROGEL_PACK", ask_price, take_size))
                    buy_capacity -= take_size
                    position     += take_size

        if bids_exist:
            for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                if sell_capacity <= 0 or bid_price <= bid_floor:
                    break
                depth_val = max(0.0, bid_price - anchor)
                scaled    = int(HYDRO_TAKE_BASE + HYDRO_TAKE_SLOPE * depth_val)
                take_size = min(HYDRO_MAX_ORDER_SIZE, sell_capacity, scaled,
                                order_depth.buy_orders[bid_price])
                if take_size > 0:
                    orders.append(Order("HYDROGEL_PACK", bid_price, -take_size))
                    sell_capacity -= take_size
                    position      -= take_size

        # --- STEP 2: FLATTEN ---
        if position > HYDRO_FLATTEN_POSITION and bids_exist and sell_capacity > 0:
            units_past   = position - HYDRO_FLATTEN_POSITION
            flatten_edge = units_past * HYDRO_FLATTEN_SLOPE
            for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                if sell_capacity <= 0:
                    break
                if bid_price >= fair_value:
                    continue
                if bid_price <= fair_value - flatten_edge:
                    break
                cap = position - HYDRO_FLATTEN_POSITION
                if cap <= 0:
                    break
                take_size = min(HYDRO_MAX_ORDER_SIZE, sell_capacity, cap,
                                order_depth.buy_orders[bid_price])
                if take_size > 0:
                    orders.append(Order("HYDROGEL_PACK", bid_price, -take_size))
                    sell_capacity -= take_size
                    position      -= take_size
        elif position < -HYDRO_FLATTEN_POSITION and asks_exist and buy_capacity > 0:
            units_past   = -position - HYDRO_FLATTEN_POSITION
            flatten_edge = units_past * HYDRO_FLATTEN_SLOPE
            for ask_price in sorted(order_depth.sell_orders.keys()):
                if buy_capacity <= 0:
                    break
                if ask_price <= fair_value:
                    continue
                if ask_price >= fair_value + flatten_edge:
                    break
                cap = -position - HYDRO_FLATTEN_POSITION
                if cap <= 0:
                    break
                take_size = min(HYDRO_MAX_ORDER_SIZE, buy_capacity, cap,
                                -order_depth.sell_orders[ask_price])
                if take_size > 0:
                    orders.append(Order("HYDROGEL_PACK", ask_price, take_size))
                    buy_capacity -= take_size
                    position     += take_size

        # --- STEP 3: MAKE (penny-improve, FV-gated) ---
        sorted_bid_prices = sorted(order_depth.buy_orders.keys(), reverse=True)
        sorted_ask_prices = sorted(order_depth.sell_orders.keys())

        if sorted_bid_prices:
            our_bid = sorted_bid_prices[0] + 1
            if our_bid >= fair_value:
                if len(sorted_bid_prices) >= 2:
                    our_bid = sorted_bid_prices[1] + 1
                else:
                    our_bid = int((fair_value - HYDRO_MM_WIDTH) // 1)
        else:
            our_bid = int((fair_value - HYDRO_MM_WIDTH) // 1)

        if sorted_ask_prices:
            our_ask = sorted_ask_prices[0] - 1
            if our_ask <= fair_value:
                if len(sorted_ask_prices) >= 2:
                    our_ask = sorted_ask_prices[1] - 1
                else:
                    our_ask = -int(-(fair_value + HYDRO_MM_WIDTH) // 1)
        else:
            our_ask = -int(-(fair_value + HYDRO_MM_WIDTH) // 1)

        if buy_capacity > 0 and our_bid < fair_value:
            orders.append(Order("HYDROGEL_PACK", our_bid,
                                min(HYDRO_MAX_ORDER_SIZE, buy_capacity)))
        if sell_capacity > 0 and our_ask > fair_value:
            orders.append(Order("HYDROGEL_PACK", our_ask,
                                -min(HYDRO_MAX_ORDER_SIZE, sell_capacity)))

        return orders

    # =========================================================================
    # Butterfly-momentum taker on VEV_5300 / VEV_5400
    # =========================================================================

    def _take_butterfly_momentum(self, state: TradingState,
                                  bfly_mem: dict) -> Tuple[dict, dict]:
        orders_by_sym: dict = {}
        new_mem = dict(bfly_mem)

        for cfg in BFLY_CFGS:
            left_sym = cfg["left"]; mid_sym = cfg["mid"]; right_sym = cfg["right"]
            d_left  = state.order_depths.get(left_sym)
            d_mid   = state.order_depths.get(mid_sym)
            d_right = state.order_depths.get(right_sym)
            if not (d_left and d_mid and d_right
                    and d_left.buy_orders  and d_left.sell_orders
                    and d_mid.buy_orders   and d_mid.sell_orders
                    and d_right.buy_orders and d_right.sell_orders):
                continue

            mid_left  = 0.5 * (max(d_left.buy_orders)  + min(d_left.sell_orders))
            mid_mid   = 0.5 * (max(d_mid.buy_orders)   + min(d_mid.sell_orders))
            mid_right = 0.5 * (max(d_right.buy_orders) + min(d_right.sell_orders))
            bf = mid_left - 2 * mid_mid + mid_right

            key       = f"bf_{cfg['center']}_ewma"
            prev_ewma = bfly_mem.get(key)
            alpha     = cfg["ewma_alpha"]
            ewma      = bf if prev_ewma is None else prev_ewma + alpha * (bf - prev_ewma)
            new_mem[key] = ewma

            if prev_ewma is None:
                continue

            dev       = bf - ewma
            threshold = cfg["threshold"]
            pos       = state.position.get(mid_sym, 0)
            max_pos   = cfg["max_pos"]
            size_cap  = cfg["take_size"]

            if dev > threshold and pos > -max_pos:
                bid     = max(d_mid.buy_orders.keys())
                bid_vol = d_mid.buy_orders[bid]
                size    = min(size_cap, max_pos + pos, bid_vol)
                if size > 0:
                    orders_by_sym.setdefault(mid_sym, []).append(
                        Order(mid_sym, bid, -size))
            elif dev < -threshold and pos < max_pos:
                ask     = min(d_mid.sell_orders.keys())
                ask_vol = -d_mid.sell_orders[ask]
                size    = min(size_cap, max_pos - pos, ask_vol)
                if size > 0:
                    orders_by_sym.setdefault(mid_sym, []).append(
                        Order(mid_sym, ask, size))

        return orders_by_sym, new_mem

    # =========================================================================
    # EWMA anchor update (VELVET and vouchers)
    # =========================================================================

    def _update_anchor(self, state: TradingState, symbol: str, alpha: float,
                       prev_anchor):
        depth = state.order_depths.get(symbol)
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return prev_anchor
        mid = 0.5 * (max(depth.buy_orders) + min(depth.sell_orders))
        if prev_anchor is None:
            return mid
        return prev_anchor + alpha * (mid - prev_anchor)

    # =========================================================================
    # Black-Scholes gamma (gamma scalp)
    # =========================================================================

    def _bs_gamma(self, S: float, K: int, T_years: float,
                  option_price: float) -> float:
        import math
        if T_years <= 0 or S <= 0 or option_price <= 0:
            return 0.0
        try:
            iv = implied_volatility(option_price, S, K, T_years,
                                    RISK_FREE_RATE, 'c')
            if iv <= 0 or iv > 20:
                return 0.0
            d1 = ((math.log(S / K) + 0.5 * iv ** 2 * T_years)
                  / (iv * math.sqrt(T_years)))
            return (math.exp(-0.5 * d1 ** 2)
                    / (math.sqrt(2 * math.pi) * S * iv * math.sqrt(T_years)))
        except Exception:
            return 0.0

    # =========================================================================
    # Gamma scalp on VELVETFRUIT_EXTRACT
    # =========================================================================

    def _gamma_scalp(self, state: TradingState, prev_S: float,
                     mm_velvet_orders: List[Order], T_days: float) -> List[Order]:
        scalp_depth = state.order_depths.get(SCALP_SYMBOL)
        if scalp_depth is None or not scalp_depth.buy_orders or not scalp_depth.sell_orders:
            return mm_velvet_orders

        S = 0.5 * (max(scalp_depth.buy_orders) + min(scalp_depth.sell_orders))

        if prev_S is None:
            return mm_velvet_orders

        delta_S = S - prev_S
        if delta_S == 0:
            return mm_velvet_orders

        T_years = T_days / 365.0

        portfolio_gamma = 0.0
        for sym, K in VEV_STRIKES.items():
            pos = state.position.get(sym, 0)
            if pos == 0:
                continue
            opt_depth = state.order_depths.get(sym)
            if opt_depth is None or not opt_depth.buy_orders or not opt_depth.sell_orders:
                continue
            opt_mid = 0.5 * (max(opt_depth.buy_orders) + min(opt_depth.sell_orders))
            portfolio_gamma += pos * self._bs_gamma(S, K, T_years, opt_mid)

        if portfolio_gamma == 0.0:
            return mm_velvet_orders

        rebalance_raw = portfolio_gamma * delta_S
        rebalance_qty = -round(rebalance_raw)

        if abs(rebalance_qty) < SCALP_MIN_TRADE:
            return mm_velvet_orders

        best_bid = max(scalp_depth.buy_orders.keys())
        best_ask = min(scalp_depth.sell_orders.keys())
        if best_ask - best_bid > SCALP_MAX_SPREAD:
            return mm_velvet_orders

        current_pos   = state.position.get(SCALP_SYMBOL, 0)
        mm_net        = sum(o.quantity for o in mm_velvet_orders)
        projected_pos = current_pos + mm_net

        scalp_orders = list(mm_velvet_orders)

        if rebalance_qty > 0:
            capacity = SCALP_POSITION_LIMIT - projected_pos
            qty = min(rebalance_qty, capacity, -scalp_depth.sell_orders[best_ask])
            if qty > 0:
                scalp_orders.append(Order(SCALP_SYMBOL, best_ask, qty))
        else:
            capacity = SCALP_POSITION_LIMIT + projected_pos
            qty = min(-rebalance_qty, capacity, scalp_depth.buy_orders[best_bid])
            if qty > 0:
                scalp_orders.append(Order(SCALP_SYMBOL, best_bid, -qty))

        return scalp_orders

    # =========================================================================
    # run
    # =========================================================================

    def run(self, state: TradingState):
        if state.traderData:
            memory = json.loads(state.traderData)
        else:
            memory = {}
        memory.setdefault("velvet_prev_makers", {})
        memory.setdefault("hydro_prev_makers", {})
        memory.setdefault("voucher_mm_makers", {})
        memory.setdefault("voucher_anchors", {})

        result = {}

        # --- VELVET MM with adaptive anchor ---
        symbol      = VELVET_CFG["symbol"]
        prev_anchor = memory.get("velvet_anchor")
        anchor      = self._update_anchor(state, symbol,
                                          VELVET_CFG["anchor_alpha"], prev_anchor)
        if anchor is not None:
            memory["velvet_anchor"] = anchor
            dynamic_cfg = {**VELVET_CFG, "anchor": anchor}
            orders, memory["velvet_prev_makers"] = self._mm(
                state, dynamic_cfg, memory["velvet_prev_makers"]
            )
            result[symbol] = orders
        else:
            result[symbol] = []

        # --- HYDROGEL_PACK — optimised ACO ---
        if "HYDROGEL_PACK" in state.order_depths:
            hydro_depth = state.order_depths["HYDROGEL_PACK"]
            hydro_fv, memory["hydro_prev_makers"] = self.compute_fair_value_hydro(
                hydro_depth, memory["hydro_prev_makers"]
            )
            if hydro_fv is not None:
                hydro_pos = state.position.get("HYDROGEL_PACK", 0)
                result["HYDROGEL_PACK"] = self._trade_hydro(
                    hydro_depth, hydro_fv, hydro_pos
                )
            else:
                result["HYDROGEL_PACK"] = []

        # --- Voucher MM ---
        for cfg in VOUCHER_STRATEGY_CFGS:
            sym         = cfg["symbol"]
            prev_anchor = memory["voucher_anchors"].get(sym)
            anchor      = self._update_anchor(state, sym,
                                              cfg["anchor_alpha"], prev_anchor)
            if anchor is None:
                continue
            memory["voucher_anchors"][sym] = anchor

            dynamic_cfg = {**cfg, "anchor": anchor}
            prev = memory["voucher_mm_makers"].setdefault(sym, {})
            orders, memory["voucher_mm_makers"][sym] = self._mm(
                state, dynamic_cfg, prev
            )
            if orders:
                result[sym] = orders

        # --- Butterfly-momentum taker ---
        bfly_mem = memory.setdefault("bfly", {})
        bfly_orders, memory["bfly"] = self._take_butterfly_momentum(state, bfly_mem)
        for sym, ords in bfly_orders.items():
            result.setdefault(sym, []).extend(ords)

        # --- Gamma scalp on VELVETFRUIT_EXTRACT ---
        T_days           = compute_T(state.timestamp)
        prev_S           = memory.get("scalp_prev_S")
        mm_velvet_orders = list(result.get(SCALP_SYMBOL, []))
        result[SCALP_SYMBOL] = self._gamma_scalp(
            state, prev_S, mm_velvet_orders, T_days
        )
        scalp_depth = state.order_depths.get(SCALP_SYMBOL)
        if scalp_depth and scalp_depth.buy_orders and scalp_depth.sell_orders:
            memory["scalp_prev_S"] = 0.5 * (
                max(scalp_depth.buy_orders) + min(scalp_depth.sell_orders)
            )

        return result, 0, json.dumps(memory)