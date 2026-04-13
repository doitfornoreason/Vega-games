"""
Signal Detection — Price indicators, statistical signals, and microstructure tools.

Stdlib-only (math, statistics, json). Provides both stateless functions and stateful class wrappers.

Price indicators: sma, bollinger_bands, rsi, z_score
Statistical signals: ema, rolling_autocorrelation, estimate_premium, dynamic_threshold
Microstructure: kyle_lambda, roll_model, high_low_vol
Stateful: EMATracker, CUSUMDetector

Usage in Trader.run():
    from signal_detection import sma, bollinger_bands, rsi, z_score
    from signal_detection import ema, estimate_premium, CUSUMDetector, EMATracker
    from signal_detection import kyle_lambda, roll_model
"""

import json
import math
import statistics
from typing import Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════════════
# Stateless functions
# ═══════════════════════════════════════════════════════════════════════════════

def ema(value: float, prev_ema: float, alpha: float) -> float:
    """
    Exponential moving average one-step update.

    EMA(t) = alpha * value + (1 - alpha) * prev_ema

    Args:
        value: new observation
        prev_ema: previous EMA value
        alpha: smoothing factor (0 < alpha <= 1). Higher = more responsive.
    """
    return alpha * value + (1.0 - alpha) * prev_ema


def rolling_autocorrelation(prices: List[float], lag: int = 1) -> Optional[float]:
    """
    Autocorrelation of price returns at a given lag.

    Positive = momentum (trending), Negative = mean-reversion.

    Args:
        prices: list of prices (at least lag + 2 elements)
        lag: the lag to compute autocorrelation at (default 1)

    Returns:
        Autocorrelation coefficient in [-1, +1], or None if insufficient data.
    """
    if len(prices) < lag + 2:
        return None

    # Compute returns
    returns = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

    n = len(returns)
    if n < lag + 1:
        return None

    # Mean of returns
    mean_r = sum(returns) / n

    # Autocovariance at lag
    numerator = 0.0
    denominator = 0.0
    for i in range(lag, n):
        numerator += (returns[i] - mean_r) * (returns[i - lag] - mean_r)
    for i in range(n):
        denominator += (returns[i] - mean_r) ** 2

    if denominator == 0:
        return 0.0

    return numerator / denominator


def sma(prices: List[float], window: int) -> Optional[float]:
    """
    Simple moving average of the last `window` prices.

    Args:
        prices: list of prices
        window: number of prices to average

    Returns:
        SMA value, or None if insufficient data.
    """
    if len(prices) < window:
        return None
    return sum(prices[-window:]) / window


def bollinger_bands(prices: List[float], window: int = 20,
                    num_std: float = 2.0) -> Optional[Tuple[float, float, float]]:
    """
    Bollinger Bands — mean ± num_std * standard deviation over a rolling window.

    Args:
        prices: list of prices
        window: lookback window for mean and std dev
        num_std: number of standard deviations for the bands (default 2.0)

    Returns:
        (lower, middle, upper) tuple, or None if insufficient data.
    """
    if len(prices) < window:
        return None
    recent = prices[-window:]
    middle = sum(recent) / window
    std = statistics.stdev(recent)
    return (middle - num_std * std, middle, middle + num_std * std)


def rsi(prices: List[float], window: int = 14) -> Optional[float]:
    """
    Relative Strength Index.

    RSI = 100 - 100 / (1 + RS), where RS = avg_gain / avg_loss over the window.

    For Prosperity's 100ms ticks, use window=50 to window=100 for meaningful readings
    (default 14 is calibrated for daily bars).

    Args:
        prices: list of prices (needs at least window + 1 elements)
        window: lookback period

    Returns:
        RSI value in [0, 100], or None if insufficient data.
    """
    if len(prices) < window + 1:
        return None

    # Compute changes over the last `window` periods
    changes = [prices[i] - prices[i - 1] for i in range(len(prices) - window, len(prices))]

    gains = [max(0.0, c) for c in changes]
    losses = [max(0.0, -c) for c in changes]

    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window

    if avg_loss == 0:
        return 100.0  # no losses → maximum RSI

    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def z_score(prices: List[float], window: int = 20) -> Optional[float]:
    """
    Z-score of the current price relative to a rolling window.

    z = (price - mean) / std

    Positive = price above mean (sell signal for mean-reversion).
    Negative = price below mean (buy signal for mean-reversion).
    |z| > 2 = strong signal (2 standard deviations from mean).

    Args:
        prices: list of prices
        window: lookback window

    Returns:
        Z-score, or None if insufficient data or zero variance.
    """
    if len(prices) < window:
        return None
    recent = prices[-window:]
    mean_p = sum(recent) / window
    std_p = statistics.stdev(recent)
    if std_p == 0:
        return None
    return (prices[-1] - mean_p) / std_p


