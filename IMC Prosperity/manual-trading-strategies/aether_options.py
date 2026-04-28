"""
aether_options.py
=================
Monte Carlo EV / SD calculator for the Aether Crystal options trading round.

Products (from the order book):
  AC          – underlying spot           bid 49.975 / ask 50.025   (N/A expiry)
  AC_50_P     – 50-strike put             bid 12    / ask 12.05     (T+21 = 3 weeks)
  AC_50_C     – 50-strike call            bid 12    / ask 12.05     (T+21 = 3 weeks)
  AC_35_P     – 35-strike put             bid 4.33  / ask 4.35      (T+21 = 3 weeks)
  AC_40_P     – 40-strike put             bid 6.5   / ask 6.55      (T+21 = 3 weeks)
  AC_45_P     – 45-strike put             bid 9.05  / ask 9.1       (T+21 = 3 weeks)
  AC_60_C     – 60-strike call            bid 8.8   / ask 8.85      (T+21 = 3 weeks)
  AC_50_P_2   – 50-strike put  (2-week)   bid 9.7   / ask 9.75      (T+14 = 2 weeks)
  AC_50_C_2   – 50-strike call (2-week)   bid 9.7   / ask 9.75      (T+14 = 2 weeks)
  AC_50_CO    – chooser K=50, choose@2w   bid 22.2  / ask 22.3      (T+14/21)
  AC_40_BP    – binary put K=40, pays 10  bid 5.0   / ask 5.1       (T+21 = 3 weeks)
  AC_45_KO    – knock-out put K=45 bar=?  bid 0.15  / ask 0.175     (T+21 = 3 weeks)

Scoring model (matches the problem spec exactly)
------------------------------------------------
* The platform scores you on the AVERAGE PnL across exactly 100 simulations.
* Each simulation produces one GBM path; the payoff on that path is realised
  at expiry and marked at face value (intrinsic).
* PnL for one simulation =
      Σ_products  qty × (payoff_on_path − entry_price) × CONTRACT_SIZE
  where CONTRACT_SIZE = 3000 for ALL products including the underlying.
* qty > 0  →  BUY  (entry_price = ask)
  qty < 0  →  SELL (entry_price = bid)

What `run()` gives you
-----------------------
* `pnl_per_sim`  : array of length n_outer_sims, where each element is the
                   average PnL across 100 inner paths — i.e. what the platform
                   would score you on one "roll" of the 100-sim evaluation.
* EV / SD        : mean and std-dev of pnl_per_sim across the outer sims.
  With n_outer_sims=1000 (default) you get a tight estimate of the true
  distribution of your score.

Quick-start
-----------
>>> from aether_options import run
>>> positions = {
...     "AC_50_CO": +10,   # buy 10 choosers
...     "AC_50_P":  -5,    # sell 5 vanilla 3-week puts
... }
>>> results = run(positions, seed=42)
>>> results.summary()
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Simulation constants (match the problem spec exactly)
# ---------------------------------------------------------------------------
TRADING_DAYS_PER_YEAR: int = 252
STEPS_PER_DAY: int = 4
STEPS_PER_YEAR: int = TRADING_DAYS_PER_YEAR * STEPS_PER_DAY
CONTRACT_SIZE: int = 3_000

# Convenience helpers (also defined in the problem spec)
def weeks_to_years(weeks: float) -> float:
    return (weeks * 5) / TRADING_DAYS_PER_YEAR

def steps_for_weeks(weeks: float) -> int:
    return int(round(weeks * 5 * STEPS_PER_DAY))

STEPS_2W: int = steps_for_weeks(2)   # 40
STEPS_3W: int = steps_for_weeks(3)   # 60


# ---------------------------------------------------------------------------
# Product definitions
# ---------------------------------------------------------------------------
@dataclass
class Product:
    """Describes a single tradeable instrument."""
    id: str
    label: str
    kind: str          # underlying | call | put | chooser | binary_put | ko_put
    strike: Optional[float] = None
    barrier: Optional[float] = None  # for knock-out options
    binary_payout: float = 10.0      # for binary options
    expiry_steps: Optional[int] = None
    choice_steps: Optional[int] = None  # for chooser: steps until choice date
    bid: float = 0.0
    ask: float = 0.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    def entry_price(self, qty: float) -> float:
        """Return the fill price: ask when buying, bid when selling."""
        if qty > 0:
            return self.ask
        elif qty < 0:
            return self.bid
        return self.mid

    def payoff(self, path: np.ndarray) -> float:
        """
        Compute the *intrinsic* payoff for ONE contract given a price path.
        `path` is a 1-D array of length (total_steps + 1) covering the
        maximum horizon (3 weeks = 60 steps), starting at S0 = path[0].
        """
        k = self.kind

        if k == "underlying":
            return path[-1] - path[0]

        if k == "call":
            st = path[self.expiry_steps]
            return max(0.0, st - self.strike)

        if k == "put":
            st = path[self.expiry_steps]
            return max(0.0, self.strike - st)

        if k == "chooser":
            # At choice_steps the holder picks call or put.
            # The option then expires at expiry_steps.
            s_choice = path[self.choice_steps]
            s_expiry = path[self.expiry_steps]
            call_val = max(0.0, s_expiry - self.strike)
            put_val  = max(0.0, self.strike  - s_expiry)
            # Simple rule: choose call if spot >= strike at choice date, else put.
            return call_val if s_choice >= self.strike else put_val

        if k == "binary_put":
            st = path[self.expiry_steps]
            return self.binary_payout if st < self.strike else 0.0

        if k == "ko_put":
            # Knocked out if any step (inclusive) touches or crosses below barrier.
            # Only discrete path points are checked (per problem spec).
            knocked = np.any(path[1:self.expiry_steps + 1] <= self.barrier)
            if knocked:
                return 0.0
            st = path[self.expiry_steps]
            return max(0.0, self.strike - st)

        raise ValueError(f"Unknown product kind: {k!r}")


# ---------------------------------------------------------------------------
# Order book (bid / ask from the screenshot)
# ---------------------------------------------------------------------------

# NOTE on AC_45_KO barrier: the problem says "knock-out put" written on AC with
# strike 45. The barrier is not explicitly stated in the table; we default to
# barrier = 35 (a common choice in this problem set). Override via
# PRODUCTS["AC_45_KO"].barrier = <your_value> before calling run().

PRODUCTS: Dict[str, Product] = {
    "AC": Product(
        id="AC", label="AC underlying", kind="underlying",
        expiry_steps=STEPS_3W,   # we use 3w path length; spot just uses path[-1]
        bid=49.975, ask=50.025,
    ),
    "AC_50_P": Product(
        id="AC_50_P", label="AC 50 put (3w)", kind="put",
        strike=50.0, expiry_steps=STEPS_3W,
        bid=12.0, ask=12.05,
    ),
    "AC_50_C": Product(
        id="AC_50_C", label="AC 50 call (3w)", kind="call",
        strike=50.0, expiry_steps=STEPS_3W,
        bid=12.0, ask=12.05,
    ),
    "AC_35_P": Product(
        id="AC_35_P", label="AC 35 put (3w)", kind="put",
        strike=35.0, expiry_steps=STEPS_3W,
        bid=4.33, ask=4.35,
    ),
    "AC_40_P": Product(
        id="AC_40_P", label="AC 40 put (3w)", kind="put",
        strike=40.0, expiry_steps=STEPS_3W,
        bid=6.5, ask=6.55,
    ),
    "AC_45_P": Product(
        id="AC_45_P", label="AC 45 put (3w)", kind="put",
        strike=45.0, expiry_steps=STEPS_3W,
        bid=9.05, ask=9.1,
    ),
    "AC_60_C": Product(
        id="AC_60_C", label="AC 60 call (3w)", kind="call",
        strike=60.0, expiry_steps=STEPS_3W,
        bid=8.8, ask=8.85,
    ),
    "AC_50_P_2": Product(
        id="AC_50_P_2", label="AC 50 put (2w)", kind="put",
        strike=50.0, expiry_steps=STEPS_2W,
        bid=9.7, ask=9.75,
    ),
    "AC_50_C_2": Product(
        id="AC_50_C_2", label="AC 50 call (2w)", kind="call",
        strike=50.0, expiry_steps=STEPS_2W,
        bid=9.7, ask=9.75,
    ),
    "AC_50_CO": Product(
        id="AC_50_CO", label="AC 50 chooser (choice@2w, expiry@3w)", kind="chooser",
        strike=50.0,
        choice_steps=STEPS_2W,   # 40
        expiry_steps=STEPS_3W,   # 60
        bid=22.2, ask=22.3,
    ),
    "AC_40_BP": Product(
        id="AC_40_BP", label="AC 40 binary put (3w, pays 10)", kind="binary_put",
        strike=40.0, binary_payout=10.0, expiry_steps=STEPS_3W,
        bid=5.0, ask=5.1,
    ),
    "AC_45_KO": Product(
        id="AC_45_KO", label="AC 45 knock-out put (3w, barrier=35)", kind="ko_put",
        strike=45.0, barrier=35.0, expiry_steps=STEPS_3W,
        bid=0.15, ask=0.175,
    ),
}


# ---------------------------------------------------------------------------
# Path simulation
# ---------------------------------------------------------------------------

def simulate_paths(
    s0: float,
    sigma: float,
    n_steps: int,
    n_sims: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Returns array of shape (n_sims, n_steps + 1) under risk-neutral GBM
    with zero drift and annualised volatility `sigma`.

    dt = 1 / (TRADING_DAYS_PER_YEAR * STEPS_PER_DAY)
    """
    dt = 1.0 / STEPS_PER_YEAR
    z = rng.standard_normal((n_sims, n_steps))
    log_returns = (-0.5 * sigma**2 * dt) + sigma * math.sqrt(dt) * z
    log_paths = np.concatenate(
        [np.zeros((n_sims, 1)), np.cumsum(log_returns, axis=1)], axis=1
    )
    return s0 * np.exp(log_paths)


