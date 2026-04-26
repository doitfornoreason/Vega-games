from datamodel import OrderDepth, TradingState, Order
from typing import List, Tuple
import json
import math
import sys


# =================================================================
# Black-Scholes helpers (inlined; no external deps so the strategy
# uploads as a single file). Mirror utils/options_toolkit.py.
# =================================================================

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))


def _bs_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if sigma <= 0 or T <= 0:
        return max(0.0, S - K * math.exp(-r * T))
    d1 = _bs_d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def _implied_vol(price: float, S: float, K: float, T: float, r: float,
                 max_iter: int = 50, tol: float = 1e-6) -> float:
    """Newton-Raphson IV. Returns 0 on failure."""
    if T <= 0 or S <= 0 or price <= 0:
        return 0.0
    sigma = 0.2
    for _ in range(max_iter):
        diff = _bs_call(S, K, T, r, sigma) - price
        if abs(diff) < tol:
            return sigma
        d1 = _bs_d1(S, K, T, r, sigma)
        vega = S * _norm_pdf(d1) * math.sqrt(T)
        if vega < 1e-12:
            return 0.0
        sigma -= diff / vega
        if sigma <= 0:
            return 0.0
    return sigma


def _bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = _bs_d1(S, K, T, r, sigma)
    return _norm_pdf(d1) / (S * sigma * math.sqrt(T))


# =================================================================
# Voucher MM configs
# =================================================================

VOUCHER_STRATEGY_CFGS = [
    # VEV_4000, VEV_4500, and VEV_5000 use dedicated tier strategies (see
    # _trade_voucher_tier + VEV4000_TIER_CFG / VEV4500_TIER_CFG /
    # VEV5000_TIER_CFG); they are NOT in this list any more.
    # Fix A is opt-in per cfg via "symmetric_skew": True. Applied only to
    # 5100/5200 — the strikes that pinned long.
    # Per-strike fix scoping:
    #   inventory_skew_factor (Fix B): 5100  (NOT 5200 — hurt PnL)
    #   fair_value_method=spot_intrinsic (Fix E): 5100  (NOT 5200 — TV too noisy)
    # Strikes without these keys fall back to the default behaviour.
    {"symbol": "VEV_5100", "position_limit": 300, "max_order_size": 25,
     "transition_start": 80, "transition_end": 280,
     "skew_factor": 0.15, "flatten_position": 280, "flatten_slope": 0.15,
     "max_take_distance": 3, "anchor_alpha": 0.0005,
     "symmetric_skew": True, "inventory_skew_factor": 3.0,
     "fair_value_method": "spot_intrinsic"},
    {"symbol": "VEV_5200", "position_limit": 300, "max_order_size": 25,
     "transition_start": 112, "transition_end": 280,
     "skew_factor": 0.1, "flatten_position": 280, "flatten_slope": 0.1,
     "max_take_distance": 2, "anchor_alpha": 0.0001,
     "symmetric_skew": True},
]

# =================================================================
# Butterfly-momentum taker on VEV_5300 / VEV_5400
# =================================================================

BFLY_CFGS = [
    {"center": 5300, "left": "VEV_5200", "mid": "VEV_5300", "right": "VEV_5400",
     "threshold": 1.5, "ewma_alpha": 0.002,
     "max_pos": 300, "take_size": 20},
    {"center": 5400, "left": "VEV_5300", "mid": "VEV_5400", "right": "VEV_5500",
     "threshold": 2.0, "ewma_alpha": 0.002,
     "max_pos": 300, "take_size": 20},
]

# =================================================================
# VELVET MM config
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
# HYDROGEL_PACK — tiered-band strategy.
# Tier 1: when ask <= ANCHOR-BAND (or bid >= ANCHOR+BAND+10), aggressively
#         load up to TIER1_LIMIT.
# Tier 2: above TIER1_LIMIT but below SOFT_POSITION_LIMIT, only TAKE inside
#         FV +/- FV_BAND.
# Tier 3: at extreme positions (>=197 or <=-197), TAKE only against FV.
# Plus penny-improve MAKE that's gated by anchor + soft-position state.
# =================================================================

