import Highcharts from 'highcharts';
import { ReactNode } from 'react';
import { ProsperitySymbol } from '../../models.ts';
import { useStore } from '../../store.ts';
import { Chart } from './Chart.tsx';

export interface SpreadChartProps {
  symbol: ProsperitySymbol;
}

export function SpreadChart({ symbol }: SpreadChartProps): ReactNode {
  const algorithm = useStore(state => state.algorithm)!;

  const spreadData: [number, number][] = [];

  for (const row of algorithm.activityLogs) {
    if (row.product !== symbol) {
      continue;
    }

    if (row.askPrices.length > 0 && row.bidPrices.length > 0) {
      const spread = row.askPrices[0] - row.bidPrices[0];
      spreadData.push([row.timestamp, spread]);
    }
  }

  const series: Highcharts.SeriesOptionsType[] = [
    {
      type: 'area',
      name: 'Spread',
      data: spreadData,
      color: 'rgba(52, 152, 219, 0.8)',
      fillColor: 'rgba(52, 152, 219, 0.2)',
    },
  ];

  return <Chart title={`${symbol} - Spread (Ask1 - Bid1)`} series={series} dayBoundaries={algorithm.dayBoundaries} />;
}