# ---------------------------------------------------------------------------
# Results container
# ---------------------------------------------------------------------------

@dataclass
class SimResults:
    positions: Dict[str, float]
    # Each element = average PnL across 100 inner paths for one outer "trial".
    # This is what the platform would report as your score on each evaluation run.
    pnl_per_sim: np.ndarray        # shape (n_outer_sims,)
    # Per-product version of the same: pid -> array(n_outer_sims,)
    per_product: Dict[str, np.ndarray]
    s0: float
    sigma: float
    n_outer_sims: int
    n_inner_paths: int             # always 100, matching the platform

    @property
    def ev(self) -> float:
        """Expected score = mean of average-PnL-over-100-paths."""
        return float(np.mean(self.pnl_per_sim))

    @property
    def sd(self) -> float:
        """Std-dev of the score across outer trials."""
        return float(np.std(self.pnl_per_sim))

    @property
    def sharpe(self) -> float:
        return self.ev / self.sd if self.sd > 0 else float("nan")

    @property
    def prob_profit(self) -> float:
        """Fraction of outer trials where the 100-sim average PnL is positive."""
        return float(np.mean(self.pnl_per_sim > 0))

    def percentile(self, p: float) -> float:
        return float(np.percentile(self.pnl_per_sim, p))

    def summary(self, percentiles: Tuple[float, ...] = (1, 5, 10, 25, 50, 75, 90, 95, 99)) -> None:
        """Pretty-print a summary table."""
        sep = "-" * 58
        print(sep)
        print(f"  Aether Crystal strategy — Monte Carlo summary")
        print(f"  S0={self.s0}  σ={self.sigma:.0%}  "
              f"inner={self.n_inner_paths} paths  outer={self.n_outer_sims:,} trials")
        print(f"  (Each 'trial' = avg PnL across {self.n_inner_paths} sims, matching platform scoring)")
        print(sep)
        print(f"  {'Expected score (EV)':<30} {self.ev:>+16,.2f}")
        print(f"  {'Std dev of score (SD)':<30} {self.sd:>16,.2f}")
        print(f"  {'Sharpe (EV/SD)':<30} {self.sharpe:>16.4f}")
        print(f"  {'P(score > 0)':<30} {self.prob_profit:>15.1%}")
        print(sep)
        print("  Score percentiles:")
        for p in percentiles:
            print(f"    {p:>4}th  {self.percentile(p):>+16,.2f}")
        print(sep)
        print("  Per-product EV breakdown:")
        for pid, arr in sorted(self.per_product.items(), key=lambda x: -abs(np.mean(x[1]))):
            qty = self.positions.get(pid, 0)
            print(f"    {pid:<14} qty={qty:>+6}   EV={np.mean(arr):>+14,.2f}   SD={np.std(arr):>12,.2f}")
        print(sep)

    def histogram(self, bins: int = 30) -> None:
        """Print a simple ASCII histogram of the score distribution."""
        counts, edges = np.histogram(self.pnl_per_sim, bins=bins)
        max_count = counts.max()
        bar_width = 40
        print(f"\n  Score histogram  (outer_sims={self.n_outer_sims:,},"
              f" inner={self.n_inner_paths} paths each)\n")
        for i, cnt in enumerate(counts):
            label = f"{edges[i]:>+10,.0f}"
            bar_len = int(cnt / max_count * bar_width)
            bar = ("█" * bar_len).ljust(bar_width)
            pct = cnt / self.n_outer_sims
            print(f"  {label}  {bar}  {pct:.1%}")
        print()


