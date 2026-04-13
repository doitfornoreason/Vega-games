import Highcharts from 'highcharts';
import { ReactNode } from 'react';
import { useStore } from '../../store.ts';
import { Chart } from './Chart.tsx';

const COLORS = [
  '#2ecc71', '#e74c3c', '#3498db', '#f39c12', '#9b59b6',
  '#1abc9c', '#e67e22', '#34495e', '#16a085', '#c0392b',
];

export function ComparisonPnlChart(): ReactNode {
  const comparisonAlgorithms = useStore(state => state.comparisonAlgorithms);
  const algorithm = useStore(state => state.algorithm);

  if (comparisonAlgorithms.length === 0) {
    return null;
  }

  const series: Highcharts.SeriesOptionsType[] = comparisonAlgorithms.map((algo, i) => ({
    type: 'line' as const,
    name: algo.name,
    data: algo.pnlData,
    color: COLORS[i % COLORS.length],
  }));

  return (
    <Chart
      title="PnL Comparison"
      series={series}
      dayBoundaries={algorithm?.dayBoundaries}
    />
  );
}
