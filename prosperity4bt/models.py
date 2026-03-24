from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import orjson

from prosperity4bt.datamodel import Trade


@dataclass
class SandboxLogRow:
    timestamp: int
    sandbox_log: str
    lambda_log: str

    def with_offset(self, timestamp_offset: int) -> "SandboxLogRow":
        return SandboxLogRow(
            self.timestamp + timestamp_offset,
            self.sandbox_log,
            self.lambda_log.replace(f"[[{self.timestamp},", f"[[{self.timestamp + timestamp_offset},"),
        )

    def __str__(self) -> str:
        return orjson.dumps(
            {
                "sandboxLog": self.sandbox_log,
                "lambdaLog": self.lambda_log,
                "timestamp": self.timestamp,
            },
            option=orjson.OPT_APPEND_NEWLINE | orjson.OPT_INDENT_2,
        ).decode("utf-8")


@dataclass
class ActivityLogRow:
    columns: list[Any]

    @property
    def timestamp(self) -> int:
        return self.columns[1]

    def with_offset(self, timestamp_offset: int, profit_loss_offset: float) -> "ActivityLogRow":
        new_columns = self.columns[:]
        new_columns[1] += timestamp_offset
        new_columns[-1] += profit_loss_offset

        return ActivityLogRow(new_columns)

    def __str__(self) -> str:
        return ";".join(map(str, self.columns))


@dataclass
class TradeRow:
    trade: Trade

    @property
    def timestamp(self) -> int:
        return self.trade.timestamp

    def with_offset(self, timestamp_offset: int) -> "TradeRow":
        return TradeRow(
            Trade(
                self.trade.symbol,
                self.trade.price,
                self.trade.quantity,
                self.trade.buyer,
                self.trade.seller,
                self.trade.timestamp + timestamp_offset,
            )
        )

    def __str__(self) -> str:
        return (
            "  "
            + f"""
  {{
    "timestamp": {self.trade.timestamp},
    "buyer": "{self.trade.buyer}",
    "seller": "{self.trade.seller}",
    "symbol": "{self.trade.symbol}",
    "currency": "SEASHELLS",
    "price": {self.trade.price},
    "quantity": {self.trade.quantity},
  }}
        """.strip()
        )


@dataclass
class BacktestResult:
    round_num: int
    day_num: int

    sandbox_logs: list[SandboxLogRow]
    activity_logs: list[ActivityLogRow]
    trades: list[TradeRow]


@dataclass
class MarketTrade:
    trade: Trade
    buy_quantity: int
    sell_quantity: int


class TradeMatchingMode(str, Enum):
    all = "all"
    worse = "worse"
    none = "none"


@dataclass
class BacktestMetrics:
    """Performance metrics computed after a backtest run."""

    total_pnl: float = 0.0
    pnl_by_product: dict[str, float] = field(default_factory=dict)
    total_trades: int = 0
    trades_by_product: dict[str, int] = field(default_factory=dict)
    total_volume: int = 0
    volume_by_product: dict[str, int] = field(default_factory=dict)
    orders_submitted: int = 0
    orders_filled: int = 0
    max_drawdown: float = 0.0
    peak_pnl: float = 0.0
    pnl_over_time: list[tuple[int, float]] = field(default_factory=list)

    @property
    def fill_rate(self) -> float:
        if self.orders_submitted == 0:
            return 0.0
        return self.orders_filled / self.orders_submitted

    def summary(self) -> str:
        lines = [
            "--- Performance Metrics ---",
            f"Total PnL:       {self.total_pnl:>12,.0f}",
            f"Max Drawdown:    {self.max_drawdown:>12,.0f}",
            f"Total Trades:    {self.total_trades:>12,d}",
            f"Total Volume:    {self.total_volume:>12,d}",
            f"Fill Rate:       {self.fill_rate:>11.1%}",
            "",
            f"{'Product':<30s} {'PnL':>10s} {'Trades':>8s} {'Volume':>10s}",
            "-" * 62,
        ]

        all_products = sorted(set(self.pnl_by_product.keys()) | set(self.trades_by_product.keys()))
        for product in all_products:
            pnl = self.pnl_by_product.get(product, 0.0)
            trades = self.trades_by_product.get(product, 0)
            volume = self.volume_by_product.get(product, 0)
            lines.append(f"{product:<30s} {pnl:>10,.0f} {trades:>8,d} {volume:>10,d}")

        lines.append("-" * 62)
        return "\n".join(lines)