# ---------------------------------------------------------------------------
# Main simulation entry point
# ---------------------------------------------------------------------------

def run(
    positions: Dict[str, float],
    s0: float = 50.0,
    sigma: float = 2.51,
    n_inner_paths: int = 100,
    n_outer_sims: int = 1_000,
    seed: Optional[int] = None,
    products: Optional[Dict[str, Product]] = None,
) -> SimResults:
    """
    Run the two-level Monte Carlo matching the platform's scoring model.

    Platform scoring
    ----------------
    Your final score = average PnL across exactly 100 simulated paths.
    PnL on one path  = Σ qty × (payoff_on_path − entry_price) × CONTRACT_SIZE

    This function replicates that evaluation n_outer_sims times so you can
    estimate the mean and std-dev of what the platform will report.

    Parameters
    ----------
    positions : dict
        product_id -> signed quantity.
        Positive = buy (filled at ask), negative = sell (filled at bid).
        Example: {"AC_50_CO": +10, "AC_50_P": -10}

    s0 : float
        Initial spot price of AETHER_CRYSTAL (default 50.0).

    sigma : float
        Annualised volatility in decimal form (default 2.51 = 251 %).

    n_inner_paths : int
        Number of paths per evaluation run. Match the platform = 100.

    n_outer_sims : int
        How many independent evaluation runs to simulate (default 1,000).
        Higher → tighter estimate of EV/SD distribution.

    seed : int | None
        RNG seed for reproducibility.

    products : dict | None
        Override the default PRODUCTS dict (e.g. to change a barrier).

    Returns
    -------
    SimResults
        pnl_per_sim[i] = average PnL across the 100 inner paths of trial i.
        Call .summary() or .histogram() on the result.
    """
    if products is None:
        products = PRODUCTS

    rng = np.random.default_rng(seed)

    # Filter to non-zero positions up front
    active = {pid: qty for pid, qty in positions.items() if qty != 0}
    for pid in active:
        if pid not in products:
            raise KeyError(f"Unknown product id: {pid!r}. Available: {list(products)}")

    total_paths = n_outer_sims * n_inner_paths

    # Simulate ALL paths at once: shape (total_paths, STEPS_3W + 1)
    all_paths = simulate_paths(s0, sigma, STEPS_3W, total_paths, rng)

    # Compute per-product PnL on every path: shape (total_paths,) per product
    raw_per_product: Dict[str, np.ndarray] = {}
    total_raw = np.zeros(total_paths)

    for pid, qty in active.items():
        prod = products[pid]
        entry = prod.entry_price(qty)
        intrinsic = _vectorised_payoff(prod, all_paths)
        # PnL per path = qty × (payoff − entry_price) × CONTRACT_SIZE
        # CONTRACT_SIZE = 3000 applies to ALL products including the underlying
        pnl_paths = qty * (intrinsic - entry) * CONTRACT_SIZE
        raw_per_product[pid] = pnl_paths
        total_raw += pnl_paths

    # Reshape to (n_outer_sims, n_inner_paths) and average across inner axis
    # → gives the platform score for each outer trial
    scored = total_raw.reshape(n_outer_sims, n_inner_paths).mean(axis=1)

    per_product_scored: Dict[str, np.ndarray] = {
        pid: arr.reshape(n_outer_sims, n_inner_paths).mean(axis=1)
        for pid, arr in raw_per_product.items()
    }

    return SimResults(
        positions=dict(positions),
        pnl_per_sim=scored,
        per_product=per_product_scored,
        s0=s0,
        sigma=sigma,
        n_outer_sims=n_outer_sims,
        n_inner_paths=n_inner_paths,
    )


