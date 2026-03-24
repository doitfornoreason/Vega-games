import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from prosperity4bt.datamodel import Symbol, Trade
from prosperity4bt.file_reader import FileReader

# Position limits per product.
# Empty by default - will be populated when Prosperity 4 products are announced.
# Can be overridden via --limits-file CLI flag or limits.json in the data directory.
LIMITS: dict[str, int] = {}


def load_limits_from_file(path: Path) -> dict[str, int]:
    """Load position limits from a JSON file.

    Expected format: {"PRODUCT_NAME": limit, ...}
    Example: {"ROSES": 60, "CHOCOLATE": 250}
    """
    with open(path, encoding="utf-8") as f:
        limits = json.load(f)
    if not isinstance(limits, dict):
        raise ValueError(f"limits file must contain a JSON object, got {type(limits).__name__}")
    return {str(k): int(v) for k, v in limits.items()}


def discover_limits(file_reader: FileReader, explicit_path: Optional[Path] = None) -> dict[str, int]:
    """Discover position limits from various sources.

    Priority order:
    1. Explicit --limits-file CLI argument
    2. limits.json in the data directory (if using FileSystemReader)
    3. Built-in LIMITS dict
    """
    if explicit_path is not None:
        return load_limits_from_file(explicit_path)

    # Try limits.json in data directory
    with file_reader.file(["limits.json"]) as f:
        if f is not None:
            return load_limits_from_file(f)

    return LIMITS.copy()


@dataclass
class PriceRow:
    day: int
    timestamp: int
    product: Symbol
    bid_prices: list[int]
    bid_volumes: list[int]
    ask_prices: list[int]
    ask_volumes: list[int]
    mid_price: float
    profit_loss: float


def get_column_values(columns: list[str], indices: list[int]) -> list[int]:
    values = []

    for index in indices:
        if index >= len(columns):
            break
        value = columns[index]
        if value == "":
            break

        values.append(int(value))

    return values


@dataclass
class ObservationRow:
    """Dynamic observation row - fields are determined by CSV headers."""

    timestamp: int
    fields: dict[str, float] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name in ("timestamp", "fields"):
            raise AttributeError(name)
        try:
            return self.fields[name]
        except KeyError:
            raise AttributeError(f"ObservationRow has no field '{name}'")


@dataclass
class BacktestData:
    round_num: int
    day_num: int

    prices: dict[int, dict[Symbol, PriceRow]]
    trades: dict[int, dict[Symbol, list[Trade]]]
    observations: dict[int, ObservationRow]
    observation_headers: list[str]
    products: list[Symbol]
    profit_loss: dict[Symbol, float]
    limits: dict[str, int]


def create_backtest_data(
    round_num: int,
    day_num: int,
    prices: list[PriceRow],
    trades: list[Trade],
    observations: list[ObservationRow],
    observation_headers: list[str],
    limits: dict[str, int],
) -> BacktestData:
    prices_by_timestamp: dict[int, dict[Symbol, PriceRow]] = defaultdict(dict)
    for row in prices:
        prices_by_timestamp[row.timestamp][row.product] = row

    trades_by_timestamp: dict[int, dict[Symbol, list[Trade]]] = defaultdict(lambda: defaultdict(list))
    for trade in trades:
        trades_by_timestamp[trade.timestamp][trade.symbol].append(trade)

    products = sorted(set(row.product for row in prices))
    profit_loss = {product: 0.0 for product in products}

    observations_by_timestamp = {row.timestamp: row for row in observations}

    # Auto-discover limits from data if not explicitly provided
    if not limits:
        # Use a default limit of 50 for any product found in data
        limits = {product: 50 for product in products}
        print(f"Warning: no position limits configured, using default limit of 50 for all {len(products)} products")

    return BacktestData(
        round_num=round_num,
        day_num=day_num,
        prices=prices_by_timestamp,
        trades=trades_by_timestamp,
        observations=observations_by_timestamp,
        observation_headers=observation_headers,
        products=products,
        profit_loss=profit_loss,
        limits=limits,
    )


def has_day_data(file_reader: FileReader, round_num: int, day_num: int) -> bool:
    with file_reader.file([f"round{round_num}", f"prices_round_{round_num}_day_{day_num}.csv"]) as file:
        return file is not None


def read_day_data(
    file_reader: FileReader, round_num: int, day_num: int, no_names: bool, limits: dict[str, int]
) -> BacktestData:
    prices = []
    with file_reader.file([f"round{round_num}", f"prices_round_{round_num}_day_{day_num}.csv"]) as file:
        if file is None:
            raise ValueError(f"Prices data is not available for round {round_num} day {day_num}")

        for line in file.read_text(encoding="utf-8").splitlines()[1:]:
            columns = line.split(";")

            prices.append(
                PriceRow(
                    day=int(columns[0]),
                    timestamp=int(columns[1]),
                    product=columns[2],
                    bid_prices=get_column_values(columns, [3, 5, 7]),
                    bid_volumes=get_column_values(columns, [4, 6, 8]),
                    ask_prices=get_column_values(columns, [9, 11, 13]),
                    ask_volumes=get_column_values(columns, [10, 12, 14]),
                    mid_price=float(columns[15]),
                    profit_loss=float(columns[16]),
                )
            )

    trades = []
    with file_reader.file([f"round{round_num}", f"trades_round_{round_num}_day_{day_num}.csv"]) as file:
        if file is not None:
            for line in file.read_text(encoding="utf-8").splitlines()[1:]:
                columns = line.split(";")

                trades.append(
                    Trade(
                        symbol=columns[3],
                        price=int(float(columns[5])),
                        quantity=int(columns[6]),
                        buyer=columns[1],
                        seller=columns[2],
                        timestamp=int(columns[0]),
                    )
                )

    observations: list[ObservationRow] = []
    observation_headers: list[str] = []
    with file_reader.file([f"round{round_num}", f"observations_round_{round_num}_day_{day_num}.csv"]) as file:
        if file is not None:
            lines = file.read_text(encoding="utf-8").splitlines()
            if len(lines) > 0:
                # Detect delimiter (comma or semicolon)
                header_line = lines[0]
                delimiter = "," if "," in header_line else ";"
                headers = header_line.strip().split(delimiter)

                # First column is always timestamp, rest are observation fields
                observation_headers = headers[1:]

                for line in lines[1:]:
                    columns = line.strip().split(delimiter)
                    fields = {}
                    for i, header in enumerate(observation_headers):
                        if i + 1 < len(columns) and columns[i + 1] != "":
                            try:
                                fields[header] = float(columns[i + 1])
                            except ValueError:
                                pass

                    observations.append(
                        ObservationRow(
                            timestamp=int(columns[0]),
                            fields=fields,
                        )
                    )

    return create_backtest_data(round_num, day_num, prices, trades, observations, observation_headers, limits)
