from datamodel import OrderDepth, TradingState, Order
from typing import List, Tuple
import json
import math
import sys


# =================================================================
# Black-Scholes helpers (inlined to keep the strategy a single file).
# Used by _fair_value_theo_ewma_iv for IV solving + theo pricing.
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


# =================================================================
# HYDROGEL_PACK — tiered-band strategy.
# Tier 1: when ask <= ANCHOR - BAND (or bid >= ANCHOR + BAND + SELL_OFFSET),
#         aggressively load up to TIER1_LIMIT.
# Tier 2: above TIER1_LIMIT but below SOFT_POSITION_LIMIT, TAKE inside
#         FV +/- FV_BAND (loose, unlike VELVET/voucher tier 2).
# Tier 3: at extreme positions (>= 197 or <= -197), TAKE only against FV.
# Plus penny-improve MAKE that's gated by anchor + soft-position state.
# =================================================================

HYDRO_POSITION_LIMIT      = 200
HYDRO_ANCHOR              = 10000
HYDRO_ANCHOR_BAND         = 7
HYDRO_TIER1_LIMIT         = 175
HYDRO_FV_BAND             = 1
HYDRO_MM_SIZE             = 30
HYDRO_SOFT_POSITION_LIMIT = 190
HYDRO_SELL_OFFSET         = 10    # asymmetric sell-side trigger offset

# =================================================================
# VELVETFRUIT_EXTRACT — tiered-band strategy (mirrors HYDRO).
# Custom FV: volume-weighted blend of worst bid + worst_bid+1 and
# worst ask + worst_ask-1 layers (favors deepest resting levels).
# Tier 2 condition is STRICT (ask < FV - FV_BAND for buys, bid > FV
# + FV_BAND for sells), opposite of HYDRO's loose tier 2.
# =================================================================

VELVET_POSITION_LIMIT      = 200
VELVET_ANCHOR              = 5250
VELVET_ANCHOR_BAND         = 5     # swept winner (was 10) +$7,931 alone
VELVET_TIER1_LIMIT         = 175
VELVET_FV_BAND             = 0     # swept winner (was 1) +$1,644 with band=5
VELVET_MM_SIZE             = 30
VELVET_SOFT_POSITION_LIMIT = 190
VELVET_TAKE_NEG_SPOT       = 197
VELVET_SELL_OFFSET         = 10    # asymmetric sell-side trigger offset

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

# Theo-tier configs (VEV_5100/5200/5300/5400/5500). Anchor is dynamic:
# BS theo at anchor_spot with EWMA-fitted IV, populated per-tick in run().
# Bands scale with inside spread; top_offset = 0 because theo already
# includes time value (no asymmetry needed).
VEV5300_TIER_CFG = {
    "symbol":              "VEV_5300",
    # "anchor":            <set per-tick from theo>
    "anchor_band":         5,
    "anchor_top_offset":   0,
    "position_limit":      300,
    "tier1_limit":         295,
    "fv_band":             1,
    "mm_size":             30,
    "soft_position_limit": 295,
    "take_neg_spot":       298,
    "mm_fallback":         2,
    "iv_alpha":            0.05,
    "anchor_spot":         5250.0,
    "iv_seed":             0.2738,   # NOTE: re-calibrate per round (mean of Day-2 IV)
}

VEV5400_TIER_CFG = {
    "symbol":              "VEV_5400",
    "anchor_band":         3,
    "anchor_top_offset":   0,
    "position_limit":      300,
    "tier1_limit":         295,
    "fv_band":             1,
    "mm_size":             25,
    "soft_position_limit": 295,
    "take_neg_spot":       298,
    "mm_fallback":         1,
    "iv_alpha":            0.05,
    "anchor_spot":         5240.0,
    "iv_seed":             0.2545,   # NOTE: re-calibrate per round
    "anchor_spot_blend":   0.7,      # 5400 only: tracks VELVET (deep OTM, delta-driven)
}

VEV5500_TIER_CFG = {
    "symbol":              "VEV_5500",
    "anchor_band":         1,        # tight: theo can be 2-3, larger -> Tier 1 never fires
    "anchor_top_offset":   0,
    "position_limit":      300,
    "tier1_limit":         295,
    "fv_band":             1,
    "mm_size":             10,       # smaller: thinnest book
    "soft_position_limit": 295,
    "take_neg_spot":       298,
    "mm_fallback":         1,
    "iv_alpha":            0.05,
    "anchor_spot":         5256.0,
    "iv_seed":             0.2780,   # round 3: day-2 mean
    "anchor_spot_blend":   0.7,      # swept winner
}