def _vectorised_payoff(prod: Product, paths: np.ndarray) -> np.ndarray:
    """
    Compute payoff for all simulated paths at once.
    paths : shape (n_sims, n_steps + 1)
    Returns : shape (n_sims,)
    """
    k = prod.kind

    if k == "underlying":
        # Return S_T — what you receive when you hold the underlying.
        # PnL = qty * (S_T - entry_price) * CONTRACT_SIZE,
        # where entry_price is the ask (~50) when buying, bid when selling.
        return paths[:, prod.expiry_steps]

    if k == "call":
        return np.maximum(0.0, paths[:, prod.expiry_steps] - prod.strike)

    if k == "put":
        return np.maximum(0.0, prod.strike - paths[:, prod.expiry_steps])

    if k == "chooser":
        s_choice = paths[:, prod.choice_steps]
        s_expiry = paths[:, prod.expiry_steps]
        call_val = np.maximum(0.0, s_expiry - prod.strike)
        put_val  = np.maximum(0.0, prod.strike  - s_expiry)
        return np.where(s_choice >= prod.strike, call_val, put_val)

    if k == "binary_put":
        return np.where(paths[:, prod.expiry_steps] < prod.strike, prod.binary_payout, 0.0)

    if k == "ko_put":
        # Check discrete barrier breach over steps 1..expiry_steps
        knocked = np.any(paths[:, 1:prod.expiry_steps + 1] <= prod.barrier, axis=1)
        st = paths[:, prod.expiry_steps]
        vanilla_put = np.maximum(0.0, prod.strike - st)
        return np.where(knocked, 0.0, vanilla_put)

    raise ValueError(f"Unknown kind: {k!r}")


