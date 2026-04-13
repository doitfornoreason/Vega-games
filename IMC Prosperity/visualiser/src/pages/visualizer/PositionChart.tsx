import Highcharts from 'highcharts';
import { ReactNode } from 'react';
import { useStore } from '../../store.ts';
import { getLimit } from '../../utils/limits.ts';
import { Chart } from './Chart.tsx';

export interface PositionChartProps {
  symbols: string[];
}

export function PositionChart({ symbols }: PositionChartProps): ReactNode {
  const algorithm = useStore(state => state.algorithm)!;

  const limits: Record<string, number> = {};
  for (const symbol of symbols) {
    limits[symbol] = getLimit(algorithm, symbol);
  }

  const data: Record<string, [number, number][]> = {};
  for (const symbol of symbols) {
    data[symbol] = [];
  }

  for (const row of algorithm.data) {
    for (const symbol of symbols) {
      const position = row.state.position[symbol] || 0;
      data[symbol].push([row.state.timestamp, (position / limits[symbol]) * 100]);
    }
  }

  const series: Highcharts.SeriesOptionsType[] = symbols.map((symbol, i) => ({
    type: 'line',
    name: symbol,
    data: data[symbol],

    // We offset the position color by 1 to make it line up with the colors in the profit / loss chart,
    // while keeping the "Total" line in the profit / loss chart the same color at all times
    colorIndex: (i + 1) % 10,
  }));

  return <Chart title="Positions (% of limit)" series={series} min={-100} max={100} dayBoundaries={algorithm.dayBoundaries} />;
}
