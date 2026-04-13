"""
Informed Trader (Olivia) Detector — detect informed trading from behavioral patterns.

Two modes:
1. With trader IDs (last round): profile each trader by hit rate and volume consistency.
2. Without trader IDs (rounds 1-4): profile by trade quantity — look for a consistent
   lot size that keeps appearing at price extremes.

The key insight: an informed trader is identified NOT by volume size, but by
CONSISTENCY — same quantity, repeatedly buying at lows and selling at highs.

Stdlib-only (math, json).

Usage in Trader.run():
    from informed_trader import OliviaDetector

    detector = OliviaDetector()
    detector.update(mid_price, state.market_trades.get(symbol, []))

    suspects = detector.get_suspects()
    signal = detector.get_signal()  # "buy", "sell", or None

    # Persist
    trader_data["olivia"] = detector.get_state()
    detector = OliviaDetector.from_state(trader_data["olivia"])
"""

import math
from typing import Any, Dict, List, Optional, Set, Tuple

from datamodel import Trade


class OliviaDetector:
    """
    Detect informed trading from behavioral patterns.

    Approach:
    1. Track a rolling price window → compute running min/max
    2. Every trade near a min or max is recorded as an "event"
    3. Profile each trader ID (if available) or each trade quantity:
       - What % of their trades are at extremes? (hit rate)
       - How consistent is their trade size? (low variance = suspicious)
    4. Flag traders/quantities that have high hit rate + consistent sizing
    """

    def __init__(self, window_size: int = 100, proximity_pct: float = 0.02,
                 min_observations: int = 3):
        """
        Args:
            window_size: number of ticks for rolling min/max price window
            proximity_pct: fraction of price range that counts as "near" an extreme
            min_observations: minimum trades before a pattern is considered reliable
        """
        self.window_size = window_size
        self.proximity_pct = proximity_pct
        self.min_observations = min_observations

        # Rolling price buffer
        self.price_buffer: List[float] = []

        # --- Profile by trader ID (when available) ---
        # {trader_id: {"buys_at_low": N, "sells_at_high": N, "total_trades": N, "quantities": [q1, q2, ...]}}
        self.trader_profiles: Dict[str, Dict[str, Any]] = {}

        # --- Profile by quantity (when IDs are not available) ---
        # {quantity: {"buys_at_low": N, "sells_at_high": N, "total_seen": N}}
        self.qty_profiles: Dict[int, Dict[str, int]] = {}

        # Last tick's flagged trades: list of (trade, direction, trader_id)
        # direction is "buys_at_low" or "sells_at_high"
        self._last_flags: List[Tuple[Trade, str, str]] = []

        # Last detected signal
        self._signal: Optional[str] = None

    def update(self, mid_price: float, trades: List[Trade]) -> None:
        """
        Call every tick with current mid-price and market trades.

        Args:
            mid_price: current mid-price for this product
            trades: list of Trade objects from state.market_trades[symbol]
        """
        # Update price buffer
        self.price_buffer.append(mid_price)
        if len(self.price_buffer) > self.window_size:
            self.price_buffer.pop(0)

        self._last_flags = []
        self._signal = None

        # Need enough history
        if len(self.price_buffer) < 10:
            return

        running_min = min(self.price_buffer)
        running_max = max(self.price_buffer)
        price_range = running_max - running_min

        if price_range <= 0:
            return

        proximity_band = self.proximity_pct * price_range

        for trade in trades:
            qty = abs(trade.quantity)
            buyer = trade.buyer or ""
            seller = trade.seller or ""

            dist_to_min = trade.price - running_min
            dist_to_max = running_max - trade.price

            # Proximity score: linear, 1.0 at the extreme, 0.0 at edge of band
            at_low = dist_to_min <= proximity_band
            at_high = dist_to_max <= proximity_band

            if not at_low and not at_high:
                # Trade is in the middle — still count it for total_trades
                self._record_normal_trade(buyer, seller, qty)
                continue

            # --- Trade is near an extreme ---
            if at_low:
                self._last_flags.append((trade, "buys_at_low", buyer))
                self._record_extreme(buyer, qty, "buys_at_low")
            else:
                self._last_flags.append((trade, "sells_at_high", seller))
                self._record_extreme(seller, qty, "sells_at_high")

        # Update signal based on current suspects
        self._update_signal()

    def _record_normal_trade(self, buyer: str, seller: str, qty: int) -> None:
        """Record a non-extreme trade to track total trade count per trader."""
        for trader_id in (buyer, seller):
            if trader_id and trader_id != "SUBMISSION":
                if trader_id not in self.trader_profiles:
                    self.trader_profiles[trader_id] = {
                        "buys_at_low": 0, "sells_at_high": 0,
                        "total_trades": 0, "quantities": [],
                    }
                self.trader_profiles[trader_id]["total_trades"] += 1

    def _record_extreme(self, trader_id: str, qty: int, direction: str) -> None:
        """Record a trade at an extreme (low or high)."""
        # Profile by trader ID
        if trader_id and trader_id != "SUBMISSION":
            if trader_id not in self.trader_profiles:
                self.trader_profiles[trader_id] = {
                    "buys_at_low": 0, "sells_at_high": 0,
                    "total_trades": 0, "quantities": [],
                }
            self.trader_profiles[trader_id][direction] += 1
            self.trader_profiles[trader_id]["total_trades"] += 1
            self.trader_profiles[trader_id]["quantities"].append(qty)

        # Profile by quantity (always, regardless of ID availability)
        if qty not in self.qty_profiles:
            self.qty_profiles[qty] = {"buys_at_low": 0, "sells_at_high": 0, "total_seen": 0}
        self.qty_profiles[qty][direction] += 1
        self.qty_profiles[qty]["total_seen"] += 1

    def _is_suspect_trader(self, trader_id: str) -> bool:
        """Check if a trader ID matches a known suspect pattern."""
        if not trader_id or trader_id == "SUBMISSION":
            return False
        profile = self.trader_profiles.get(trader_id)
        if profile is None:
            return False
        extreme_count = profile["buys_at_low"] + profile["sells_at_high"]
        if extreme_count < self.min_observations:
            return False
        hit_rate = extreme_count / profile["total_trades"] if profile["total_trades"] > 0 else 0
        if hit_rate < 0.5:
            return False
        qtys = profile["quantities"]
        if len(qtys) >= 2 and _qty_is_consistent(qtys):
            return True
        return False

    def _is_suspect_quantity(self, qty: int) -> bool:
        """Check if a trade quantity matches a known suspect lot size."""
        profile = self.qty_profiles.get(qty)
        if profile is None:
            return False
        extreme_count = profile["buys_at_low"] + profile["sells_at_high"]
        return extreme_count >= self.min_observations

    def _update_signal(self) -> None:
        """
        Determine signal from THIS TICK's flagged trades, but only if they
        match a suspect pattern built over time.

        Logic:
        1. For each flagged trade this tick, check if the trader ID is suspect
        2. If no trader ID match, check if the trade quantity is suspect
        3. Signal = direction of the first matched trade
        """
        if not self._last_flags:
            return

        for trade, direction, trader_id in self._last_flags:
            qty = abs(trade.quantity)

            # Priority 1: trader ID matches a suspect profile
            if self._is_suspect_trader(trader_id):
                self._signal = "buy" if direction == "buys_at_low" else "sell"
                return

            # Priority 2: quantity matches a suspect lot size
            if self._is_suspect_quantity(qty):
                self._signal = "buy" if direction == "buys_at_low" else "sell"
                return

    def get_signal(self) -> Optional[str]:
        """
        Returns the current informed trading signal.

        "buy"  — informed buying detected at lows → price likely going up
        "sell" — informed selling detected at highs → price likely going down
        None   — no clear informed pattern detected
        """
        return self._signal

    def get_suspects(self, min_hit_rate: float = 0.5) -> Dict[str, Dict[str, Any]]:
        """
        Get trader IDs suspected of being informed.

        Returns dict of {trader_id: {
            "hit_rate": float (fraction of trades at extremes),
            "buys_at_low": int,
            "sells_at_high": int,
            "total_trades": int,
            "qty_consistent": bool (do they always trade the same size?),
            "typical_qty": float (most common quantity),
        }}
        """
        suspects: Dict[str, Dict[str, Any]] = {}

        for trader_id, profile in self.trader_profiles.items():
            extreme_count = profile["buys_at_low"] + profile["sells_at_high"]
            if extreme_count < self.min_observations:
                continue

            total = profile["total_trades"]
            if total == 0:
                continue

            hit_rate = extreme_count / total
            if hit_rate < min_hit_rate:
                continue

            qtys = profile["quantities"]
            consistent = _qty_is_consistent(qtys) if len(qtys) >= 2 else False
            typical = _most_common(qtys) if qtys else 0

            suspects[trader_id] = {
                "hit_rate": round(hit_rate, 2),
                "buys_at_low": profile["buys_at_low"],
                "sells_at_high": profile["sells_at_high"],
                "total_trades": total,
                "qty_consistent": consistent,
                "typical_qty": typical,
            }

        return suspects

    def get_suspect_quantities(self) -> Dict[int, Dict[str, int]]:
        """
        Get trade quantities that suspiciously cluster at extremes.
        Useful when trader IDs are not available (rounds 1-4).

        Returns dict of {quantity: {"buys_at_low": N, "sells_at_high": N, "total_seen": N}}
        Only includes quantities with at least min_observations events.
        """
        return {
            qty: dict(profile)
            for qty, profile in self.qty_profiles.items()
            if (profile["buys_at_low"] + profile["sells_at_high"]) >= self.min_observations
        }

    def get_last_flags(self) -> List[Tuple[Trade, str, str]]:
        """
        Trades from the most recent tick that were near an extreme.
        Returns list of (trade, direction, trader_id) tuples.
        direction is "buys_at_low" or "sells_at_high".
        """
        return self._last_flags

    def get_state(self) -> Dict:
        """Serialize for JSON persistence via traderData."""
        return {
            "window_size": self.window_size,
            "proximity_pct": self.proximity_pct,
            "min_observations": self.min_observations,
            "price_buffer": self.price_buffer,
            "trader_profiles": self.trader_profiles,
            "qty_profiles": {str(k): v for k, v in self.qty_profiles.items()},
        }

    @classmethod
    def from_state(cls, state_dict: Dict) -> "OliviaDetector":
        """Reconstruct from serialized state."""
        detector = cls(
            window_size=state_dict.get("window_size", 100),
            proximity_pct=state_dict.get("proximity_pct", 0.02),
            min_observations=state_dict.get("min_observations", 3),
        )
        detector.price_buffer = state_dict.get("price_buffer", [])
        detector.trader_profiles = state_dict.get("trader_profiles", {})
        # JSON keys are strings, convert back to int
        raw_qty = state_dict.get("qty_profiles", {})
        detector.qty_profiles = {int(k): v for k, v in raw_qty.items()}
        return detector


