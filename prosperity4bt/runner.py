import os
import traceback
from contextlib import redirect_stdout
from io import StringIO

from tqdm import tqdm

from prosperity4bt.data import BacktestData, read_day_data
from prosperity4bt.datamodel import (
    ConversionObservation,
    Listing,
    Observation,
    Order,
    OrderDepth,
    Symbol,
    Trade,
    TradingState,
)
from prosperity4bt.file_reader import FileReader
from prosperity4bt.models import (
    ActivityLogRow,
    BacktestMetrics,
    BacktestResult,
    MarketTrade,
    SandboxLogRow,
    TradeMatchingMode,
    TradeRow,
)
from prosperity4bt.tee import Tee


def prepare_state(state: TradingState, data: BacktestData) -> None:
    for product in data.products:
        order_depth = OrderDepth()
        row = data.prices[state.timestamp][product]

        for price, volume in zip(row.bid_prices, row.bid_volumes):
            order_depth.buy_orders[price] = volume

        for price, volume in zip(row.ask_prices, row.ask_volumes):
            order_depth.sell_orders[price] = -volume

        state.order_depths[product] = order_depth
        state.listings[product] = Listing(product, product, 1)

    observation_row = data.observations.get(state.timestamp)

    if observation_row is None:
        state.observations = Observation({}, {})
    else:
        # Build conversion observations dynamically from whatever fields exist
        conversion_fields = {}
        conversion_keys = {"bidPrice", "askPrice", "transportFees", "exportTariff", "importTariff"}
        other_fields = {}

        for key, value in observation_row.fields.items():
            if key in conversion_keys:
                conversion_fields[key] = value
            else:
                other_fields[key] = value

        # If we have conversion-related fields, create a ConversionObservation
        # Attach it to any product that has observation data
        conversion_observations: dict[str, ConversionObservation] = {}
        if conversion_fields:
            # Include all fields in the conversion observation
            all_obs_fields = {**conversion_fields, **other_fields}
            conv_obs = ConversionObservation(**all_obs_fields)

            # Try to determine which product this applies to.
            # In the absence of a known product, attach to all products that have limits
            # but aren't in the standard price data, or to all products.
            # For now, attach to all products - the trader can pick what it needs.
            for product in data.products:
                conversion_observations[product] = conv_obs

        plain_values: dict[str, int] = {}
        for key, value in other_fields.items():
            if not conversion_fields:
                # If no conversion fields, put everything in plain values
                plain_values[key] = int(value)

        state.observations = Observation(
            plainValueObservations=plain_values,
            conversionObservations=conversion_observations,
        )


def type_check_orders(orders: dict[Symbol, list[Order]]) -> None:
    for key, value in orders.items():
        if not isinstance(key, str):
            raise ValueError(f"Orders key '{key}' is of type {type(key)}, expected a str")

        for order in value:
            if not isinstance(order.symbol, str):
                raise ValueError(f"Order symbol of '{order}' is of type {type(order.symbol)}, expected a str")

            if not isinstance(order.price, int):
                raise ValueError(f"Order price of '{order}' is of type {type(order.price)}, expected an int")

            if not isinstance(order.quantity, int):
                raise ValueError(f"Order quantity of '{order}' is of type {type(order.quantity)}, expected an int")