def estimate_premium(basket_mid: float, constituent_mids: List[float],
                     weights: List[float]) -> float:
    """
    Estimate the basket premium for ETF/basket statistical arbitrage.

    premium = basket_mid - sum(w_i * constituent_mid_i)

    Frankfurt Hedgehogs R2 approach: when premium deviates from zero,
    trade the basket against its constituents expecting mean reversion.

    Args:
        basket_mid: mid-price of the basket/ETF product
        constituent_mids: list of mid-prices for each constituent
        weights: list of weights for each constituent

    Returns:
        Premium (positive = basket is overpriced vs constituents).
    """
    fair_value = sum(w * m for w, m in zip(weights, constituent_mids))
    return basket_mid - fair_value


def kyle_lambda(price_changes: List[float], signed_volumes: List[float]) -> Optional[float]:
    """
    Kyle's Lambda — price impact coefficient (AFML Ch19).

    Measures how much prices move per unit of signed volume.
    lambda = sum(dP * V) / sum(V^2)

    Higher lambda = less liquid = more price impact per trade.

    Args:
        price_changes: list of price changes (dP)
        signed_volumes: list of signed trade volumes (positive = buyer-initiated)

    Returns:
        Lambda coefficient, or None if insufficient data.
    """
    n = min(len(price_changes), len(signed_volumes))
    if n < 2:
        return None

    sum_dpv = sum(price_changes[i] * signed_volumes[i] for i in range(n))
    sum_v2 = sum(signed_volumes[i] ** 2 for i in range(n))

    if sum_v2 == 0:
        return None

    return sum_dpv / sum_v2


def roll_model(price_changes: List[float]) -> float:
    """
    Roll's model — estimate effective bid-ask spread from serial covariance (AFML Ch19).

    spread = 2 * sqrt(-cov(dP_t, dP_{t-1})) if autocovariance is negative, else 0.

    The intuition: in a pure market-making model, price changes bounce between
    bid and ask, creating negative serial correlation. The magnitude of this
    correlation reveals the half-spread.

    Args:
        price_changes: list of price changes

    Returns:
        Estimated effective spread (always >= 0).
    """
    n = len(price_changes)
    if n < 3:
        return 0.0

    # Autocovariance at lag 1
    mean_dp = sum(price_changes) / n
    cov = 0.0
    count = 0
    for i in range(1, n):
        cov += (price_changes[i] - mean_dp) * (price_changes[i - 1] - mean_dp)
        count += 1

    if count == 0:
        return 0.0

    cov /= count

    if cov >= 0:
        return 0.0

    return 2.0 * math.sqrt(-cov)


def high_low_vol(highs: List[float], lows: List[float],
                 window: int = 20) -> Optional[float]:
    """
    Parkinson volatility estimator using high-low range (AFML Ch19).

    sigma = sqrt(sum(ln(H/L)^2) / (4 * n * ln(2)))

    More efficient than close-to-close volatility (uses 5x less data for same accuracy).

    Args:
        highs: list of high prices per period
        lows: list of low prices per period
        window: number of periods to use (uses the most recent)

    Returns:
        Annualized volatility estimate, or None if insufficient data.
    """
    n = min(len(highs), len(lows), window)
    if n == 0:
        return None

    # Use the most recent `window` observations
    h = highs[-n:]
    l = lows[-n:]

    sum_sq = 0.0
    valid = 0
    for i in range(n):
        if l[i] > 0 and h[i] > 0:
            ratio = math.log(h[i] / l[i])
            sum_sq += ratio * ratio
            valid += 1

    if valid == 0:
        return None

    return math.sqrt(sum_sq / (4.0 * valid * math.log(2.0)))


def dynamic_threshold(volatility: float, base_threshold: float,
                      scaling: float = 1.0, base_vol: float = 0.01) -> float:
    """
    Adjust a trading threshold based on current volatility regime.

    threshold = base_threshold * (1 + scaling * (volatility / base_vol - 1))

    When volatility is high, widen thresholds to avoid overtrading.
    When volatility is low, tighten thresholds to capture smaller moves.

    Args:
        volatility: current volatility estimate
        base_threshold: the threshold at "normal" volatility
        scaling: how aggressively to adjust (0 = no adjustment, 1 = proportional)
        base_vol: the "normal" volatility level for calibration

    Returns:
        Adjusted threshold (always positive).
    """
    if base_vol <= 0:
        return base_threshold

    ratio = volatility / base_vol
    adjusted = base_threshold * (1.0 + scaling * (ratio - 1.0))
    return max(0.0, adjusted)