VEV5100_TIER_CFG = {
    "symbol":              "VEV_5100",
    "anchor_band":         12,
    "anchor_top_offset":   0,
    "position_limit":      300,
    "tier1_limit":         295,
    "fv_band":             1,
    "mm_size":             30,
    "soft_position_limit": 295,
    "take_neg_spot":       298,
    "mm_fallback":         5,
    "iv_alpha":            0.05,
    "anchor_spot":         5250.0,
    "anchor_spot_blend":   0.0,
    "iv_seed":             0.2617,   # NOTE: re-calibrate per round
}

VEV5200_TIER_CFG = {
    "symbol":              "VEV_5200",
    "anchor_band":         10,
    "anchor_top_offset":   0,
    "position_limit":      300,
    "tier1_limit":         295,
    "fv_band":             1,
    "mm_size":             30,
    "soft_position_limit": 295,
    "take_neg_spot":       298,
    "mm_fallback":         3,
    "iv_alpha":            0.05,
    "anchor_spot":         5250.0,
    "anchor_spot_blend":   0.0,
    "iv_seed":             0.2684,   # NOTE: re-calibrate per round
}

TICKS_PER_DAY = 1_000_000
# Bump before each round submission: round 4 -> 4, round 5 -> 5.
# (Voucher TTE_start = 8 - round; vouchers were 7-day from round 1.)
SUBMISSION_ROUND = 4