def create_activity_logs(
    state: TradingState,
    data: BacktestData,
    result: BacktestResult,
) -> None:
    for product in data.products:
        row = data.prices[state.timestamp][product]

        product_profit_loss = data.profit_loss[product]

        position = state.position.get(product, 0)
        if position != 0:
            product_profit_loss += position * row.mid_price

        bid_prices_len = len(row.bid_prices)
        bid_volumes_len = len(row.bid_volumes)
        ask_prices_len = len(row.ask_prices)
        ask_volumes_len = len(row.ask_volumes)

        columns = [
            result.day_num,
            state.timestamp,
            product,
            row.bid_prices[0] if bid_prices_len > 0 else "",
            row.bid_volumes[0] if bid_volumes_len > 0 else "",
            row.bid_prices[1] if bid_prices_len > 1 else "",
            row.bid_volumes[1] if bid_volumes_len > 1 else "",
            row.bid_prices[2] if bid_prices_len > 2 else "",
            row.bid_volumes[2] if bid_volumes_len > 2 else "",
            row.ask_prices[0] if ask_prices_len > 0 else "",
            row.ask_volumes[0] if ask_volumes_len > 0 else "",
            row.ask_prices[1] if ask_prices_len > 1 else "",
            row.ask_volumes[1] if ask_volumes_len > 1 else "",
            row.ask_prices[2] if ask_prices_len > 2 else "",
            row.ask_volumes[2] if ask_volumes_len > 2 else "",
            row.mid_price,
            product_profit_loss,
        ]

        result.activity_logs.append(ActivityLogRow(columns))


def enforce_limits(
    state: TradingState,
    data: BacktestData,
    orders: dict[Symbol, list[Order]],
    sandbox_row: SandboxLogRow,
    verbose: bool = False,
) -> None:
    """Enforce position limits by cancelling worst-priced orders first.

    Instead of cancelling ALL orders for a product when limits are exceeded,
    this removes orders one at a time (worst price first) until the remaining
    orders fit within position limits.
    """
    sandbox_log_lines = []
    for product in data.products:
        product_orders = orders.get(product, [])
        if not product_orders:
            continue

        limit = data.limits.get(product)
        if limit is None:
            continue

        product_position = state.position.get(product, 0)

        # Separate buy and sell orders
        buy_orders = [o for o in product_orders if o.quantity > 0]
        sell_orders = [o for o in product_orders if o.quantity < 0]

        total_long = sum(o.quantity for o in buy_orders)
        total_short = sum(abs(o.quantity) for o in sell_orders)

        # Cancel worst buy orders (highest price first - those are worst for buyer)
        if product_position + total_long > limit:
            buy_orders.sort(key=lambda o: o.price, reverse=True)
            while buy_orders and product_position + total_long > limit:
                removed = buy_orders.pop()
                total_long -= removed.quantity
                sandbox_log_lines.append(
                    f"Cancelled buy order {removed} for {product}: would exceed limit of {limit}"
                )

        # Cancel worst sell orders (lowest price first - those are worst for seller)
        if product_position - total_short < -limit:
            sell_orders.sort(key=lambda o: o.price)
            while sell_orders and product_position - total_short < -limit:
                removed = sell_orders.pop()
                total_short -= abs(removed.quantity)
                sandbox_log_lines.append(
                    f"Cancelled sell order {removed} for {product}: would exceed limit of {limit}"
                )

        orders[product] = buy_orders + sell_orders

    if len(sandbox_log_lines) > 0:
        sandbox_row.sandbox_log += "\n" + "\n".join(sandbox_log_lines)
        if verbose:
            for line in sandbox_log_lines:
                print(f"  [LIMIT] {line}")


