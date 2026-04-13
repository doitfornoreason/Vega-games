import { Group, NumberInput, Select, Slider, Stack, Switch, Text } from '@mantine/core';
import { ReactNode } from 'react';
import { useStore } from '../../store.ts';
import { VisualizerCard } from './VisualizerCard.tsx';

export function FeatureToggleControls(): ReactNode {
  const featureToggles = useStore(state => state.featureToggles);
  const setFeatureToggles = useStore(state => state.setFeatureToggles);

  return (
    <VisualizerCard>
      <Text fw={600} mb="sm">
        Feature Toggles
      </Text>
      <Stack gap="md">
        <Group gap="xl" wrap="wrap">
          <Switch
            label="Trade P&L Coloring"
            checked={featureToggles.showTradePnlColoring}
            onChange={e => setFeatureToggles({ showTradePnlColoring: e.currentTarget.checked })}
          />
          <Switch
            label="Order Visualization"
            checked={featureToggles.showOrderVisualization}
            onChange={e => setFeatureToggles({ showOrderVisualization: e.currentTarget.checked })}
          />
          <Switch
            label="Spread Charts"
            checked={featureToggles.showSpreadChart}
            onChange={e => setFeatureToggles({ showSpreadChart: e.currentTarget.checked })}
          />
          <Switch
            label="Inventory Heatmap"
            checked={featureToggles.showInventoryHeatmap}
            onChange={e => setFeatureToggles({ showInventoryHeatmap: e.currentTarget.checked })}
          />
          <Switch
            label="Log Viewer"
            checked={featureToggles.showLogViewer}
            onChange={e => setFeatureToggles({ showLogViewer: e.currentTarget.checked })}
          />
        </Group>

        <Group gap="xl" wrap="wrap" align="flex-end">
          <Select
            label="Price Normalization"
            data={[
              { value: 'none', label: 'None' },
              { value: 'mid', label: 'Relative to mid-price' },
              { value: 'custom', label: 'Custom value' },
            ]}
            value={featureToggles.normalizationMode}
            onChange={value => setFeatureToggles({ normalizationMode: (value as 'none' | 'mid' | 'custom') || 'none' })}
            w={200}
          />
          {featureToggles.normalizationMode === 'custom' && (
            <NumberInput
              label="Reference value"
              value={featureToggles.normalizationCustomValue ?? ''}
              onChange={value => setFeatureToggles({ normalizationCustomValue: typeof value === 'number' ? value : null })}
              w={150}
            />
          )}
          <Stack gap={4} w={200}>
            <Text size="sm">Downsampling factor: {featureToggles.downsamplingFactor}x</Text>
            <Slider
              min={1}
              max={10}
              step={1}
              marks={[
                { value: 1, label: '1x' },
                { value: 2, label: '2x' },
                { value: 5, label: '5x' },
                { value: 10, label: '10x' },
              ]}
              value={featureToggles.downsamplingFactor}
              onChange={value => setFeatureToggles({ downsamplingFactor: value })}
            />
          </Stack>
        </Group>
      </Stack>
    </VisualizerCard>
  );
}
