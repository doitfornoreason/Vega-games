import { Center, Container, Grid, Title } from '@mantine/core';
import { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useStore } from '../../store.ts';
import { formatNumber } from '../../utils/format.ts';
import { AlgorithmSummaryCard } from './AlgorithmSummaryCard.tsx';
import { ComparisonPnlChart } from './ComparisonPnlChart.tsx';
import { ConversionPriceChart } from './ConversionPriceChart.tsx';
import { EnvironmentChart } from './EnvironmentChart.tsx';
import { FeatureToggleControls } from './FeatureToggleControls.tsx';
import { LogViewer } from './LogViewer.tsx';
import { PlainValueObservationChart } from './PlainValueObservationChart.tsx';
import { PositionChart } from './PositionChart.tsx';
import { ProductPriceChart } from './ProductPriceChart.tsx';
import { ProfitLossChart } from './ProfitLossChart.tsx';
import { SpreadChart } from './SpreadChart.tsx';
import { TimestampsCard } from './TimestampsCard.tsx';
import { TradeFilterControls } from './TradeFilterControls.tsx';
import { TransportChart } from './TransportChart.tsx';
import { VisualizerCard } from './VisualizerCard.tsx';
import { VolumeChart } from './VolumeChart.tsx';

export function VisualizerPage(): ReactNode {
  const algorithm = useStore(state => state.algorithm);
  const featureToggles = useStore(state => state.featureToggles);
  const comparisonAlgorithms = useStore(state => state.comparisonAlgorithms);

  const { search } = useLocation();

  if (algorithm === null) {
    return <Navigate to={`/${search}`} />;
  }

  const conversionProducts = new Set();
  for (const row of algorithm.data) {
    for (const product of Object.keys(row.state.observations.conversionObservations)) {
      conversionProducts.add(product);
    }
  }

  let profitLoss = 0;
  const lastTimestamp = algorithm.activityLogs[algorithm.activityLogs.length - 1].timestamp;
  for (let i = algorithm.activityLogs.length - 1; i >= 0 && algorithm.activityLogs[i].timestamp == lastTimestamp; i--) {
    profitLoss += algorithm.activityLogs[i].profitLoss;
  }

  const symbols = new Set<string>();
  const plainValueObservationSymbols = new Set<string>();

  for (let i = 0; i < algorithm.data.length; i += 1000) {
    const row = algorithm.data[i];

    for (const key of Object.keys(row.state.listings)) {
      symbols.add(key);
    }

    for (const key of Object.keys(row.state.observations.plainValueObservations)) {
      plainValueObservationSymbols.add(key);
    }
  }

  const sortedSymbols = [...symbols].sort((a, b) => a.localeCompare(b));
  const sortedPlainValueObservationSymbols = [...plainValueObservationSymbols].sort((a, b) => a.localeCompare(b));

  const symbolColumns: ReactNode[] = [];
  sortedSymbols.forEach(symbol => {
    symbolColumns.push(
      <Grid.Col key={`${symbol} - product price`} span={{ xs: 12, sm: 6 }}>
        <ProductPriceChart symbol={symbol} />
      </Grid.Col>,
    );

    symbolColumns.push(
      <Grid.Col key={`${symbol} - volume`} span={{ xs: 12, sm: 6 }}>
        <VolumeChart symbol={symbol} />
      </Grid.Col>,
    );

    // Step 2: Spread chart (conditional)
    if (featureToggles.showSpreadChart) {
      symbolColumns.push(
        <Grid.Col key={`${symbol} - spread`} span={{ xs: 12, sm: 6 }}>
          <SpreadChart symbol={symbol} />
        </Grid.Col>,
      );
      // Add empty col to maintain 2-column layout
      symbolColumns.push(<Grid.Col key={`${symbol} - spread-spacer`} span={{ xs: 12, sm: 6 }} />);
    }

    if (!conversionProducts.has(symbol)) {
      return;
    }

    symbolColumns.push(
      <Grid.Col key={`${symbol} - conversion price`} span={{ xs: 12, sm: 6 }}>
        <ConversionPriceChart symbol={symbol} />
      </Grid.Col>,
    );

    symbolColumns.push(
      <Grid.Col key={`${symbol} - transport`} span={{ xs: 12, sm: 6 }}>
        <TransportChart symbol={symbol} />
      </Grid.Col>,
    );

    symbolColumns.push(
      <Grid.Col key={`${symbol} - environment`} span={{ xs: 12, sm: 6 }}>
        <EnvironmentChart symbol={symbol} />
      </Grid.Col>,
    );

    symbolColumns.push(<Grid.Col key={`${symbol} - environment-spacer`} span={{ xs: 12, sm: 6 }} />);
  });

  sortedPlainValueObservationSymbols.forEach(symbol => {
    symbolColumns.push(
      <Grid.Col key={`${symbol} - plain value observation`} span={{ xs: 12, sm: 6 }}>
        <PlainValueObservationChart symbol={symbol} />
      </Grid.Col>,
    );
  });

  return (
    <Container fluid>
      <Grid>
        <Grid.Col span={12}>
          <VisualizerCard>
            <Center>
              <Title order={2}>Final Profit / Loss: {formatNumber(profitLoss)}</Title>
            </Center>
          </VisualizerCard>
        </Grid.Col>
        <Grid.Col span={{ xs: 12, sm: 6 }}>
          <ProfitLossChart symbols={sortedSymbols} />
        </Grid.Col>
        <Grid.Col span={{ xs: 12, sm: 6 }}>
          <PositionChart symbols={sortedSymbols} />
        </Grid.Col>
        {/* Step 8: Comparison PnL chart */}
        {comparisonAlgorithms.length > 0 && (
          <Grid.Col span={12}>
            <ComparisonPnlChart />
          </Grid.Col>
        )}
        <Grid.Col span={12}>
          <FeatureToggleControls />
        </Grid.Col>
        <Grid.Col span={12}>
          <TradeFilterControls />
        </Grid.Col>
        {/* Step 7: Log viewer (conditional) */}
        {featureToggles.showLogViewer && (
          <Grid.Col span={12}>
            <LogViewer />
          </Grid.Col>
        )}
        {symbolColumns}
        <Grid.Col span={12}>
          <TimestampsCard />
        </Grid.Col>
        {algorithm.summary && (
          <Grid.Col span={12}>
            <AlgorithmSummaryCard />
          </Grid.Col>
        )}
      </Grid>
    </Container>
  );
}