def process_conversions(
    state: TradingState,
    data: BacktestData,
    conversions: int,
    product: str,
    sandbox_row: SandboxLogRow,
    verbose: bool = False,
) -> None:
    """Process conversion orders.

    A positive conversion means buying from the foreign market:
      cost = askPrice + importTariff + transportFees (per unit)
    A negative conversion means selling to the foreign market:
      revenue = bidPrice - exportTariff - transportFees (per unit)
    """
    if conversions == 0:
        return

    conv_obs = state.observations.conversionObservations.get(product)
    if conv_obs is None:
        sandbox_row.sandbox_log += f"\nNo conversion observation for {product}, skipping conversion"
        return

    bid_price = getattr(conv_obs, "bidPrice", 0)
    ask_price = getattr(conv_obs, "askPrice", 0)
    transport_fees = getattr(conv_obs, "transportFees", 0)
    export_tariff = getattr(conv_obs, "exportTariff", 0)
    import_tariff = getattr(conv_obs, "importTariff", 0)

    limit = data.limits.get(product, 0)
    position = state.position.get(product, 0)

    if conversions > 0:
        # Buying from foreign market
        max_buy = limit - position
        actual = min(conversions, max_buy)
        if actual <= 0:
            sandbox_row.sandbox_log += f"\nConversion buy for {product} rejected: position limit"
            return

        cost_per_unit = ask_price + import_tariff + transport_fees
        total_cost = cost_per_unit * actual

        state.position[product] = position + actual
        data.profit_loss[product] -= total_cost

        if verbose:
            print(f"  [CONV] Bought {actual} {product} @ {cost_per_unit:.2f}/unit (total cost: {total_cost:.2f})")

    else:
        # Selling to foreign market
        max_sell = limit + position
        actual = min(abs(conversions), max_sell)
        if actual <= 0:
            sandbox_row.sandbox_log += f"\nConversion sell for {product} rejected: position limit"
            return

        revenue_per_unit = bid_price - export_tariff - transport_fees
        total_revenue = revenue_per_unit * actual

        state.position[product] = position - actual
        data.profit_loss[product] += total_revenue

        if verbose:
            print(
                f"  [CONV] Sold {actual} {product} @ {revenue_per_unit:.2f}/unit (total revenue: {total_revenue:.2f})"
            )


def match_buy_order(
    state: TradingState,
    data: BacktestData,
    order: Order,
    market_trades: list[MarketTrade],
    trade_matching_mode: TradeMatchingMode,
    verbose: bool = False,
) -> list[Trade]:
    trades = []

    order_depth = state.order_depths[order.symbol]
    price_matches = sorted(price for price in order_depth.sell_orders.keys() if price <= order.price)
    for price in price_matches:
        volume = min(order.quantity, abs(order_depth.sell_orders[price]))

        trades.append(Trade(order.symbol, price, volume, "SUBMISSION", "", state.timestamp))

        state.position[order.symbol] = state.position.get(order.symbol, 0) + volume
        data.profit_loss[order.symbol] -= price * volume

        if verbose:
            print(f"  [FILL] BUY {volume} {order.symbol} @ {price} (from order book)")

        order_depth.sell_orders[price] += volume
        if order_depth.sell_orders[price] == 0:
            order_depth.sell_orders.pop(price)

        order.quantity -= volume
        if order.quantity == 0:
            return trades

    if trade_matching_mode == TradeMatchingMode.none:
        return trades

    for market_trade in market_trades:
        if (
            market_trade.sell_quantity == 0
            or market_trade.trade.price > order.price
            or (market_trade.trade.price == order.price and trade_matching_mode == TradeMatchingMode.worse)
        ):
            continue

        volume = min(order.quantity, market_trade.sell_quantity)

        trades.append(
            Trade(order.symbol, order.price, volume, "SUBMISSION", market_trade.trade.seller, state.timestamp)
        )

        state.position[order.symbol] = state.position.get(order.symbol, 0) + volume
        data.profit_loss[order.symbol] -= order.price * volume

        if verbose:
            print(f"  [FILL] BUY {volume} {order.symbol} @ {order.price} (from market trade)")

        market_trade.sell_quantity -= volume

        order.quantity -= volume
        if order.quantity == 0:
            return trades

    return trades


