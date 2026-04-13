import Highcharts from 'highcharts';
import { ReactNode, useMemo } from 'react';
import { ProsperitySymbol } from '../../models.ts';
import { useStore } from '../../store.ts';
import {
  getAskColor,
  getBidColor,
  getMarketTradeColor,
  getOwnTradeColor,
  getProfitableTradeColor,
  getUnprofitableTradeColor,
} from '../../utils/colors.ts';
import { getLimit } from '../../utils/limits.ts';
import { Chart } from './Chart.tsx';

export interface ProductPriceChartProps {
  symbol: ProsperitySymbol;
}

export function ProductPriceChart({ symbol }: ProductPriceChartProps): ReactNode {
  const algorithm = useStore(state => state.algorithm)!;
  const tradeFilters = useStore(state => state.tradeFilters);
  const featureToggles = useStore(state => state.featureToggles);
  const setHoveredTimestamp = useStore(state => state.setHoveredTimestamp);

  const { series, chartOptions } = useMemo(() => {
    const normMode = featureToggles.normalizationMode;
    const normCustom = featureToggles.normalizationCustomValue;

    // Build timestamp → midPrice lookup for normalization and P&L coloring
    const midPriceMap = new Map<number, number>();
    for (const row of algorithm.activityLogs) {
      if (row.product === symbol) {
        midPriceMap.set(row.timestamp, row.midPrice);
      }
    }

    // Normalization helper
    function normalize(value: number, timestamp: number): number {
      if (normMode === 'none') return value;
      if (normMode === 'mid') {
        const mid = midPriceMap.get(timestamp);
        return mid !== undefined ? value - mid : value;
      }
      if (normMode === 'custom' && normCustom !== null) {
        return value - normCustom;
      }
      return value;
    }

    const priceSeries: Highcharts.SeriesOptionsType[] = [
      { type: 'line', name: 'Bid 3', color: getBidColor(0.5), marker: { symbol: 'square' }, data: [] },
      { type: 'line', name: 'Bid 2', color: getBidColor(0.75), marker: { symbol: 'circle' }, data: [] },
      { type: 'line', name: 'Bid 1', color: getBidColor(1.0), marker: { symbol: 'triangle' }, data: [] },
      { type: 'line', name: 'Mid price', color: 'gray', dashStyle: 'Dash', marker: { symbol: 'diamond' }, data: [] },
      { type: 'line', name: 'Ask 1', color: getAskColor(1.0), marker: { symbol: 'triangle-down' }, data: [] },
      { type: 'line', name: 'Ask 2', color: getAskColor(0.75), marker: { symbol: 'circle' }, data: [] },
      { type: 'line', name: 'Ask 3', color: getAskColor(0.5), marker: { symbol: 'square' }, data: [] },
    ];

    for (const row of algorithm.activityLogs) {
      if (row.product !== symbol) {
        continue;
      }

      const ts = row.timestamp;
      for (let i = 0; i < row.bidPrices.length; i++) {
        (priceSeries[2 - i] as any).data.push([ts, normalize(row.bidPrices[i], ts)]);
      }

      (priceSeries[3] as any).data.push([ts, normalize(row.midPrice, ts)]);

      for (let i = 0; i < row.askPrices.length; i++) {
        (priceSeries[i + 4] as any).data.push([ts, normalize(row.askPrices[i], ts)]);
      }
    }

    // Build trade marker scatter series from algorithm.data
    const ownTradePoints: any[] = [];
    const marketTradePoints: any[] = [];
    const buyOrderPoints: any[] = [];
    const sellOrderPoints: any[] = [];

    if (algorithm.data) {
      for (const row of algorithm.data) {
        const ts = row.state.timestamp;

        // Own trades
        const ownTrades = row.state.ownTrades[symbol] || [];
        for (const trade of ownTrades) {
          if (tradeFilters) {
            const qty = Math.abs(trade.quantity);
            if (tradeFilters.minQuantity !== null && qty < tradeFilters.minQuantity) continue;
            if (tradeFilters.maxQuantity !== null && qty > tradeFilters.maxQuantity) continue;
            if (tradeFilters.hiddenTraderIds.has(trade.buyer) || tradeFilters.hiddenTraderIds.has(trade.seller))
              continue;
          }

          const isBuy = trade.buyer === 'SUBMISSION';
          const mid = midPriceMap.get(ts);
          let pointColor: string | undefined;

          // Step 1: Trade P&L coloring
          if (featureToggles.showTradePnlColoring && mid !== undefined) {
            const profitable = isBuy ? trade.price < mid : trade.price > mid;
            pointColor = profitable ? getProfitableTradeColor(0.9) : getUnprofitableTradeColor(0.9);
          }

          const point: any = {
            x: ts,
            y: normalize(trade.price, ts),
            custom: {
              buyer: trade.buyer || '(anonymous)',
              seller: trade.seller || '(anonymous)',
              quantity: trade.quantity,
              side: isBuy ? 'BUY' : 'SELL',
            },
          };

          if (pointColor) {
            point.color = pointColor;
            point.marker = { lineColor: pointColor };
          }

          ownTradePoints.push(point);
        }

        // Market trades
        const marketTrades = row.state.marketTrades[symbol] || [];
        for (const trade of marketTrades) {
          if (tradeFilters) {
            const qty = Math.abs(trade.quantity);
            if (tradeFilters.minQuantity !== null && qty < tradeFilters.minQuantity) continue;
            if (tradeFilters.maxQuantity !== null && qty > tradeFilters.maxQuantity) continue;
            if (tradeFilters.hiddenTraderIds.has(trade.buyer) || tradeFilters.hiddenTraderIds.has(trade.seller))
              continue;
          }

          marketTradePoints.push({
            x: ts,
            y: normalize(trade.price, ts),
            custom: {
              buyer: trade.buyer || '(anonymous)',
              seller: trade.seller || '(anonymous)',
              quantity: trade.quantity,
            },
          });
        }

        // Step 3: Order Visualization
        if (featureToggles.showOrderVisualization) {
          const orders = row.orders[symbol] || [];
          for (const order of orders) {
            const point = {
              x: ts,
              y: normalize(order.price, ts),
              custom: {
                quantity: order.quantity,
                side: order.quantity > 0 ? 'BUY' : 'SELL',
              },
            };
            if (order.quantity > 0) {
              buyOrderPoints.push(point);
            } else {
              sellOrderPoints.push(point);
            }
          }
        }
      }
    }

    // Trade scatter series
    const ownTradeSeries: any = {
      type: 'scatter',
      name: 'Own trades',
      color: getOwnTradeColor(0.9),
      marker: {
        symbol: 'cross',
        radius: 6,
        lineWidth: 2,
        lineColor: getOwnTradeColor(1.0),
      },
      data: ownTradePoints,
      stickyTracking: false,
      dataGrouping: { enabled: false },
      tooltip: {
        pointFormat:
          '<b>{point.custom.side}</b> {point.custom.quantity} @ {point.y}<br/>Buyer: {point.custom.buyer} | Seller: {point.custom.seller}<br/>',
      },
    };
    if (featureToggles.showTradePnlColoring) {
      ownTradeSeries.colorByPoint = true;
    }

    const tradeSeries: Highcharts.SeriesOptionsType[] = [
      ownTradeSeries,
      {
        type: 'scatter' as const,
        name: 'Market trades',
        color: getMarketTradeColor(0.7),
        marker: {
          symbol: 'circle',
          radius: 4,
        },
        data: marketTradePoints,
        stickyTracking: false,
        dataGrouping: { enabled: false },
        tooltip: {
          pointFormat:
            '<b>Market</b> {point.custom.quantity} @ {point.y}<br/>Buyer: {point.custom.buyer} | Seller: {point.custom.seller}<br/>',
        },
      },
    ];

    // Step 3: Order series (conditional)
    if (featureToggles.showOrderVisualization) {
      tradeSeries.push(
        {
          type: 'scatter' as const,
          name: 'Buy orders',
          color: getBidColor(0.7),
          marker: {
            symbol: 'triangle',
            radius: 5,
          },
          data: buyOrderPoints,
          stickyTracking: false,
          dataGrouping: { enabled: false },
          tooltip: {
            pointFormat: '<b>{point.custom.side} Order</b> {point.custom.quantity} @ {point.y}<br/>',
          },
        },
        {
          type: 'scatter' as const,
          name: 'Sell orders',
          color: getAskColor(0.7),
          marker: {
            symbol: 'triangle-down',
            radius: 5,
          },
          data: sellOrderPoints,
          stickyTracking: false,
          dataGrouping: { enabled: false },
          tooltip: {
            pointFormat: '<b>{point.custom.side} Order</b> {point.custom.quantity} @ {point.y}<br/>',
          },
        },
      );
    }

    // Step 6: Inventory Heatmap — build plotBands based on position
    let extraOptions: Highcharts.Options = {};
    if (featureToggles.showInventoryHeatmap && algorithm.data.length > 0) {
      const limit = getLimit(algorithm, symbol);
      const plotBands: Highcharts.XAxisPlotBandsOptions[] = [];

      for (let i = 0; i < algorithm.data.length; i++) {
        const row = algorithm.data[i];
        const position = row.state.position[symbol] || 0;
        if (position === 0) continue;

        const fromTs = row.state.timestamp;
        const toTs = i + 1 < algorithm.data.length ? algorithm.data[i + 1].state.timestamp : fromTs + 100;
        const intensity = Math.min(Math.abs(position) / limit, 1) * 0.15;

        plotBands.push({
          from: fromTs,
          to: toTs,
          color: position > 0 ? `rgba(46, 204, 113, ${intensity})` : `rgba(231, 76, 60, ${intensity})`,
        });
      }

      extraOptions = { xAxis: { plotBands } };
    }

    return { series: [...priceSeries, ...tradeSeries], chartOptions: extraOptions };
  }, [algorithm, symbol, tradeFilters, featureToggles]);

  // Step 7: hover handler for log viewer
  const handleHover = featureToggles.showLogViewer
    ? (timestamp: number) => setHoveredTimestamp(timestamp)
    : undefined;

  const normLabel =
    featureToggles.normalizationMode === 'mid'
      ? ' (norm: mid)'
      : featureToggles.normalizationMode === 'custom'
        ? ` (norm: ${featureToggles.normalizationCustomValue})`
        : '';

  return (
    <Chart
      title={`${symbol} - Price${normLabel}`}
      options={chartOptions}
      series={series}
      dayBoundaries={algorithm.dayBoundaries}
      onHover={handleHover}
    />
  );
}