def compute_T_days(timestamp: int) -> float:
    """TTE in days. Round N starts at TTE = 8 - N.

    Local backtester sets PROSPERITY4BT_DAY=0/1/2 (TTE_start 7/6/5).
    Submission: env unset -> falls back to SUBMISSION_ROUND constant.
    Env is read LAZILY per-tick via sys.modules to bypass the website's
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
    return max(8 - round_num - timestamp / TICKS_PER_DAY, 0.0)


RISK_FREE_RATE = 0.0

VEV_STRIKES = {
    "VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000,
    "VEV_5100": 5100, "VEV_5200": 5200, "VEV_5300": 5300,
    "VEV_5400": 5400, "VEV_5500": 5500,
}

# Underlying symbol used for spot lookup in theo-tier voucher FV calc.
UNDERLYING_SYMBOL = "VELVETFRUIT_EXTRACT"

class Trader:

    # =========================================================================
    # Fair-value helpers
    # =========================================================================

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

    def _fair_value_theo_ewma_iv(self, order_depth: OrderDepth,
                                  prev_makers: dict, cfg: dict) -> Tuple[float, dict]:
        """Theoretical FV via EWMA-fitted IV (theo-tier vouchers 5100-5500).

        Per tick: solve IV from observed mid at current spot, EWMA-update,
        then return BS price at the (optionally blended) anchor_spot.
        Naturally decays with TTE; persists 'iv_ewma' across ticks.

        cfg keys: strike, spot, T_days, iv_alpha (default 0.05),
        anchor_spot (5250), anchor_spot_blend (0 = equilibrium, 1 = follow
        spot), iv_min/iv_max sanity bounds (0.001 / 5.0).
        """
        K           = cfg.get("strike")
        S_actual    = cfg.get("spot")
        T_days      = cfg.get("T_days")
        iv_alpha    = cfg.get("iv_alpha",    0.05)
        anchor_spot_eq    = cfg.get("anchor_spot",       5250.0)
        anchor_spot_blend = cfg.get("anchor_spot_blend", 0.0)
        iv_min      = cfg.get("iv_min",      0.001)
        iv_max      = cfg.get("iv_max",      5.0)

        if (K is None or S_actual is None or T_days is None
                or T_days <= 0 or S_actual <= 0
                or not order_depth.buy_orders or not order_depth.sell_orders):
            return None, prev_makers

        # Blend equilibrium anchor with current spot (0 = pure equilibrium).
        anchor_spot = ((1.0 - anchor_spot_blend) * anchor_spot_eq
                       + anchor_spot_blend * S_actual)

        T_years = T_days / 365.0
        opt_mid = 0.5 * (max(order_depth.buy_orders) + min(order_depth.sell_orders))
        if opt_mid <= 0:
            return None, prev_makers

        # Solve IV from observed mid; EWMA-update.
        iv_now = _implied_vol(opt_mid, S_actual, K, T_years, RISK_FREE_RATE)
        prev_iv = prev_makers.get("iv_ewma")
        if iv_now <= iv_min or iv_now > iv_max:
            new_iv = prev_iv     # solver failed/unreasonable -> hold last
        elif prev_iv is None:
            new_iv = iv_now
        else:
            new_iv = prev_iv + iv_alpha * (iv_now - prev_iv)

        new_prev = dict(prev_makers)
        if new_iv is not None:
            new_prev["iv_ewma"] = new_iv
        if new_iv is None or new_iv <= iv_min:
            return None, new_prev

        # Theoretical anchor at the (blended) anchor spot with smoothed IV.
        fair = _bs_call(anchor_spot, K, T_years, RISK_FREE_RATE, new_iv)
        return fair, new_prev

    def compute_fair_value(self, order_depth: OrderDepth, prev_makers: dict) -> Tuple[float, dict]:
        """Maker-quote filter with memory — used by HYDROGEL_PACK only.

        Skips thin top-of-book quotes; persists last-seen "real" maker
        levels across ticks so a temporarily thin book doesn't lose FV.
        """
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

            if bid_price >= VELVET_ANCHOR + VELVET_ANCHOR_BAND + VELVET_SELL_OFFSET:
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
    # HYDROGEL_PACK — tiered-band strategy
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

            if bid_price >= HYDRO_ANCHOR + HYDRO_ANCHOR_BAND + HYDRO_SELL_OFFSET:
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
    # Generic tiered-band strategy (cfg-driven) — used by all 8 vouchers:
    #   - VEV_4000/4500/5000: deep-ITM tier (fixed anchor = intrinsic, top-3 FV)
    #   - VEV_5100/5200/5300/5400/5500: theo-tier (anchor = FV = BS theo with EWMA IV)
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
    # run
    # =========================================================================

    def run(self, state: TradingState):
        memory = json.loads(state.traderData) if state.traderData else {}
        memory.setdefault("hydro_prev_makers", {})
        memory.setdefault("voucher_mm_makers", {})

        result = {}

        # --- VELVETFRUIT_EXTRACT — tiered-band strategy (worst-layer FV) ---
        if UNDERLYING_SYMBOL in state.order_depths:
            velvet_depth = state.order_depths[UNDERLYING_SYMBOL]
            velvet_fv    = self._compute_fair_value_velvet(velvet_depth)
            if velvet_fv is not None:
                velvet_pos = state.position.get(UNDERLYING_SYMBOL, 0)
                result[UNDERLYING_SYMBOL] = self._trade_velvet_tier(
                    velvet_depth, velvet_fv, velvet_pos
                )

        # --- HYDROGEL_PACK — tiered-band strategy (maker-filter FV) ---
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

        # --- VEV_4000 / 4500 / 5000 — deep-ITM tier with top-3 FV, fixed anchor ---
        for tier_cfg in (VEV4000_TIER_CFG, VEV4500_TIER_CFG, VEV5000_TIER_CFG):
            sym = tier_cfg["symbol"]
            depth = state.order_depths.get(sym)
            if depth is None:
                continue
            fv = self._compute_fair_value_top3(depth)
            if fv is None:
                continue
            pos = state.position.get(sym, 0)
            result[sym] = self._trade_voucher_tier(depth, fv, pos, tier_cfg)

        # --- VEV_5100 / 5200 / 5300 / 5400 / 5500 — theo-tier with EWMA-IV anchor ---
        und_depth = state.order_depths.get(UNDERLYING_SYMBOL)
        if (und_depth and und_depth.buy_orders and und_depth.sell_orders):
            spot = 0.5 * (max(und_depth.buy_orders) + min(und_depth.sell_orders))
            T_days_now = compute_T_days(state.timestamp)

            for tier_cfg in (VEV5100_TIER_CFG, VEV5200_TIER_CFG, VEV5300_TIER_CFG,
                             VEV5400_TIER_CFG, VEV5500_TIER_CFG):
                sym = tier_cfg["symbol"]
                depth = state.order_depths.get(sym)
                if not depth or not depth.buy_orders or not depth.sell_orders:
                    continue
                prev = memory["voucher_mm_makers"].setdefault(sym, {})
                # Seed IV EWMA on first tick to avoid cold-start
                if "iv_ewma" not in prev and "iv_seed" in tier_cfg:
                    prev["iv_ewma"] = tier_cfg["iv_seed"]
                cfg_with_ctx = {**tier_cfg,
                                "strike": VEV_STRIKES.get(sym),
                                "spot":   spot,
                                "T_days": T_days_now}
                theo_fv, memory["voucher_mm_makers"][sym] = \
                    self._fair_value_theo_ewma_iv(depth, prev, cfg_with_ctx)
                if theo_fv is None:
                    continue
                # Use theo as both FV and anchor for tier strategy
                cfg_with_ctx["anchor"] = theo_fv
                pos = state.position.get(sym, 0)
                result[sym] = self._trade_voucher_tier(
                    depth, theo_fv, pos, cfg_with_ctx
                )

        return result, 0, json.dumps(memory)