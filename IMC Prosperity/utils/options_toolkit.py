"""
Options Toolkit — Black-Scholes pricing, 12 Greeks, IV solver, vol smile fitting, structures.

Stdlib-only (math, json). No numpy/scipy required.
Ported from phase1_foundations.ipynb with pure-Python replacements for norm_cdf, norm_pdf, brentq.

Usage in Trader.run():
    from options_toolkit import bs_price, implied_vol, bs_greeks, quick_greeks
    from options_toolkit import fit_vol_smile, vol_smile_deviation, iv_deviation_to_price
    from options_toolkit import straddle, call_spread, Structure, Leg
"""

import math
from collections import namedtuple
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════════════════════
# Internal helpers — stdlib replacements for scipy/numpy
# ═══════════════════════════════════════════════════════════════════════════════

_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf (exact to machine precision)."""
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / _SQRT2PI


def _bisect_solve(f, a: float, b: float, tol: float = 1e-8, max_iter: int = 100) -> float:
    """Pure-Python bisection root finder. Replaces scipy.optimize.brentq."""
    fa, fb = f(a), f(b)
    if fa * fb > 0:
        return float('nan')
    for _ in range(max_iter):
        mid = 0.5 * (a + b)
        fm = f(mid)
        if abs(fm) < tol or (b - a) * 0.5 < tol:
            return mid
        if fa * fm < 0:
            b = mid
            fb = fm
        else:
            a = mid
            fa = fm
    return 0.5 * (a + b)


# ═══════════════════════════════════════════════════════════════════════════════
# Black-Scholes primitives
# ═══════════════════════════════════════════════════════════════════════════════

def bs_d1(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """d1 in the Black-Scholes formula."""
    return (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))


def bs_d2(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """d2 in the Black-Scholes formula."""
    return bs_d1(S, K, T, r, sigma, q) - sigma * math.sqrt(T)


def bs_call(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Black-Scholes call price."""
    d1 = bs_d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def bs_put(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Black-Scholes put price."""
    d1 = bs_d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)


def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str = "call", q: float = 0.0) -> float:
    """Black-Scholes option price. option_type: 'call' or 'put'."""
    if option_type == "call":
        return bs_call(S, K, T, r, sigma, q)
    else:
        return bs_put(S, K, T, r, sigma, q)


# ═══════════════════════════════════════════════════════════════════════════════
# Greeks — all 12
# ═══════════════════════════════════════════════════════════════════════════════

def bs_greeks(S: float, K: float, T: float, r: float, sigma: float,
              option_type: str = "call", q: float = 0.0, multiplier: int = 100) -> Dict[str, float]:
    """
    Compute all 12 Black-Scholes Greeks.

    Returns dict with keys:
        delta, cash_delta, gamma, cash_gamma_1pct, dual_delta, dual_gamma,
        vega, theta, rho, charm, vanna, volga

    Units follow the convention from phase1_foundations.ipynb:
        - delta: unitless (0 to 1 for calls)
        - cash_delta: $ per $1 spot move (= delta * S * multiplier)
        - vega: $/share per 1pp IV (divided by 100)
        - theta: $/share per trading day (divided by 256 trading days)
        - rho: $/share per 1pp rate (divided by 100)
        - gamma: delta per $1 spot
        - cash_gamma_1pct: $ change in cash delta from 1% spot move
        - vanna: d(delta) per 1pp IV (divided by 100)
        - volga: d(vega) per 1pp IV (divided by 100)
        - charm: d(delta) per trading day (divided by 256)
    """
    sqrtT = math.sqrt(T)
    d1 = bs_d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * sqrtT

    eqT = math.exp(-q * T)
    erT = math.exp(-r * T)

    nd1 = _norm_cdf(d1)
    nd2 = _norm_cdf(d2)
    pd1 = _norm_pdf(d1)

    sign = 1.0 if option_type == "call" else -1.0

    # --- First-order ---
    delta = sign * eqT * (nd1 if option_type == "call" else nd1 - 1.0)
    cash_delta = delta * S * multiplier

    dual_delta = sign * erT * (-nd2 if option_type == "call" else 1.0 - nd2)
    # Flip sign for put: dual_delta for put = erT * N(-d2) ... actually let me follow the notebook exactly
    if option_type == "call":
        delta = eqT * nd1
        dual_delta = -erT * nd2
    else:
        delta = -eqT * (1.0 - nd1)
        dual_delta = erT * (1.0 - nd2)

    cash_delta = delta * S * multiplier

    # Vega (per 1 percentage point of IV → divide by 100)
    vega = S * eqT * pd1 * sqrtT / 100.0

    # Theta (per trading day → divide by 256)
    if option_type == "call":
        theta_annual = (-S * eqT * pd1 * sigma / (2.0 * sqrtT)
                        + q * S * eqT * nd1
                        - r * K * erT * nd2)
    else:
        theta_annual = (-S * eqT * pd1 * sigma / (2.0 * sqrtT)
                        - q * S * eqT * (1.0 - nd1)
                        + r * K * erT * (1.0 - nd2))
    theta = theta_annual / 256.0

    # Rho (per 1 percentage point → divide by 100)
    if option_type == "call":
        rho = K * T * erT * nd2 / 100.0
    else:
        rho = -K * T * erT * (1.0 - nd2) / 100.0

    # --- Second-order ---
    gamma = eqT * pd1 / (S * sigma * sqrtT)
    cash_gamma_1pct = 0.5 * gamma * S * (0.01 * S) * multiplier

    dual_gamma = erT * _norm_pdf(d2) / (K * sigma * sqrtT)

    # Vanna: d(delta)/d(sigma) per 1pp → divide by 100
    vanna = -eqT * pd1 * d2 / (sigma * 100.0)

    # Volga: d(vega)/d(sigma) per 1pp → divide by 100
    volga = S * eqT * pd1 * sqrtT * d1 * d2 / (sigma * 100.0 * 100.0)

    # Charm: d(delta)/dt per trading day → divide by 256
    charm_annual = -eqT * pd1 * (2.0 * (r - q) * T - d2 * sigma * sqrtT) / (2.0 * T * sigma * sqrtT)
    if option_type == "call":
        charm_annual += q * eqT * nd1
    else:
        charm_annual -= q * eqT * (1.0 - nd1)
    # Charm is d(delta)/dt; we want per trading day so divide by -256
    # (negative because time decreases)
    charm = -charm_annual / 256.0

    return {
        "delta": delta,
        "cash_delta": cash_delta,
        "gamma": gamma,
        "cash_gamma_1pct": cash_gamma_1pct,
        "dual_delta": dual_delta,
        "dual_gamma": dual_gamma,
        "vega": vega,
        "theta": theta,
        "rho": rho,
        "charm": charm,
        "vanna": vanna,
        "volga": volga,
    }


def quick_greeks(S: float, K: float, T: float, r: float, sigma: float,
                 option_type: str = "call") -> Dict[str, float]:
    """Simplified Greeks: just delta, gamma, vega, theta for fast use in trading loop."""
    sqrtT = math.sqrt(T)
    d1 = bs_d1(S, K, T, r, sigma, 0.0)
    d2 = d1 - sigma * sqrtT
    pd1 = _norm_pdf(d1)
    nd1 = _norm_cdf(d1)
    nd2 = _norm_cdf(d2)
    erT = math.exp(-r * T)

    if option_type == "call":
        delta = nd1
    else:
        delta = nd1 - 1.0

    gamma = pd1 / (S * sigma * sqrtT)

    vega = S * pd1 * sqrtT  # raw vega (per unit sigma, not per 1pp)

    if option_type == "call":
        theta = (-S * pd1 * sigma / (2.0 * sqrtT) - r * K * erT * nd2)
    else:
        theta = (-S * pd1 * sigma / (2.0 * sqrtT) + r * K * erT * (1.0 - nd2))

    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


# ═══════════════════════════════════════════════════════════════════════════════
# Implied Volatility solver
# ═══════════════════════════════════════════════════════════════════════════════

def implied_vol(market_price: float, S: float, K: float, T: float, r: float,
                option_type: str = "call", q: float = 0.0,
                tol: float = 1e-8, max_iter: int = 50) -> float:
    """
    Implied volatility via Newton-Raphson with bisection fallback.
    Returns float('nan') if no solution found.
    """
    # Newton-Raphson
    sigma = 0.2  # initial guess
    for _ in range(max_iter):
        price = bs_price(S, K, T, r, sigma, option_type, q)
        diff = price - market_price
        if abs(diff) < tol:
            return sigma
        # Vega (raw, unscaled)
        d1 = bs_d1(S, K, T, r, sigma, q)
        vega = S * math.exp(-q * T) * _norm_pdf(d1) * math.sqrt(T)
        if vega < 1e-12:
            break  # vega too small, Newton won't converge
        sigma -= diff / vega
        if sigma <= 0:
            break  # went negative, fallback to bisection

    # Bisection fallback
    def objective(sig: float) -> float:
        return bs_price(S, K, T, r, sig, option_type, q) - market_price

    return _bisect_solve(objective, 1e-6, 5.0, tol, 200)


# ═══════════════════════════════════════════════════════════════════════════════
# Vol surface helpers
# ═══════════════════════════════════════════════════════════════════════════════

def log_moneyness(K: float, F: float) -> float:
    """Log-moneyness: ln(K / F)."""
    return math.log(K / F)


def total_variance(sigma: float, T: float) -> float:
    """Total variance: sigma^2 * T."""
    return sigma * sigma * T


# ═══════════════════════════════════════════════════════════════════════════════
# Forward pricing
# ═══════════════════════════════════════════════════════════════════════════════

def forward_continuous(S: float, r: float, T: float,
                       div_yield: float = 0.0, tax_rate: float = 0.0) -> float:
    """Forward price with continuous dividend yield and optional tax on carry."""
    net_rate = (r - div_yield) * (1.0 - tax_rate)
    return S * math.exp(net_rate * T)


def forward_discrete(S: float, r: float, T: float,
                     divs: Optional[List[Tuple[float, float]]] = None,
                     tax_rate: float = 0.0) -> float:
    """
    Forward price with discrete dividends.
    divs: list of (time_to_div, div_amount) pairs.
    """
    pv_divs = 0.0
    if divs:
        for t_div, d_amt in divs:
            if t_div <= T:
                pv_divs += d_amt * (1.0 - tax_rate) * math.exp(-r * t_div)
    return (S - pv_divs) * math.exp(r * T)


def decompose_dividend(total_div: float, S: float, proportionality: float = 0.0,
                       tax_rate: float = 0.0) -> Tuple[float, float]:
    """
    Decompose total dividend into proportional and fixed components.
    proportionality: fraction that is proportional to spot (0 to 1).
    Returns (proportional_yield, fixed_amount).
    """
    prop_amount = proportionality * total_div
    fixed_amount = total_div - prop_amount
    prop_yield = prop_amount / S if S != 0 else 0.0
    return (prop_yield * (1.0 - tax_rate), fixed_amount * (1.0 - tax_rate))


# ═══════════════════════════════════════════════════════════════════════════════
# Implied calibration
# ═══════════════════════════════════════════════════════════════════════════════

def implied_forward(K: float, C: float, P: float, T: float, r: float) -> float:
    """Implied forward from put-call parity: F = K + (C - P) * exp(rT)."""
    return K + (C - P) * math.exp(r * T)


def implied_dividend_yield(S: float, F: float, T: float, r: float) -> float:
    """Implied continuous dividend yield: q = r - ln(F/S) / T."""
    if T == 0 or S == 0:
        return 0.0
    return r - math.log(F / S) / T


# ═══════════════════════════════════════════════════════════════════════════════
# Variance swap fair strike
# ═══════════════════════════════════════════════════════════════════════════════

def varswap_fair_strike(strikes: List[float], ivs: List[float],
                        T: float, r: float, F: float) -> float:
    """
    Variance swap fair strike (Kvar) via discrete replication.
    strikes and ivs must be sorted or will be sorted internally.
    Returns annualized variance strike (take sqrt for vol strike).
    """
    n = len(strikes)
    if n < 2:
        return 0.0

    # Sort by strike
    order = sorted(range(n), key=lambda i: strikes[i])
    K_sorted = [strikes[i] for i in order]
    iv_sorted = [ivs[i] for i in order]

    erT = math.exp(-r * T)
    total = 0.0

    for i in range(n):
        Ki = K_sorted[i]
        sigi = iv_sorted[i]
        # Option price
        if Ki < F:
            price_i = bs_put(F * math.exp(-r * T), Ki, T, r, sigi)  # use forward as spot proxy
        else:
            price_i = bs_call(F * math.exp(-r * T), Ki, T, r, sigi)

        # Weight: dK / Ki^2
        if i == 0:
            dK = K_sorted[1] - K_sorted[0]
        elif i == n - 1:
            dK = K_sorted[-1] - K_sorted[-2]
        else:
            dK = 0.5 * (K_sorted[i + 1] - K_sorted[i - 1])

        total += (dK / (Ki * Ki)) * price_i

    kvar = (2.0 / T) * math.exp(r * T) * total
    return kvar


# ═══════════════════════════════════════════════════════════════════════════════
# Competition-specific: Vol smile fitting (Frankfurt Hedgehogs approach)
# ═══════════════════════════════════════════════════════════════════════════════

def fit_vol_smile(strikes: List[float], ivs: List[float],
                  forward: Optional[float] = None) -> Tuple[float, float, float]:
    """
    Fit parabola IV = a*m^2 + b*m + c where m = log_moneyness(K, F).
    Uses least-squares normal equations (3x3 system, no numpy needed).

    Args:
        strikes: list of strike prices
        ivs: list of implied volatilities at those strikes
        forward: forward price. If None, uses average of strikes as proxy.

    Returns:
        (a, b, c) coefficients of the fitted parabola
    """
    n = len(strikes)
    if n < 3:
        # Underdetermined — return flat at mean IV
        mean_iv = sum(ivs) / n if n > 0 else 0.0
        return (0.0, 0.0, mean_iv)

    F = forward if forward is not None else sum(strikes) / n

    # Build moneyness values
    m = [math.log(K / F) for K in strikes]

    # Normal equations for quadratic fit: [sum_m4, sum_m3, sum_m2] [a]   [sum_m2_iv]
    #                                      [sum_m3, sum_m2, sum_m ] [b] = [sum_m_iv ]
    #                                      [sum_m2, sum_m,  n     ] [c]   [sum_iv   ]
    sum_m = sum(m)
    sum_m2 = sum(mi * mi for mi in m)
    sum_m3 = sum(mi ** 3 for mi in m)
    sum_m4 = sum(mi ** 4 for mi in m)
    sum_iv = sum(ivs)
    sum_m_iv = sum(mi * iv for mi, iv in zip(m, ivs))
    sum_m2_iv = sum(mi * mi * iv for mi, iv in zip(m, ivs))

    # Solve 3x3 system via Cramer's rule
    # A = [[sum_m4, sum_m3, sum_m2],
    #      [sum_m3, sum_m2, sum_m ],
    #      [sum_m2, sum_m,  n     ]]
    # b = [sum_m2_iv, sum_m_iv, sum_iv]

    def det3(a11, a12, a13, a21, a22, a23, a31, a32, a33):
        return (a11 * (a22 * a33 - a23 * a32)
                - a12 * (a21 * a33 - a23 * a31)
                + a13 * (a21 * a32 - a22 * a31))

    D = det3(sum_m4, sum_m3, sum_m2,
             sum_m3, sum_m2, sum_m,
             sum_m2, sum_m, n)

    if abs(D) < 1e-15:
        # Singular — return flat
        return (0.0, 0.0, sum_iv / n)

    Da = det3(sum_m2_iv, sum_m3, sum_m2,
              sum_m_iv, sum_m2, sum_m,
              sum_iv, sum_m, n)

    Db = det3(sum_m4, sum_m2_iv, sum_m2,
              sum_m3, sum_m_iv, sum_m,
              sum_m2, sum_iv, n)

    Dc = det3(sum_m4, sum_m3, sum_m2_iv,
              sum_m3, sum_m2, sum_m_iv,
              sum_m2, sum_m, sum_iv)

    return (Da / D, Db / D, Dc / D)


def vol_smile_deviation(strike: float, fit_params: Tuple[float, float, float],
                        current_iv: float, forward: Optional[float] = None) -> float:
    """
    Deviation of current_iv from the fitted vol smile at a given strike.

    Args:
        strike: the strike price to evaluate
        fit_params: (a, b, c) from fit_vol_smile
        current_iv: observed implied volatility
        forward: forward price used in the fit

    Returns:
        current_iv - fitted_iv (positive = IV is above smile)
    """
    F = forward if forward is not None else strike  # fallback
    m = math.log(strike / F)
    a, b, c = fit_params
    fitted_iv = a * m * m + b * m + c
    return current_iv - fitted_iv


def iv_deviation_to_price(iv_dev: float, S: float, K: float, T: float, r: float,
                          sigma: float, option_type: str = "call") -> float:
    """
    Convert IV deviation to price deviation via BS vega.
    price_deviation = iv_dev * vega (raw, unscaled).

    If IV is 2pp above the smile and vega is 0.30, the option is ~0.60 overpriced.
    """
    d1 = bs_d1(S, K, T, r, sigma, 0.0)
    vega_raw = S * _norm_pdf(d1) * math.sqrt(T)
    return iv_dev * vega_raw


# ═══════════════════════════════════════════════════════════════════════════════
# Smile features — interpretable metrics from fitted vol smile
# ═══════════════════════════════════════════════════════════════════════════════

def smile_features(fit_params: Tuple[float, float, float]) -> Dict[str, float]:
    """
    Extract interpretable features from a fitted vol smile.

    Given fit_params (a, b, c) from fit_vol_smile where IV = a*m^2 + b*m + c
    and m = log_moneyness(K, F):

    Returns dict:
        level:          c — ATM implied vol (vol at zero moneyness)
        skew:           b — slope at ATM. Negative = puts are richer (downside fear).
        curvature:      a — convexity. Positive = smile shape, wings are expensive.
        min_moneyness:  -b/(2a) — moneyness where IV is minimized (smile vertex).
                        Tells you which strike has the cheapest vol.
        min_iv:         c - b^2/(4a) — the minimum IV on the fitted curve.

    Usage:
        params = fit_vol_smile(strikes, ivs, forward=F)
        feat = smile_features(params)
        if feat["skew"] < -0.5:
            # Puts are very expensive — possible skew trading opportunity
    """
    a, b, c = fit_params

    result: Dict[str, float] = {
        "level": c,
        "skew": b,
        "curvature": a,
    }

    if abs(a) > 1e-12:
        result["min_moneyness"] = -b / (2.0 * a)
        result["min_iv"] = c - (b * b) / (4.0 * a)
    else:
        # Flat or linear smile — no vertex
        result["min_moneyness"] = 0.0
        result["min_iv"] = c

    return result


def smile_delta_skew(strikes: List[float], ivs: List[float],
                     S: float, T: float, r: float,
                     put_delta: float = -0.25,
                     call_delta: float = 0.25) -> Optional[Dict[str, float]]:
    """
    Delta-parameterised skew metrics (industry standard).

    Finds the IVs at target delta levels via interpolation, then computes:

        risk_reversal:  IV(put_delta) - IV(call_delta)
                        Positive = puts more expensive = downside fear.
        butterfly:      0.5*(IV(put_delta) + IV(call_delta)) - IV(ATM)
                        Positive = wings expensive = market expects fat tails.
        atm_vol:        IV at the strike closest to 0.50 delta.

    Why delta-parameterised? Moneyness-based skew shifts as spot moves,
    while delta-based skew is stable and comparable across time.
    This is how every vol desk in the world quotes skew.

    Args:
        strikes: list of strike prices
        ivs: list of implied volatilities
        S: current spot price
        T: time to expiry (years)
        r: risk-free rate
        put_delta: target put delta (negative, e.g., -0.25)
        call_delta: target call delta (positive, e.g., 0.25)

    Returns:
        Dict with risk_reversal, butterfly, atm_vol, put_iv, call_iv.
        Returns None if insufficient strikes to interpolate.
    """
    n = len(strikes)
    if n < 2 or n != len(ivs):
        return None

    # Compute BS delta for each (strike, iv) pair
    deltas: List[float] = []
    for K, iv in zip(strikes, ivs):
        if iv <= 0 or T <= 0:
            deltas.append(0.0)
            continue
        d1 = bs_d1(S, K, T, r, iv, 0.0)
        # Call delta for sorting; put delta = call_delta - 1
        deltas.append(_norm_cdf(d1))

    # Sort by delta ascending
    order = sorted(range(n), key=lambda i: deltas[i])
    sorted_deltas = [deltas[i] for i in order]
    sorted_ivs = [ivs[i] for i in order]

    def interp_iv(target_delta: float) -> Optional[float]:
        """Linear interpolation of IV at a target call-delta."""
        # Clamp to range
        if target_delta <= sorted_deltas[0]:
            return sorted_ivs[0]
        if target_delta >= sorted_deltas[-1]:
            return sorted_ivs[-1]
        for j in range(len(sorted_deltas) - 1):
            d_lo, d_hi = sorted_deltas[j], sorted_deltas[j + 1]
            if d_lo <= target_delta <= d_hi:
                if abs(d_hi - d_lo) < 1e-12:
                    return sorted_ivs[j]
                frac = (target_delta - d_lo) / (d_hi - d_lo)
                return sorted_ivs[j] + frac * (sorted_ivs[j + 1] - sorted_ivs[j])
        return None

    # Convert put_delta to call-delta space: call_delta = put_delta + 1
    put_as_call_delta = put_delta + 1.0  # e.g., -0.25 → 0.75...

    # Actually: put delta = N(d1) - 1, so if put_delta = -0.25, then N(d1) = 0.75
    # But that's wrong — a 25-delta put has call-delta = 0.75 only if the option is
    # the same option. In practice, 25-delta put means a different strike than 25-delta call.
    # The correct approach:
    #   25-delta put = strike where |put_delta| = 0.25 → call_delta = 1 - 0.25 = 0.75
    #   25-delta call = strike where call_delta = 0.25
    put_call_delta = 1.0 + put_delta  # -0.25 → 0.75
    atm_delta = 0.5

    iv_put = interp_iv(put_call_delta)
    iv_call = interp_iv(call_delta)
    iv_atm = interp_iv(atm_delta)

    if iv_put is None or iv_call is None or iv_atm is None:
        return None

    return {
        "risk_reversal": iv_put - iv_call,
        "butterfly": 0.5 * (iv_put + iv_call) - iv_atm,
        "atm_vol": iv_atm,
        "put_iv": iv_put,
        "call_iv": iv_call,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Realised volatility estimators
# ═══════════════════════════════════════════════════════════════════════════════

def realised_vol_close(prices: List[float], window: int = 20,
                       annualise: float = 1.0) -> Optional[float]:
    """
    Close-to-close realised volatility (standard deviation of log returns).

    sigma = std(ln(P_t / P_{t-1})) * annualise

    Args:
        prices: list of prices (at least window + 1 elements; uses last window+1)
        window: number of returns to use (uses the most recent)
        annualise: scaling factor. For Prosperity:
            - 1.0 = per-tick vol (raw)
            - sqrt(10000) ≈ 100 = per-day vol (1 day ≈ 10000 ticks)
            Leave at 1.0 when comparing against implied_vol that's also per-tick.

    Returns:
        Realised volatility, or None if insufficient data.
    """
    if len(prices) < window + 1:
        return None

    # Use last window+1 prices → window returns
    p = prices[-(window + 1):]
    log_returns = [math.log(p[i] / p[i - 1]) for i in range(1, len(p)) if p[i - 1] > 0]

    if len(log_returns) < 2:
        return None

    mean_r = sum(log_returns) / len(log_returns)
    variance = sum((r - mean_r) ** 2 for r in log_returns) / (len(log_returns) - 1)

    return math.sqrt(variance) * annualise


def realised_vol_ew(prices: List[float], decay: float = 0.94) -> Optional[float]:
    """
    Exponentially-weighted realised vol (EWMA / RiskMetrics style).

    sigma^2_t = decay * sigma^2_{t-1} + (1 - decay) * r^2_t

    More responsive to recent moves than equal-weight window.
    A vol spike immediately raises the estimate.

    Args:
        prices: list of prices (at least 3 elements)
        decay: weight given to past variance (0 < decay < 1).
            0.94 = RiskMetrics daily convention.
            0.97-0.99 = appropriate for Prosperity's 100ms ticks.

    Returns:
        Exponentially-weighted volatility (per-tick), or None if insufficient data.
    """
    if len(prices) < 3:
        return None

    # Compute log returns
    log_returns = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0:
            log_returns.append(math.log(prices[i] / prices[i - 1]))

    if len(log_returns) < 2:
        return None

    # Initialise variance with first return squared
    var = log_returns[0] ** 2

    # EWMA update
    for i in range(1, len(log_returns)):
        var = decay * var + (1.0 - decay) * log_returns[i] ** 2

    return math.sqrt(var)


# ═══════════════════════════════════════════════════════════════════════════════
# Gamma scalping framework — realised vs implied vol trading
# ═══════════════════════════════════════════════════════════════════════════════

def gamma_pnl(gamma: float, dS: float, multiplier: int = 1) -> float:
    """
    Instantaneous gamma P&L from a spot move.

    pnl = 0.5 * gamma * dS^2 * multiplier

    Long gamma → profit from moves in either direction.
    Short gamma → lose from moves, profit from time decay.

    Args:
        gamma: position gamma (from bs_greeks or quick_greeks)
        dS: spot price change (absolute, in price units)
        multiplier: contract multiplier (default 1 for Prosperity)
    """
    return 0.5 * gamma * dS * dS * multiplier


def theta_cost(theta: float, dt: float = 1.0, multiplier: int = 1) -> float:
    """
    Theta cost (time decay) over dt ticks.

    cost = -theta * dt * multiplier

    theta is typically negative for long options, so cost is positive
    (you lose money as time passes). Returns a positive number for
    the cost of holding a long option position.

    Args:
        theta: position theta (from quick_greeks; negative for long options)
        dt: number of time steps (ticks)
        multiplier: contract multiplier
    """
    return -theta * dt * multiplier


def gamma_theta_ratio(gamma: float, theta: float) -> Optional[float]:
    """
    Breakeven spot move for a gamma-theta position.

    breakeven = sqrt(-2 * theta / gamma)

    If the actual spot move exceeds this, a long-gamma position profits.
    If the actual move is smaller, theta decay wins.

    This is the fundamental equation of gamma scalping:
    you need realised vol to exceed the breakeven to make money.

    Args:
        gamma: position gamma (positive for long options)
        theta: position theta (negative for long options)

    Returns:
        Breakeven spot move in price units, or None if inputs are invalid.
    """
    if gamma <= 0 or theta >= 0:
        return None  # only meaningful for long gamma, negative theta

    return math.sqrt(-2.0 * theta / gamma)


def vol_edge(realised_vol: float, implied_vol: float) -> float:
    """
    Volatility edge: realised minus implied.

    Positive = realised > implied → options are cheap → buy options (long gamma).
    Negative = implied > realised → options are rich → sell options (short gamma).

    This is the core signal for gamma scalping strategies.
    """
    return realised_vol - implied_vol


def gamma_scalp_signal(realised_vol: float, implied_vol: float,
                       gamma: float, theta: float, S: float,
                       confidence: float = 1.0) -> Dict[str, float]:
    """
    Combined gamma scalping signal — everything you need in one call.

    Computes the full picture: is it profitable to be long or short gamma,
    and by how much?

    Args:
        realised_vol: estimated realised vol (per-tick, same units as implied_vol)
        implied_vol: current implied vol (per-tick)
        gamma: position gamma per unit
        theta: position theta per unit (negative for long options)
        S: current spot price (needed to convert vol to price moves)
        confidence: scaling factor for the signal (0 to 1). Reduce if your
            vol estimate is uncertain.

    Returns dict:
        vol_edge:           realised - implied (positive = long gamma is profitable)
        breakeven_move:     price move needed for gamma to offset theta
        expected_gamma_pnl: 0.5 * gamma * (realised_vol * S)^2 per tick
        theta_cost:         |theta| per tick
        net_edge:           expected_gamma_pnl - theta_cost (positive = go long gamma)
        position_sign:      +1 (buy options/long gamma) or -1 (sell options/short gamma)

    Usage:
        sig = gamma_scalp_signal(rv, iv, gamma, theta, spot)
        if sig["net_edge"] > min_threshold:
            # Buy options at this strike
        elif sig["net_edge"] < -min_threshold:
            # Sell options at this strike
    """
    edge = realised_vol - implied_vol

    # Expected price move per tick based on realised vol
    expected_move = realised_vol * S  # 1-sigma move in price units

    # Expected gamma P&L per tick: E[0.5 * gamma * dS^2] = 0.5 * gamma * sigma^2 * S^2
    exp_gamma_pnl = 0.5 * abs(gamma) * (expected_move ** 2)

    # Theta cost per tick
    t_cost = abs(theta) if theta != 0 else 0.0

    # Net edge
    net = (exp_gamma_pnl - t_cost) * confidence

    # Breakeven
    be_move = gamma_theta_ratio(abs(gamma), theta) if gamma != 0 and theta < 0 else None

    # Direction: long gamma if realised > implied
    sign = 1.0 if edge >= 0 else -1.0

    return {
        "vol_edge": edge,
        "breakeven_move": be_move if be_move is not None else 0.0,
        "expected_gamma_pnl": exp_gamma_pnl,
        "theta_cost": t_cost,
        "net_edge": net,
        "position_sign": sign * confidence,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Weighted Vega — portfolio-level vol exposure analysis
# ═══════════════════════════════════════════════════════════════════════════════

def portfolio_vega(positions: List[Dict], S: float, r: float,
                   q: float = 0.0) -> Dict[str, object]:
    """
    Aggregate vega across an options portfolio.

    Args:
        positions: list of dicts, each with keys:
            strike (float), sigma (float), T (float),
            option_type (str: "call"/"put"), quantity (int/float)
        S: current spot price
        r: risk-free rate
        q: dividend yield

    Returns dict:
        total_vega:       net vega across all positions
        vega_by_strike:   dict {strike: vega} for each unique strike
        weighted_strike:  vega-weighted average strike (center of exposure)
        vega_concentration: max_single_strike_vega / total_abs_vega (0 to 1;
                           1.0 = all exposure in one strike)

    Usage:
        positions = [
            {"strike": 680, "sigma": 0.20, "T": 0.12, "option_type": "call", "quantity": 5},
            {"strike": 700, "sigma": 0.22, "T": 0.12, "option_type": "put", "quantity": -3},
        ]
        pv = portfolio_vega(positions, S=690, r=0.05)
        print(f"Net vega: {pv['total_vega']:.2f}, concentrated at K={pv['weighted_strike']:.0f}")
    """
    if not positions:
        return {"total_vega": 0.0, "vega_by_strike": {}, "weighted_strike": 0.0,
                "vega_concentration": 0.0}

    vega_by_strike: Dict[float, float] = {}

    for pos in positions:
        K = pos["strike"]
        sigma = pos["sigma"]
        T = pos["T"]
        qty = pos["quantity"]

        # Compute raw vega (unscaled, per-unit-sigma)
        d1 = bs_d1(S, K, T, r, sigma, q)
        raw_vega = S * math.exp(-q * T) * _norm_pdf(d1) * math.sqrt(T)

        position_vega = qty * raw_vega

        if K in vega_by_strike:
            vega_by_strike[K] += position_vega
        else:
            vega_by_strike[K] = position_vega

    total_vega = sum(vega_by_strike.values())
    total_abs_vega = sum(abs(v) for v in vega_by_strike.values())

    # Vega-weighted average strike
    if total_abs_vega > 0:
        weighted_strike = sum(K * abs(v) for K, v in vega_by_strike.items()) / total_abs_vega
        max_abs = max(abs(v) for v in vega_by_strike.values())
        concentration = max_abs / total_abs_vega
    else:
        weighted_strike = 0.0
        concentration = 0.0

    return {
        "total_vega": total_vega,
        "vega_by_strike": vega_by_strike,
        "weighted_strike": weighted_strike,
        "vega_concentration": concentration,
    }


def vega_bucketed(positions: List[Dict], S: float, r: float,
                  bucket_boundaries: Optional[List[float]] = None,
                  q: float = 0.0) -> Dict[str, float]:
    """
    Vega bucketed by moneyness region.

    Splits vega into named buckets to understand where your vol exposure sits.
    Helps answer: "Am I long wing vol and short ATM vol?" (a butterfly position)
    vs "Am I uniformly long vol?" (a straddle-like position).

    Args:
        positions: same format as portfolio_vega
        S: current spot price
        r: risk-free rate
        bucket_boundaries: moneyness boundaries (list of floats, ascending).
            Default: [-0.05, -0.02, 0.02, 0.05] creating 5 buckets.
        q: dividend yield

    Default buckets (by moneyness m = ln(K/S)):
        m < -0.05:           "deep_otm_puts"
        -0.05 <= m < -0.02:  "otm_puts"
        -0.02 <= m <= 0.02:  "atm"
        0.02 < m <= 0.05:    "otm_calls"
        m > 0.05:            "deep_otm_calls"

    Returns:
        Dict of {bucket_name: total_vega_in_bucket}
    """
    if bucket_boundaries is None:
        bucket_boundaries = [-0.05, -0.02, 0.02, 0.05]

    bucket_names = ["deep_otm_puts"]
    for i in range(len(bucket_boundaries) - 1):
        lo = bucket_boundaries[i]
        hi = bucket_boundaries[i + 1]
        if lo < 0 and hi <= 0:
            bucket_names.append("otm_puts")
        elif lo < 0 and hi > 0:
            bucket_names.append("atm")
        elif lo >= 0 and hi > 0:
            bucket_names.append("otm_calls")
        else:
            bucket_names.append(f"bucket_{i+1}")
    bucket_names.append("deep_otm_calls")

    buckets: Dict[str, float] = {name: 0.0 for name in bucket_names}

    for pos in positions:
        K = pos["strike"]
        sigma = pos["sigma"]
        T = pos["T"]
        qty = pos["quantity"]

        # Compute moneyness and vega
        m = math.log(K / S) if S > 0 else 0.0
        d1 = bs_d1(S, K, T, r, sigma, q)
        raw_vega = S * math.exp(-q * T) * _norm_pdf(d1) * math.sqrt(T)
        position_vega = qty * raw_vega

        # Assign to bucket
        assigned = False
        for i, boundary in enumerate(bucket_boundaries):
            if m < boundary:
                buckets[bucket_names[i]] += position_vega
                assigned = True
                break
        if not assigned:
            buckets[bucket_names[-1]] += position_vega

    return buckets


# ═══════════════════════════════════════════════════════════════════════════════
# Structure system — multi-leg option structures
# ═══════════════════════════════════════════════════════════════════════════════

Leg = namedtuple("Leg", ["strike", "option_type", "quantity", "sigma", "T"])

GREEK_UNITS = {
    "delta":           "unitless (0 to 1)",
    "cash_delta":      "$ per $1 spot move",
    "dual_delta":      "$/share per $1 strike",
    "vega":            "$/share per 1pp IV",
    "theta":           "$/share per trading day",
    "rho":             "$/share per 1pp rate",
    "gamma":           "delta per $1 spot",
    "cash_gamma_1pct": "$ change in cash delta from 1% spot move",
    "dual_gamma":      "d(dual_delta) per $1 strike",
    "vanna":           "d(delta) per 1pp IV",
    "volga":           "d(vega) per 1pp IV",
    "charm":           "d(delta) per trading day",
}


class Structure:
    """A multi-leg option structure. Each Leg carries its own strike, type, quantity, sigma, and T."""

    def __init__(self, legs: List, name: str = ""):
        self.legs = legs
        self.name = name

    def payoff(self, S_range: List[float]) -> List[float]:
        """Intrinsic payoff at expiry per share ($) across a range of spot prices."""
        total = [0.0] * len(S_range)
        for leg in self.legs:
            for i, s in enumerate(S_range):
                if leg.option_type == "call":
                    total[i] += leg.quantity * max(s - leg.strike, 0.0)
                else:
                    total[i] += leg.quantity * max(leg.strike - s, 0.0)
        return total

    def price(self, S: float, r: float, q: float = 0.0, multiplier: int = 100) -> float:
        """Net premium ($, scaled by multiplier). Positive = debit, negative = credit."""
        total = 0.0
        for leg in self.legs:
            total += leg.quantity * bs_price(S, leg.strike, leg.T, r, leg.sigma, leg.option_type, q)
        return total * multiplier

    def greeks(self, S: float, r: float, q: float = 0.0, multiplier: int = 100) -> Dict[str, float]:
        """Aggregate Greeks across all legs (same units as bs_greeks)."""
        agg: Optional[Dict[str, float]] = None
        for leg in self.legs:
            g = bs_greeks(S, leg.strike, leg.T, r, leg.sigma, leg.option_type, q, multiplier)
            if agg is None:
                agg = {k: v * leg.quantity for k, v in g.items()}
            else:
                for k, v in g.items():
                    agg[k] += v * leg.quantity
        return agg if agg is not None else {}

    def pnl(self, S_range: List[float], S: float, r: float,
            q: float = 0.0, multiplier: int = 100) -> List[float]:
        """P&L at expiry ($) = payoff * multiplier - premium paid."""
        payoffs = self.payoff(S_range)
        premium = self.price(S, r, q, multiplier)
        return [p * multiplier - premium for p in payoffs]

    def summary(self, S: float, r: float, q: float = 0.0, multiplier: int = 100) -> str:
        """Return text summary of the structure: legs, price, Greeks."""
        lines = []
        lines.append("=" * 64)
        lines.append(f"  {self.name}  (multiplier={multiplier})")
        lines.append("=" * 64)
        lines.append("  Legs:")
        for leg in self.legs:
            sign = "Long" if leg.quantity > 0 else "Short"
            lines.append(
                f"    {sign} {abs(leg.quantity)} {leg.option_type} "
                f"K=${leg.strike} sigma={leg.sigma:.2%} T={leg.T:.4f}yr"
            )

        premium = self.price(S, r, q, multiplier)
        lines.append(f"  Net premium: ${premium:+.4f}")

        g = self.greeks(S, r, q, multiplier)
        lines.append("  Greeks:")
        for name, val in g.items():
            unit = GREEK_UNITS.get(name, "")
            lines.append(f"    {name:>16s}: {val:+.6f}  [{unit}]")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# 18 Structure factory functions
# ═══════════════════════════════════════════════════════════════════════════════

def synthetic(K: float, sigma: float, T: float) -> Structure:
    """Long synthetic: long call + short put at K."""
    return Structure([
        Leg(K, "call", +1, sigma, T),
        Leg(K, "put", -1, sigma, T),
    ], name="Synthetic (Long)")


def straddle(K: float, sigma: float, T: float) -> Structure:
    """Long straddle: long call + long put at K."""
    return Structure([
        Leg(K, "call", +1, sigma, T),
        Leg(K, "put", +1, sigma, T),
    ], name="Straddle (Long)")


def strangle(K1: float, K2: float, sigma1: float, sigma2: float, T: float) -> Structure:
    """Long strangle: long put at K1 + long call at K2 (K1 < K2)."""
    return Structure([
        Leg(K1, "put", +1, sigma1, T),
        Leg(K2, "call", +1, sigma2, T),
    ], name="Strangle (Long)")


def call_spread(K1: float, K2: float, sigma1: float, sigma2: float, T: float) -> Structure:
    """Bull call spread: long call at K1 + short call at K2 (K1 < K2)."""
    return Structure([
        Leg(K1, "call", +1, sigma1, T),
        Leg(K2, "call", -1, sigma2, T),
    ], name="Call Spread (Bull)")


def put_spread(K1: float, K2: float, sigma1: float, sigma2: float, T: float) -> Structure:
    """Bear put spread: long put at K2 + short put at K1 (K1 < K2)."""
    return Structure([
        Leg(K2, "put", +1, sigma2, T),
        Leg(K1, "put", -1, sigma1, T),
    ], name="Put Spread (Bear)")


def risk_reversal_put_over(K1: float, K2: float, sigma1: float, sigma2: float,
                           T: float) -> Structure:
    """Put-over risk reversal: long put at K1 + short call at K2 (K1 < K2)."""
    return Structure([
        Leg(K1, "put", +1, sigma1, T),
        Leg(K2, "call", -1, sigma2, T),
    ], name="Risk Reversal (Put-Over)")


def risk_reversal_call_over(K1: float, K2: float, sigma1: float, sigma2: float,
                            T: float) -> Structure:
    """Call-over risk reversal: short put at K1 + long call at K2 (K1 < K2)."""
    return Structure([
        Leg(K1, "put", -1, sigma1, T),
        Leg(K2, "call", +1, sigma2, T),
    ], name="Risk Reversal (Call-Over)")


def call_butterfly(K1: float, K2: float, K3: float,
                   sigma1: float, sigma2: float, sigma3: float, T: float) -> Structure:
    """Call butterfly: long K1 + short 2x K2 + long K3 calls (equal spacing)."""
    return Structure([
        Leg(K1, "call", +1, sigma1, T),
        Leg(K2, "call", -2, sigma2, T),
        Leg(K3, "call", +1, sigma3, T),
    ], name="Call Butterfly")


def put_butterfly(K1: float, K2: float, K3: float,
                  sigma1: float, sigma2: float, sigma3: float, T: float) -> Structure:
    """Put butterfly: long K1 + short 2x K2 + long K3 puts (equal spacing)."""
    return Structure([
        Leg(K1, "put", +1, sigma1, T),
        Leg(K2, "put", -2, sigma2, T),
        Leg(K3, "put", +1, sigma3, T),
    ], name="Put Butterfly")


def iron_butterfly(K1: float, K2: float, K3: float,
                   sigma1: float, sigma2_c: float, sigma2_p: float, sigma3: float,
                   T: float) -> Structure:
    """Iron butterfly: long put K1 + short put K2 + short call K2 + long call K3."""
    return Structure([
        Leg(K1, "put", +1, sigma1, T),
        Leg(K2, "put", -1, sigma2_p, T),
        Leg(K2, "call", -1, sigma2_c, T),
        Leg(K3, "call", +1, sigma3, T),
    ], name="Iron Butterfly")


def call_condor(K1: float, K2: float, K3: float, K4: float,
                sigma1: float, sigma2: float, sigma3: float, sigma4: float,
                T: float) -> Structure:
    """Call condor: long K1 + short K2 + short K3 + long K4 calls."""
    return Structure([
        Leg(K1, "call", +1, sigma1, T),
        Leg(K2, "call", -1, sigma2, T),
        Leg(K3, "call", -1, sigma3, T),
        Leg(K4, "call", +1, sigma4, T),
    ], name="Call Condor")


def put_condor(K1: float, K2: float, K3: float, K4: float,
               sigma1: float, sigma2: float, sigma3: float, sigma4: float,
               T: float) -> Structure:
    """Put condor: long K1 + short K2 + short K3 + long K4 puts."""
    return Structure([
        Leg(K1, "put", +1, sigma1, T),
        Leg(K2, "put", -1, sigma2, T),
        Leg(K3, "put", -1, sigma3, T),
        Leg(K4, "put", +1, sigma4, T),
    ], name="Put Condor")


def iron_condor(K1: float, K2: float, K3: float, K4: float,
                sigma1: float, sigma2: float, sigma3: float, sigma4: float,
                T: float) -> Structure:
    """Iron condor: long put K1 + short put K2 + short call K3 + long call K4."""
    return Structure([
        Leg(K1, "put", +1, sigma1, T),
        Leg(K2, "put", -1, sigma2, T),
        Leg(K3, "call", -1, sigma3, T),
        Leg(K4, "call", +1, sigma4, T),
    ], name="Iron Condor")


def call_ladder(K1: float, K2: float, K3: float,
                sigma1: float, sigma2: float, sigma3: float, T: float) -> Structure:
    """Call ladder: long call K1 + short call K2 + short call K3 (K1 < K2 < K3)."""
    return Structure([
        Leg(K1, "call", +1, sigma1, T),
        Leg(K2, "call", -1, sigma2, T),
        Leg(K3, "call", -1, sigma3, T),
    ], name="Call Ladder")


def call_strip(K1: float, K2: float, K3: float,
               sigma1: float, sigma2: float, sigma3: float, T: float) -> Structure:
    """Call strip: long call K1 + long call K2 + long call K3 (K1 < K2 < K3)."""
    return Structure([
        Leg(K1, "call", +1, sigma1, T),
        Leg(K2, "call", +1, sigma2, T),
        Leg(K3, "call", +1, sigma3, T),
    ], name="Call Strip")


def put_strip(K1: float, K2: float, K3: float,
              sigma1: float, sigma2: float, sigma3: float, T: float) -> Structure:
    """Put strip: long put K1 + long put K2 + long put K3 (K1 < K2 < K3)."""
    return Structure([
        Leg(K1, "put", +1, sigma1, T),
        Leg(K2, "put", +1, sigma2, T),
        Leg(K3, "put", +1, sigma3, T),
    ], name="Put Strip")


def box(K1: float, K2: float, sigma1: float, sigma2: float, T: float) -> Structure:
    """Box spread: bull call spread + bear put spread at K1, K2."""
    return Structure([
        Leg(K1, "call", +1, sigma1, T),
        Leg(K2, "call", -1, sigma2, T),
        Leg(K2, "put", +1, sigma2, T),
        Leg(K1, "put", -1, sigma1, T),
    ], name="Box Spread")


def calendar(K: float, sigma_near: float, sigma_far: float,
             T_near: float, T_far: float, option_type: str = "call") -> Structure:
    """Long calendar: short near-dated + long far-dated at same strike K."""
    return Structure([
        Leg(K, option_type, -1, sigma_near, T_near),
        Leg(K, option_type, +1, sigma_far, T_far),
    ], name=f"Calendar ({option_type.title()})")