# ═══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _qty_is_consistent(quantities: List[int], max_cv: float = 0.15) -> bool:
    """
    Check if a list of quantities has low variance (consistent sizing).

    Uses coefficient of variation (CV = std / mean).
    CV < 0.15 means quantities are within ~15% of the mean — very consistent.
    A bot always trading 15 would have CV = 0.0.
    A bot trading 13, 15, 14, 15, 16 would have CV ≈ 0.07.
    """
    if len(quantities) < 2:
        return False

    mean_q = sum(quantities) / len(quantities)
    if mean_q == 0:
        return False

    variance = sum((q - mean_q) ** 2 for q in quantities) / len(quantities)
    cv = math.sqrt(variance) / mean_q

    return cv <= max_cv


def _most_common(values: List[int]) -> int:
    """Return the most frequently occurring value."""
    counts: Dict[int, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=counts.get)


# ═══════════════════════════════════════════════════════════════════════════════
# Standalone helper functions
# ═══════════════════════════════════════════════════════════════════════════════

def detect_by_trader_id(trades: List[Trade], known_ids: Set[str]) -> List[Trade]:
    """
    For the last round when trader IDs are visible: filter trades from known informed traders.

    Args:
        trades: list of Trade objects
        known_ids: set of known informed trader IDs (e.g., {"Olivia"})

    Returns:
        List of trades where buyer or seller is in known_ids.
    """
    return [t for t in trades if t.buyer in known_ids or t.seller in known_ids]