def match_sell_order(
    state: TradingState,
    data: BacktestData,
    order: Order,
    market_trades: list[MarketTrade],
    trade_matching_mode: TradeMatchingMode,
    verbose: bool = False,
) -> list[Trade]:
    trades = []

    order_depth = state.order_depths[order.symbol]
    price_matches = sorted((price for price in order_depth.buy_orders.keys() if price >= order.price), reverse=True)
    for price in price_matches:
        volume = min(abs(order.quantity), order_depth.buy_orders[price])

        trades.append(Trade(order.symbol, price, volume, "", "SUBMISSION", state.timestamp))

        state.position[order.symbol] = state.position.get(order.symbol, 0) - volume
        data.profit_loss[order.symbol] += price * volume

        if verbose:
            print(f"  [FILL] SELL {volume} {order.symbol} @ {price} (from order book)")

        order_depth.buy_orders[price] -= volume
        if order_depth.buy_orders[price] == 0:
            order_depth.buy_orders.pop(price)

        order.quantity += volume
        if order.quantity == 0:
            return trades

    if trade_matching_mode == TradeMatchingMode.none:
        return trades

    for market_trade in market_trades:
        if (
            market_trade.buy_quantity == 0
            or market_trade.trade.price < order.price
            or (market_trade.trade.price == order.price and trade_matching_mode == TradeMatchingMode.worse)
        ):
            continue

        volume = min(abs(order.quantity), market_trade.buy_quantity)

        trades.append(Trade(order.symbol, order.price, volume, market_trade.trade.buyer, "SUBMISSION", state.timestamp))

        state.position[order.symbol] = state.position.get(order.symbol, 0) - volume
        data.profit_loss[order.symbol] += order.price * volume

        if verbose:
            print(f"  [FILL] SELL {volume} {order.symbol} @ {order.price} (from market trade)")

        market_trade.buy_quantity -= volume

        order.quantity += volume
        if order.quantity == 0:
            return trades

    return trades


def match_order(
    state: TradingState,
    data: BacktestData,
    order: Order,
    market_trades: list[MarketTrade],
    trade_matching_mode: TradeMatchingMode,
    verbose: bool = False,
) -> list[Trade]:
    if order.quantity > 0:
        return match_buy_order(state, data, order, market_trades, trade_matching_mode, verbose)
    elif order.quantity < 0:
        return match_sell_order(state, data, order, market_trades, trade_matching_mode, verbose)
    else:
        return []


def match_orders(
    state: TradingState,
    data: BacktestData,
    orders: dict[Symbol, list[Order]],
    result: BacktestResult,
    trade_matching_mode: TradeMatchingMode,
    metrics: BacktestMetrics | None = None,
    verbose: bool = False,
) -> None:
    market_trades = {
        product: [MarketTrade(t, t.quantity, t.quantity) for t in trades]
        for product, trades in data.trades[state.timestamp].items()
    }

    for product in data.products:
        new_trades = []

        product_orders = orders.get(product, [])

        if metrics is not None:
            metrics.orders_submitted += len(product_orders)

        for order in product_orders:
            order_trades = match_order(
                state,
                data,
                order,
                market_trades.get(product, []),
                trade_matching_mode,
                verbose,
            )
            new_trades.extend(order_trades)

            if metrics is not None and len(order_trades) > 0:
                metrics.orders_filled += 1

        if len(new_trades) > 0:
            state.own_trades[product] = new_trades
            result.trades.extend([TradeRow(trade) for trade in new_trades])

            if metrics is not None:
                metrics.trades_by_product[product] = metrics.trades_by_product.get(product, 0) + len(new_trades)
                metrics.volume_by_product[product] = metrics.volume_by_product.get(product, 0) + sum(
                    t.quantity for t in new_trades
                )

    for product, trades in market_trades.items():
        for trade in trades:
            trade.trade.quantity = min(trade.buy_quantity, trade.sell_quantity)

        remaining_market_trades = [t.trade for t in trades if t.trade.quantity > 0]

        state.market_trades[product] = remaining_market_trades
        result.trades.extend([TradeRow(trade) for trade in remaining_market_trades])