HYDRO_POSITION_LIMIT      = 200
HYDRO_ANCHOR              = 10000
HYDRO_ANCHOR_BAND         = 7
HYDRO_TIER1_LIMIT         = 175
HYDRO_FV_BAND             = 1
HYDRO_MM_SIZE             = 30
HYDRO_SOFT_POSITION_LIMIT = 190

# =================================================================
# VELVETFRUIT_EXTRACT — tiered-band strategy (mirrors HYDRO).
# Custom FV: volume-weighted blend of worst bid + worst_bid+1 and
# worst ask + worst_ask-1 layers (favors deepest resting levels).
# Tier 2 condition is STRICT (ask < FV - FV_BAND for buys, bid > FV
# + FV_BAND for sells), opposite of HYDRO's loose tier 2.
# =================================================================

VELVET_POSITION_LIMIT      = 200
VELVET_ANCHOR              = 5250
VELVET_ANCHOR_BAND         = 10
VELVET_TIER1_LIMIT         = 175
VELVET_FV_BAND             = 1
VELVET_MM_SIZE             = 30
VELVET_SOFT_POSITION_LIMIT = 190
VELVET_TAKE_NEG_SPOT       = 197

# =================================================================
# VEV_4000 / VEV_4500 / VEV_5000 — tiered-band strategies.
# Custom FV: top-3 bid/ask layers, only counting levels with vol > 5
# (filters out tiny noise quotes). Tier 2 condition is STRICT
# (FV +/- FV_BAND). Sell-side anchor offset is product-specific.
# =================================================================

VEV4000_TIER_CFG = {
    "symbol":              "VEV_4000",
    "anchor":              1250,
    "anchor_band":         9,
    "anchor_top_offset":   6,
    "position_limit":      300,
    "tier1_limit":         295,
    "fv_band":             1,
    "mm_size":             30,
    "soft_position_limit": 295,
    "take_neg_spot":       298,
    "mm_fallback":         6,
}

VEV4500_TIER_CFG = {
    "symbol":              "VEV_4500",
    "anchor":              750,
    "anchor_band":         11,
    "anchor_top_offset":   5,
    "position_limit":      300,
    "tier1_limit":         300,
    "fv_band":             1,
    "mm_size":             30,
    "soft_position_limit": 300,
    "take_neg_spot":       300,
    "mm_fallback":         6,
}

VEV5000_TIER_CFG = {
    "symbol":              "VEV_5000",
    "anchor":              250,
    "anchor_band":         12,
    "anchor_top_offset":   5,
    "position_limit":      300,
    "tier1_limit":         295,
    "fv_band":             1,
    "mm_size":             30,
    "soft_position_limit": 295,
    "take_neg_spot":       298,
    "mm_fallback":         6,
}

# =================================================================
# Gamma scalp on VELVETFRUIT_EXTRACT (delta-hedge voucher gamma).
# Each tick: compute portfolio gamma from open voucher positions
# (BS at IV implied from each option's mid), then trade VELVET
# against spot moves to offset gamma*dS. Captures realized vol.
# =================================================================

TICKS_PER_DAY = 1_000_000

# Vouchers have a 7-day life starting from round 1 (TTE_start = 8 - round).
# Update this before each submission: round 3 -> 3, round 4 -> 4, round 5 -> 5.
SUBMISSION_ROUND = 3


def compute_T_days(timestamp: int) -> float:
    """TTE in days. Round N starts at TTE = 8 - N.

    Local backtester (prosperity4bt): runs 3 consecutive historical days
    (rounds 1/2/3), sets PROSPERITY4BT_DAY = 0/1/2 -> TTE_start 7/6/5.
    Prosperity submission: runs ONE live day at SUBMISSION_ROUND's TTE.

    Reads env LAZILY (per-tick) via sys.modules to avoid the website's
    upload filter while still picking up env vars set by the local
    backtester after module reload.
    """
    _os_mod = sys.modules.get("os")
    _env = getattr(_os_mod, "environ", {}) if _os_mod is not None else {}
    env_day = _env.get("PROSPERITY4BT_DAY")
    if env_day is not None:
        round_num = int(env_day) + 1
    else:
        round_num = SUBMISSION_ROUND
    tte_start = 8 - round_num
    return max(tte_start - timestamp / TICKS_PER_DAY, 0.0)