def build_trade_profile(trades: List[Trade]) -> Dict[str, Dict[str, Any]]:
    """
    Build frequency analysis of all trader IDs from a list of trades.

    Returns dict of {trader_id: {
        "buy_count": int, "sell_count": int,
        "total_buy_qty": int, "total_sell_qty": int,
        "avg_buy_qty": float, "avg_sell_qty": float
    }}
    """
    profile: Dict[str, Dict[str, Any]] = {}

    def ensure_entry(trader_id: str) -> None:
        if trader_id not in profile:
            profile[trader_id] = {
                "buy_count": 0, "sell_count": 0,
                "total_buy_qty": 0, "total_sell_qty": 0,
                "avg_buy_qty": 0.0, "avg_sell_qty": 0.0,
            }

    for trade in trades:
        qty = abs(trade.quantity)

        if trade.buyer:
            ensure_entry(trade.buyer)
            profile[trade.buyer]["buy_count"] += 1
            profile[trade.buyer]["total_buy_qty"] += qty

        if trade.seller:
            ensure_entry(trade.seller)
            profile[trade.seller]["sell_count"] += 1
            profile[trade.seller]["total_sell_qty"] += qty

    for trader_id, data in profile.items():
        if data["buy_count"] > 0:
            data["avg_buy_qty"] = data["total_buy_qty"] / data["buy_count"]
        if data["sell_count"] > 0:
            data["avg_sell_qty"] = data["total_sell_qty"] / data["sell_count"]

    return profile