# ---------------------------------------------------------------------------
# Convenience: compute theoretical Black-Scholes price for sanity checks
# ---------------------------------------------------------------------------

def bs_price(kind: str, s: float, k: float, t: float, sigma: float) -> float:
    """
    Vanilla European option price under zero-drift GBM (risk-neutral).
    kind : 'call' or 'put'
    t    : time to expiry in years
    """
    from scipy.stats import norm  # soft dependency

    if t <= 0:
        if kind == "call":
            return max(0.0, s - k)
        return max(0.0, k - s)

    sqrt_t = math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * sigma**2 * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    if kind == "call":
        return s * norm.cdf(d1) - k * norm.cdf(d2)
    elif kind == "put":
        return k * norm.cdf(-d2) - s * norm.cdf(-d1)
    raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")


def fair_value_all(
    s0: float = 50.0,
    sigma: float = 2.51,
    n_sims: int = 100_000,
    seed: Optional[int] = None,
    products: Optional[Dict[str, Product]] = None,
) -> None:
    """
    Print the fair value for every product alongside its bid and ask.

    Vanilla calls and puts use the closed-form Black-Scholes formula (labelled
    'BS').  Everything else — underlying, chooser, binary put, knock-out put —
    is priced by Monte Carlo (labelled 'MC').

    Columns
    -------
    Fair      : theoretical fair value per contract
    Bid / Ask : market prices
    Edge(buy) : fair - ask  →  positive means buying at ask is cheap
    Edge(sell): bid - fair  →  positive means selling at bid is rich

    Parameters
    ----------
    s0      : current spot price (default 50.0)
    sigma   : annualised vol in decimal form (default 2.51 = 251%)
    n_sims  : MC paths for exotic pricing (default 100,000)
    seed    : RNG seed for reproducibility
    """
    if products is None:
        products = PRODUCTS

    rng = np.random.default_rng(seed)
    paths = simulate_paths(s0, sigma, STEPS_3W, n_sims, rng)

    t2w = weeks_to_years(2)
    t3w = weeks_to_years(3)
    VANILLA = {"call", "put"}

    print(
        f"\n  Fair values  —  S0={s0}, σ={sigma:.0%}, MC paths={n_sims:,}\n"
        f"  {'Product':<14} {'Kind':<12} {'Method':<6}  "
        f"{'Fair':>8}  {'Bid':>8}  {'Ask':>8}  {'Edge(buy)':>10}  {'Edge(sell)':>10}\n"
        f"  " + "-" * 80
    )

    for pid, prod in products.items():
        k = prod.kind

        if k in VANILLA:
            t = t2w if prod.expiry_steps == STEPS_2W else t3w
            fv = bs_price(k, s0, prod.strike, t, sigma)
            method = "BS"
        elif k == "underlying":
            # bid/ask are spot prices (~50), not option premiums.
            # Fair value = E[S_T] = S0 (zero drift GBM).
            # Edge = what you expect to receive minus what you pay.
            fv = float(np.mean(paths[:, prod.expiry_steps]))
            method = "MC"
        else:
            fv = float(np.mean(_vectorised_payoff(prod, paths)))
            method = "MC"

        # For the underlying: you pay ask (~50) and receive S_T (~50), so
        # edge_buy = E[S_T] - ask.  For options: you pay the premium and
        # receive the payoff, so edge_buy = fair_payoff - ask.  Same formula.
        edge_buy  = fv - prod.ask    # positive = cheap to buy
        edge_sell = prod.bid - fv    # positive = rich to sell

        def _fmt(e: float) -> str:
            return f"{'+' if e >= 0 else ''}{e:.4f}"

        print(
            f"  {pid:<14} {k:<12} {method:<6}  "
            f"{fv:>8.4f}  {prod.bid:>8.4f}  {prod.ask:>8.4f}  "
            f"{_fmt(edge_buy):>10}  {_fmt(edge_sell):>10}"
        )

    print()


# ---------------------------------------------------------------------------
# Example usage (run this file directly for a quick demo)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Example: buy the chooser and hedge both wings with vanilla calls + puts
    example_positions = {
        "AC_50_CO":  +1,   # buy 1 chooser
        "AC_50_P":   -1,   # sell 1 vanilla 3w put  (hedge put wing)
        "AC_50_C":   -1,   # sell 1 vanilla 3w call (hedge call wing)
    }

    print("\nRunning example strategy...")
    results = run(example_positions, n_inner_paths=100, n_outer_sims=2_000, seed=0)
    results.summary()
    results.histogram()

    print("\nFair values for all products:")
    fair_value_all(seed=1)