def run_backtest(
    trader: object,
    file_reader: FileReader,
    round_num: int,
    day_num: int,
    print_output: bool,
    trade_matching_mode: TradeMatchingMode,
    no_names: bool,
    show_progress_bar: bool,
    verbose: bool = False,
    limits: dict[str, int] | None = None,
) -> tuple[BacktestResult, BacktestMetrics]:
    data = read_day_data(file_reader, round_num, day_num, no_names, limits or {})

    os.environ["PROSPERITY4BT_ROUND"] = str(round_num)
    os.environ["PROSPERITY4BT_DAY"] = str(day_num)

    trader_data = ""
    state = TradingState(
        traderData=trader_data,
        timestamp=0,
        listings={},
        order_depths={},
        own_trades={},
        market_trades={},
        position={},
        observations=Observation({}, {}),
    )

    result = BacktestResult(
        round_num=data.round_num,
        day_num=data.day_num,
        sandbox_logs=[],
        activity_logs=[],
        trades=[],
    )

    metrics = BacktestMetrics()

    timestamps = sorted(data.prices.keys())
    timestamps_iterator = tqdm(timestamps, ascii=True) if show_progress_bar else timestamps

    for timestamp in timestamps_iterator:
        state.timestamp = timestamp
        state.traderData = trader_data

        prepare_state(state, data)

        stdout = StringIO()

        try:
            if print_output:
                with Tee(stdout):
                    orders, conversions, trader_data = trader.run(state)  # type: ignore[attr-defined]
            else:
                with redirect_stdout(stdout):
                    orders, conversions, trader_data = trader.run(state)  # type: ignore[attr-defined]
        except Exception:
            error_msg = traceback.format_exc()
            sandbox_row = SandboxLogRow(
                timestamp=timestamp,
                sandbox_log=f"TRADER ERROR:\n{error_msg}",
                lambda_log=stdout.getvalue().rstrip(),
            )
            result.sandbox_logs.append(sandbox_row)

            if print_output or verbose:
                print(f"  [ERROR] Trader exception at t={timestamp}:\n{error_msg}")

            # Skip this timestamp - no orders processed
            create_activity_logs(state, data, result)
            continue

        sandbox_row = SandboxLogRow(
            timestamp=timestamp,
            sandbox_log="",
            lambda_log=stdout.getvalue().rstrip(),
        )

        result.sandbox_logs.append(sandbox_row)

        type_check_orders(orders)
        create_activity_logs(state, data, result)
        enforce_limits(state, data, orders, sandbox_row, verbose)

        # Process conversions if the trader returned any
        if isinstance(conversions, int) and conversions != 0:
            # Find the product with conversion observations
            for product in state.observations.conversionObservations:
                process_conversions(state, data, conversions, product, sandbox_row, verbose)
                break
        elif isinstance(conversions, dict):
            for product, conv_amount in conversions.items():
                if isinstance(conv_amount, int) and conv_amount != 0:
                    process_conversions(state, data, conv_amount, product, sandbox_row, verbose)

        match_orders(state, data, orders, result, trade_matching_mode, metrics, verbose)

        # Update PnL tracking for metrics
        total_pnl = 0.0
        for product in data.products:
            pnl = data.profit_loss[product]
            position = state.position.get(product, 0)
            if position != 0:
                row = data.prices[timestamp][product]
                pnl += position * row.mid_price
            total_pnl += pnl

        metrics.pnl_over_time.append((timestamp, total_pnl))

        if total_pnl > metrics.peak_pnl:
            metrics.peak_pnl = total_pnl
        drawdown = metrics.peak_pnl - total_pnl
        if drawdown > metrics.max_drawdown:
            metrics.max_drawdown = drawdown

    # Finalize metrics
    if metrics.pnl_over_time:
        metrics.total_pnl = metrics.pnl_over_time[-1][1]

    metrics.total_trades = sum(metrics.trades_by_product.values())
    metrics.total_volume = sum(metrics.volume_by_product.values())

    for product in data.products:
        pnl = data.profit_loss[product]
        position = state.position.get(product, 0)
        if position != 0:
            last_row = data.prices[timestamps[-1]][product]
            pnl += position * last_row.mid_price
        metrics.pnl_by_product[product] = pnl

    return result, metrics