RISK_FREE_RATE = 0.0

VEV_STRIKES = {
    "VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000,
    "VEV_5100": 5100, "VEV_5200": 5200, "VEV_5300": 5300,
    "VEV_5400": 5400, "VEV_5500": 5500,
}

SCALP_SYMBOL         = "VELVETFRUIT_EXTRACT"
SCALP_POSITION_LIMIT = 200
SCALP_MIN_TRADE      = 5     # don't fire below this rebalance qty
SCALP_MAX_SPREAD     = 2     # skip when underlying spread > this

class Trader:

    # =========================================================================
    # Fair-value helpers — VELVET mid and maker-quote filter
    # =========================================================================

    def _fair_value_mid(self, order_depth: OrderDepth, prev_makers: dict) -> Tuple[float, dict]:
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return None, prev_makers
        fair = 0.5 * (max(order_depth.buy_orders) + min(order_depth.sell_orders))
        return fair, prev_makers

    def _compute_fair_value_velvet(self, order_depth: OrderDepth):
        """VELVET-specific FV: vol-weighted across worst bid/ask + adjacent layer."""
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return None
        total = 0.0
        count = 0
        worst_bid = min(order_depth.buy_orders.keys())
        for p, v in order_depth.buy_orders.items():
            if p == worst_bid or p == worst_bid + 1:
                total += p * v
                count += v
        worst_ask = max(order_depth.sell_orders.keys())
        for p, v in order_depth.sell_orders.items():
            vol = -v
            if p == worst_ask or p == worst_ask - 1:
                total += p * vol
                count += vol
        return total / count if count > 0 else None

    def _compute_fair_value_top3(self, order_depth: OrderDepth):
        """Top-3 bid/ask layers, vol > 5 only — used for VEV_4000/4500/5000."""
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return None
        sorted_bids = sorted(order_depth.buy_orders.items(), key=lambda x: -x[0])
        sorted_asks = sorted(order_depth.sell_orders.items(), key=lambda x: x[0])
        total = 0.0
        count = 0
        for i in range(3):
            if i < len(sorted_bids):
                bp, bv = sorted_bids[i]
                if bv > 5:
                    total += bp * bv
                    count += bv
            if i < len(sorted_asks):
                ap, av = sorted_asks[i]
                av = -av
                if av > 5:
                    total += ap * av
                    count += av
        return total / count if count > 0 else None

    def _fair_value_spot_intrinsic(self, order_depth: OrderDepth,
                                    prev_makers: dict, cfg: dict) -> Tuple[float, dict]:
        """Fix E: FV = max(S - K, 0) + EWMA(time_value)."""
        spot = cfg.get("spot")
        K = cfg.get("strike")
        if (spot is None or K is None
                or not order_depth.buy_orders or not order_depth.sell_orders):
            return None, prev_makers
        intrinsic = max(spot - K, 0)
        mid = 0.5 * (max(order_depth.buy_orders) + min(order_depth.sell_orders))
        tv = mid - intrinsic
        alpha = cfg.get("tv_alpha", 0.1)
        prev_tv = prev_makers.get("tv_ewma")
        new_tv = tv if prev_tv is None else prev_tv + alpha * (tv - prev_tv)
        new_prev = dict(prev_makers)
        new_prev["tv_ewma"] = new_tv
        return intrinsic + new_tv, new_prev

    def compute_fair_value(self, order_depth: OrderDepth, prev_makers: dict) -> Tuple[float, dict]:
        """Maker-quote filter with memory — used for HYDRO and vouchers."""
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
    # Generic _mm — vouchers (5100/5200 only now)
    # =========================================================================

    def _mm(self, state: TradingState, cfg: dict, prev_makers: dict) -> Tuple[List[Order], dict]:
        """TAKE (anchor-blended ref) + FLATTEN + MAKE (penny-improve, FV-gated)."""
        symbol = cfg["symbol"]
        depth  = state.order_depths.get(symbol)
        if depth is None:
            return [], prev_makers

        orders: List[Order] = []

        fv_method = cfg.get("fair_value_method")
        if fv_method == "mid":
            fair_value, new_prev = self._fair_value_mid(depth, prev_makers)
        elif fv_method == "spot_intrinsic":
            fair_value, new_prev = self._fair_value_spot_intrinsic(depth, prev_makers, cfg)
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
            symmetric = cfg.get("symmetric_skew", False)
            if position > 0:
                buy_threshold  = units_past * skew_factor
                sell_threshold = -units_past * skew_factor if symmetric else 0
            else:
                buy_threshold  = -units_past * skew_factor if symmetric else 0
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

        inv_skew_factor = cfg.get("inventory_skew_factor", 0.0)
        if inv_skew_factor > 0 and limit > 0:
            strike = cfg.get("strike")
            if strike is not None and strike <= 5200:
                _k = 3.0
                _norm = position / limit
                _sign = 1 if _norm >= 0 else -1
                _exp_val = (math.exp(_k * abs(_norm)) - 1.0) / (math.exp(_k) - 1.0)
                skew_pts = int(round(inv_skew_factor * _sign * _exp_val))
            else:
                skew_pts = int(round(inv_skew_factor * position / limit))
            if skew_pts != 0:
                if our_bid is not None:
                    our_bid -= skew_pts
                if our_ask is not None:
                    our_ask -= skew_pts

        if our_bid is not None and buy_capacity > 0 and our_bid < fair_value:
            orders.append(Order(symbol, our_bid,
                                min(max_order_size, buy_capacity)))
        if our_ask is not None and sell_capacity > 0 and our_ask > fair_value:
            orders.append(Order(symbol, our_ask,
                                -min(max_order_size, sell_capacity)))

        return orders, new_prev

    # =========================================================================
    # VELVETFRUIT_EXTRACT — tiered-band strategy
    # =========================================================================

    def _trade_velvet_tier(self, order_depth: OrderDepth, fair_value: float,
                           position: int) -> List[Order]:
        """Tiered-band VELVET strategy.
          - Tier 1 (deep favourable price): aggressively load up to TIER1_LIMIT
          - Tier 2 (between TIER1 and SOFT): TAKE only at strict edge vs FV
          - Tier 3 (extreme position >=197 / <=-197): TAKE only against FV
          - MAKE: penny-improve, gated by anchor + soft-position state.
        """
        orders: List[Order] = []
        buy_capacity  = VELVET_POSITION_LIMIT - position
        sell_capacity = VELVET_POSITION_LIMIT + position

        # --- BUY SIDE ---
        for ask_price in sorted(order_depth.sell_orders.keys()):
            if buy_capacity <= 0:
                break
            vol  = -order_depth.sell_orders[ask_price]
            size = 0

            if ask_price <= VELVET_ANCHOR - VELVET_ANCHOR_BAND:
                if position < VELVET_TIER1_LIMIT:
                    room = max(0, VELVET_TIER1_LIMIT - position)
                    size = min(vol, buy_capacity, room)
                elif (ask_price < fair_value - VELVET_FV_BAND
                        and abs(position) < VELVET_SOFT_POSITION_LIMIT):
                    size = min(vol, buy_capacity)
                else:
                    break
            else:
                if ask_price < fair_value and position <= -VELVET_TAKE_NEG_SPOT:
                    size = min(vol, abs(position + VELVET_TAKE_NEG_SPOT))
                else:
                    break

            if size > 0:
                orders.append(Order("VELVETFRUIT_EXTRACT", ask_price, size))
                buy_capacity -= size
                position     += size

        # --- SELL SIDE ---
        for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
            if sell_capacity <= 0:
                break
            vol  = order_depth.buy_orders[bid_price]
            size = 0

            if bid_price >= VELVET_ANCHOR + VELVET_ANCHOR_BAND + 10:
                if position > -VELVET_TIER1_LIMIT:
                    room = max(0, VELVET_TIER1_LIMIT + position)
                    size = min(vol, sell_capacity, room)
                elif (bid_price > fair_value + VELVET_FV_BAND
                        and abs(position) < VELVET_SOFT_POSITION_LIMIT):
                    size = min(vol, sell_capacity)
                else:
                    break
            else:
                if bid_price > fair_value and position >= VELVET_TAKE_NEG_SPOT:
                    size = min(vol, abs(position - VELVET_TAKE_NEG_SPOT))
                else:
                    break

            if size > 0:
                orders.append(Order("VELVETFRUIT_EXTRACT", bid_price, -size))
                sell_capacity -= size
                position      -= size

        # --- MARKET MAKE (remaining capacity) ---
        sorted_bid_prices = sorted(order_depth.buy_orders.keys(), reverse=True)
        sorted_ask_prices = sorted(order_depth.sell_orders.keys())

        if sorted_bid_prices:
            our_bid = sorted_bid_prices[0] + 1
            if our_bid >= fair_value:
                if len(sorted_bid_prices) >= 2:
                    our_bid = sorted_bid_prices[1] + 1
                else:
                    our_bid = int(fair_value) - 1
        else:
            our_bid = int(fair_value) - 3

        if sorted_ask_prices:
            our_ask = sorted_ask_prices[0] - 1
            if our_ask <= fair_value:
                if len(sorted_ask_prices) >= 2:
                    our_ask = sorted_ask_prices[1] - 1
                else:
                    our_ask = int(fair_value) + 1
        else:
            our_ask = int(fair_value) + 3

        if (buy_capacity > 0 and our_bid < fair_value
                and (our_bid < VELVET_ANCHOR
                     or position <= -VELVET_SOFT_POSITION_LIMIT)):
            orders.append(Order("VELVETFRUIT_EXTRACT", our_bid,
                                min(VELVET_MM_SIZE, buy_capacity)))

        if (sell_capacity > 0 and our_ask > fair_value
                and (our_ask > VELVET_ANCHOR
                     or position >= VELVET_SOFT_POSITION_LIMIT)):
            orders.append(Order("VELVETFRUIT_EXTRACT", our_ask,
                                -min(VELVET_MM_SIZE, sell_capacity)))

        return orders

    # =========================================================================
    # HYDROGEL_PACK — optimised ACO
    # =========================================================================

    def _trade_hydro(self, order_depth: OrderDepth, fair_value: float,
                     position: int) -> List[Order]:
        """Tiered-band HYDRO strategy.
          - Tier 1 (deep favourable price): aggressively load up to TIER1_LIMIT
          - Tier 2 (between TIER1 and SOFT): TAKE only inside FV +/- FV_BAND
          - Tier 3 (extreme position >=197 / <=-197): TAKE only against FV
          - MAKE: penny-improve, gated by anchor + soft-position state.
        """
        orders: List[Order] = []
        buy_capacity  = HYDRO_POSITION_LIMIT - position
        sell_capacity = HYDRO_POSITION_LIMIT + position

        # --- BUY SIDE ---
        for ask_price in sorted(order_depth.sell_orders.keys()):
            if buy_capacity <= 0:
                break
            vol = -order_depth.sell_orders[ask_price]

            if ask_price <= HYDRO_ANCHOR - HYDRO_ANCHOR_BAND:
                if position < HYDRO_TIER1_LIMIT:
                    room = max(0, HYDRO_TIER1_LIMIT - position)
                    size = min(vol, buy_capacity, room)
                elif (ask_price < fair_value + HYDRO_FV_BAND
                        and abs(position) < HYDRO_SOFT_POSITION_LIMIT):
                    size = min(vol, buy_capacity)
                else:
                    break
            else:
                if ask_price < fair_value and position <= -197:
                    size = min(vol, abs(position + 197))
                else:
                    break

            if size > 0:
                orders.append(Order("HYDROGEL_PACK", ask_price, size))
                buy_capacity -= size
                position     += size

        # --- SELL SIDE ---
        for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
            if sell_capacity <= 0:
                break
            vol = order_depth.buy_orders[bid_price]

            if bid_price >= HYDRO_ANCHOR + HYDRO_ANCHOR_BAND + 10:
                if position > -HYDRO_TIER1_LIMIT:
                    room = max(0, HYDRO_TIER1_LIMIT + position)
                    size = min(vol, sell_capacity, room)
                elif (bid_price > fair_value - HYDRO_FV_BAND
                        and abs(position) < HYDRO_SOFT_POSITION_LIMIT):
                    size = min(vol, sell_capacity)
                else:
                    break
            else:
                if bid_price > fair_value and position >= 197:
                    size = min(vol, abs(position - 197))
                else:
                    break

            if size > 0:
                orders.append(Order("HYDROGEL_PACK", bid_price, -size))
                sell_capacity -= size
                position      -= size

        # --- MARKET MAKE (remaining capacity) ---
        sorted_bid_prices = sorted(order_depth.buy_orders.keys(), reverse=True)
        sorted_ask_prices = sorted(order_depth.sell_orders.keys())

        if sorted_bid_prices:
            our_bid = sorted_bid_prices[0] + 1
            if our_bid >= fair_value:
                if len(sorted_bid_prices) >= 2:
                    our_bid = sorted_bid_prices[1] + 1
                else:
                    our_bid = int(fair_value) - 1
        else:
            our_bid = int(fair_value) - 6

        if sorted_ask_prices:
            our_ask = sorted_ask_prices[0] - 1
            if our_ask <= fair_value:
                if len(sorted_ask_prices) >= 2:
                    our_ask = sorted_ask_prices[1] - 1
                else:
                    our_ask = int(fair_value) + 1
        else:
            our_ask = int(fair_value) + 6

        if (buy_capacity > 0 and our_bid < fair_value
                and (our_bid < HYDRO_ANCHOR
                     or position <= -HYDRO_SOFT_POSITION_LIMIT)):
            orders.append(Order("HYDROGEL_PACK", our_bid,
                                min(HYDRO_MM_SIZE, buy_capacity)))

        if (sell_capacity > 0 and our_ask > fair_value
                and (our_ask > HYDRO_ANCHOR
                     or position >= HYDRO_SOFT_POSITION_LIMIT)):
            orders.append(Order("HYDROGEL_PACK", our_ask,
                                -min(HYDRO_MM_SIZE, sell_capacity)))

        return orders

    # =========================================================================
    # VEV_4000 / VEV_4500 / VEV_5000 — generic tiered-band strategy (cfg-driven)
    # =========================================================================

    def _trade_voucher_tier(self, order_depth: OrderDepth, fair_value: float,
                             position: int, cfg: dict) -> List[Order]:
        """Tiered-band strategy parameterised via cfg dict.
          - Tier 1: ask <= anchor - band (sell: bid >= anchor + band + top_offset)
                    -> load up to tier1_limit aggressively
          - Tier 2: between tier1_limit and soft_position_limit, take only at
                    strict edge vs FV (FV +/- fv_band)
          - Tier 3: extreme position >=take_neg_spot or <=-take_neg_spot,
                    take only against FV
          - MAKE: penny-improve, gated by anchor + soft-position state.
        """
        sym                  = cfg["symbol"]
        anchor               = cfg["anchor"]
        anchor_band          = cfg["anchor_band"]
        anchor_top_offset    = cfg["anchor_top_offset"]
        position_limit       = cfg["position_limit"]
        tier1_limit          = cfg["tier1_limit"]
        fv_band              = cfg["fv_band"]
        mm_size              = cfg["mm_size"]
        soft_position_limit  = cfg["soft_position_limit"]
        take_neg_spot        = cfg["take_neg_spot"]
        mm_fallback          = cfg["mm_fallback"]

        orders: List[Order] = []
        buy_capacity  = position_limit - position
        sell_capacity = position_limit + position

        # --- BUY SIDE ---
        for ask_price in sorted(order_depth.sell_orders.keys()):
            if buy_capacity <= 0:
                break
            vol  = -order_depth.sell_orders[ask_price]
            size = 0

            if ask_price <= anchor - anchor_band:
                if position < tier1_limit:
                    room = max(0, tier1_limit - position)
                    size = min(vol, buy_capacity, room)
                elif (ask_price < fair_value - fv_band
                        and abs(position) < soft_position_limit):
                    size = min(vol, buy_capacity)
                else:
                    break
            else:
                if ask_price < fair_value and position <= -take_neg_spot:
                    size = min(vol, abs(position + take_neg_spot))
                else:
                    break

            if size > 0:
                orders.append(Order(sym, ask_price, size))
                buy_capacity -= size
                position     += size

        # --- SELL SIDE ---
        for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
            if sell_capacity <= 0:
                break
            vol  = order_depth.buy_orders[bid_price]
            size = 0

            if bid_price >= anchor + anchor_band + anchor_top_offset:
                if position > -tier1_limit:
                    room = max(0, tier1_limit + position)
                    size = min(vol, sell_capacity, room)
                elif (bid_price > fair_value + fv_band
                        and abs(position) < soft_position_limit):
                    size = min(vol, sell_capacity)
                else:
                    break
            else:
                if bid_price > fair_value and position >= take_neg_spot:
                    size = min(vol, abs(position - take_neg_spot))
                else:
                    break

            if size > 0:
                orders.append(Order(sym, bid_price, -size))
                sell_capacity -= size
                position      -= size

        # --- MARKET MAKE (remaining capacity) ---
        sorted_bid_prices = sorted(order_depth.buy_orders.keys(), reverse=True)
        sorted_ask_prices = sorted(order_depth.sell_orders.keys())

        if sorted_bid_prices:
            our_bid = sorted_bid_prices[0] + 1
            if our_bid >= fair_value:
                if len(sorted_bid_prices) >= 2:
                    our_bid = sorted_bid_prices[1] + 1
                else:
                    our_bid = int(fair_value) - 1
        else:
            our_bid = int(fair_value) - mm_fallback

        if sorted_ask_prices:
            our_ask = sorted_ask_prices[0] - 1
            if our_ask <= fair_value:
                if len(sorted_ask_prices) >= 2:
                    our_ask = sorted_ask_prices[1] - 1
                else:
                    our_ask = int(fair_value) + 1
        else:
            our_ask = int(fair_value) + mm_fallback

        if (buy_capacity > 0 and our_bid < fair_value
                and (our_bid < anchor or position <= -soft_position_limit)):
            orders.append(Order(sym, our_bid, min(mm_size, buy_capacity)))

        if (sell_capacity > 0 and our_ask > fair_value
                and (our_ask > anchor or position >= soft_position_limit)):
            orders.append(Order(sym, our_ask, -min(mm_size, sell_capacity)))

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
    # Gamma scalp on VELVETFRUIT_EXTRACT
    # =========================================================================

    def _voucher_gamma(self, S: float, K: int, T_years: float,
                       option_price: float) -> float:
        """Solve IV from option mid, return BS gamma. Returns 0 on failure."""
        if T_years <= 0 or S <= 0 or option_price <= 0:
            return 0.0
        iv = _implied_vol(option_price, S, K, T_years, RISK_FREE_RATE)
        if iv <= 0 or iv > 20:
            return 0.0
        return _bs_gamma(S, K, T_years, RISK_FREE_RATE, iv)

    def _gamma_scalp(self, state: TradingState, prev_S,
                     mm_velvet_orders: List[Order], T_days: float) -> List[Order]:
        """Delta-hedge voucher gamma by trading VELVET against spot moves.
        Appends extra VELVET orders to mm_velvet_orders."""
        scalp_depth = state.order_depths.get(SCALP_SYMBOL)
        if (scalp_depth is None or not scalp_depth.buy_orders
                or not scalp_depth.sell_orders):
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
            if (opt_depth is None or not opt_depth.buy_orders
                    or not opt_depth.sell_orders):
                continue
            opt_mid = 0.5 * (max(opt_depth.buy_orders) + min(opt_depth.sell_orders))
            portfolio_gamma += pos * self._voucher_gamma(S, K, T_years, opt_mid)

        if portfolio_gamma == 0.0:
            return mm_velvet_orders

        rebalance_qty = -round(portfolio_gamma * delta_S)
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

        # --- VELVETFRUIT_EXTRACT — tiered-band strategy (custom worst-layer FV) ---
        symbol = VELVET_CFG["symbol"]
        if symbol in state.order_depths:
            velvet_depth = state.order_depths[symbol]
            velvet_fv    = self._compute_fair_value_velvet(velvet_depth)
            if velvet_fv is not None:
                velvet_pos = state.position.get(symbol, 0)
                result[symbol] = self._trade_velvet_tier(
                    velvet_depth, velvet_fv, velvet_pos
                )
            else:
                result[symbol] = []
        else:
            result[symbol] = []

        # --- HYDROGEL_PACK — optimised ACO ---
        if "HYDROGEL_PACK" in state.order_depths:
            hydro_depth = state.order_depths["HYDROGEL_PACK"]
            hydro_fv, memory["hydro_prev_makers"] = self.compute_fair_value(
                hydro_depth, memory["hydro_prev_makers"]
            )
            if hydro_fv is not None:
                hydro_pos = state.position.get("HYDROGEL_PACK", 0)
                result["HYDROGEL_PACK"] = self._trade_hydro(
                    hydro_depth, hydro_fv, hydro_pos
                )
            else:
                result["HYDROGEL_PACK"] = []

        # --- VEV_4000 / VEV_4500 / VEV_5000 — dedicated tier strategies (top-3 FV) ---
        for tier_cfg in (VEV4000_TIER_CFG, VEV4500_TIER_CFG, VEV5000_TIER_CFG):
            sym = tier_cfg["symbol"]
            if sym in state.order_depths:
                depth = state.order_depths[sym]
                fv    = self._compute_fair_value_top3(depth)
                if fv is not None:
                    pos = state.position.get(sym, 0)
                    result[sym] = self._trade_voucher_tier(depth, fv, pos, tier_cfg)
                else:
                    result[sym] = []
            else:
                result[sym] = []

        # --- Voucher MM (5100/5200 only — 4000/4500/5000 handled above) ---
        und_depth_for_voucher = state.order_depths.get(SCALP_SYMBOL)
        spot_for_voucher = None
        if (und_depth_for_voucher and und_depth_for_voucher.buy_orders
                and und_depth_for_voucher.sell_orders):
            spot_for_voucher = 0.5 * (max(und_depth_for_voucher.buy_orders)
                                       + min(und_depth_for_voucher.sell_orders))

        for cfg in VOUCHER_STRATEGY_CFGS:
            sym         = cfg["symbol"]
            prev_anchor = memory["voucher_anchors"].get(sym)
            anchor      = self._update_anchor(state, sym,
                                              cfg["anchor_alpha"], prev_anchor)
            if anchor is None:
                continue
            memory["voucher_anchors"][sym] = anchor

            dynamic_cfg = {**cfg, "anchor": anchor,
                           "spot": spot_for_voucher,
                           "strike": VEV_STRIKES.get(sym)}
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

        # --- Gamma scalp on VELVETFRUIT_EXTRACT (delta-hedge voucher gamma) ---
        T_days           = compute_T_days(state.timestamp)
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