# ═══════════════════════════════════════════════════════════════════════════════
# Stateful class wrappers
# ═══════════════════════════════════════════════════════════════════════════════

class EMATracker:
    """
    Stateful exponential moving average tracker with serialization for traderData.

    Usage:
        tracker = EMATracker(alpha=0.1)
        new_val = tracker.update(mid_price)

        # Persist
        state = tracker.get_state()
        trader_data["ema"] = state

        # Restore
        tracker = EMATracker.from_state(state)
    """

    def __init__(self, alpha: float, initial_value: Optional[float] = None):
        self.alpha = alpha
        self.value = initial_value
        self.initialized = initial_value is not None

    def update(self, observation: float) -> float:
        """Update EMA with new observation. Returns the new EMA value."""
        if not self.initialized:
            self.value = observation
            self.initialized = True
        else:
            self.value = self.alpha * observation + (1.0 - self.alpha) * self.value
        return self.value

    def get_value(self) -> Optional[float]:
        """Get current EMA value. Returns None if never updated."""
        return self.value if self.initialized else None

    def get_state(self) -> Dict:
        """Serialize to dict for JSON persistence."""
        return {
            "alpha": self.alpha,
            "value": self.value,
            "initialized": self.initialized,
        }

    @classmethod
    def from_state(cls, state_dict: Dict) -> "EMATracker":
        """Reconstruct from serialized state."""
        tracker = cls(alpha=state_dict["alpha"])
        tracker.value = state_dict.get("value")
        tracker.initialized = state_dict.get("initialized", False)
        return tracker


class CUSUMDetector:
    """
    CUSUM change-point detector for regime shifts.

    Tracks cumulative sum of deviations from a running target mean.
    Signals when the cumulative sum exceeds a threshold, indicating a structural
    change in the mean.

    Usage:
        detector = CUSUMDetector(threshold=5.0, drift=0.5)
        signal = detector.update(mid_price)
        if signal != 0:
            print(f"Regime change detected: {'UP' if signal > 0 else 'DOWN'}")

        # Persist
        state = detector.get_state()

        # Restore
        detector = CUSUMDetector.from_state(state)
    """

    def __init__(self, threshold: float, drift: float = 0.0):
        """
        Args:
            threshold: detection threshold. Higher = fewer false alarms, slower detection.
            drift: allowable drift before accumulation starts. Acts as a filter for noise.
        """
        self.threshold = threshold
        self.drift = drift
        self.s_high = 0.0   # cumulative sum for upward shifts
        self.s_low = 0.0    # cumulative sum for downward shifts
        self.target = 0.0   # running mean (target to detect deviations from)
        self.n = 0           # number of observations seen
        self.sum_values = 0.0  # for computing running mean

    def update(self, value: float) -> int:
        """
        Feed a new observation.

        Returns:
            0: no change detected
            +1: upward shift detected (mean increased)
            -1: downward shift detected (mean decreased)
        """
        # Update running mean
        self.n += 1
        self.sum_values += value
        self.target = self.sum_values / self.n

        # Update CUSUM statistics
        self.s_high = max(0.0, self.s_high + value - self.target - self.drift)
        self.s_low = max(0.0, self.s_low - value + self.target - self.drift)

        # Check for signals
        if self.s_high > self.threshold:
            self.s_high = 0.0
            return 1
        if self.s_low > self.threshold:
            self.s_low = 0.0
            return -1

        return 0

    def reset(self) -> None:
        """Reset the detector state."""
        self.s_high = 0.0
        self.s_low = 0.0
        self.target = 0.0
        self.n = 0
        self.sum_values = 0.0

    def get_state(self) -> Dict:
        """Serialize to dict for JSON persistence."""
        return {
            "threshold": self.threshold,
            "drift": self.drift,
            "s_high": self.s_high,
            "s_low": self.s_low,
            "target": self.target,
            "n": self.n,
            "sum_values": self.sum_values,
        }

    @classmethod
    def from_state(cls, state_dict: Dict) -> "CUSUMDetector":
        """Reconstruct from serialized state."""
        detector = cls(
            threshold=state_dict["threshold"],
            drift=state_dict.get("drift", 0.0),
        )
        detector.s_high = state_dict.get("s_high", 0.0)
        detector.s_low = state_dict.get("s_low", 0.0)
        detector.target = state_dict.get("target", 0.0)
        detector.n = state_dict.get("n", 0)
        detector.sum_values = state_dict.get("sum_values", 0.0)
        return detector
