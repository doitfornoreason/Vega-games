import { Button, Chip, Group, NumberInput, Stack, Text } from '@mantine/core';
import { ReactNode, useMemo } from 'react';
import { useStore } from '../../store.ts';
import { VisualizerCard } from './VisualizerCard.tsx';

export function TradeFilterControls(): ReactNode {
  const algorithm = useStore(state => state.algorithm)!;
  const tradeFilters = useStore(state => state.tradeFilters);
  const setHiddenTraderIds = useStore(state => state.setHiddenTraderIds);
  const setMinQuantity = useStore(state => state.setMinQuantity);
  const setMaxQuantity = useStore(state => state.setMaxQuantity);

  // Extract unique trader IDs from all trades across all timestamps
  const traderIds = useMemo(() => {
    const ids = new Set<string>();
    if (algorithm.data) {
      for (const row of algorithm.data) {
        for (const trades of Object.values(row.state.ownTrades)) {
          for (const t of trades) {
            if (t.buyer) ids.add(t.buyer);
            if (t.seller) ids.add(t.seller);
          }
        }
        for (const trades of Object.values(row.state.marketTrades)) {
          for (const t of trades) {
            if (t.buyer) ids.add(t.buyer);
            if (t.seller) ids.add(t.seller);
          }
        }
      }
    }
    return [...ids].sort();
  }, [algorithm.data]);

  const handleToggleTrader = (traderId: string) => {
    const newHidden = new Set(tradeFilters.hiddenTraderIds);
    if (newHidden.has(traderId)) {
      newHidden.delete(traderId);
    } else {
      newHidden.add(traderId);
    }
    setHiddenTraderIds(newHidden);
  };

  const handleReset = () => {
    setHiddenTraderIds(new Set<string>());
    setMinQuantity(null);
    setMaxQuantity(null);
  };

  if (traderIds.length === 0) {
    return null;
  }

  return (
    <VisualizerCard title="Trade Filters">
      <Stack gap="sm">
        <div>
          <Text size="sm" fw={500} mb={4}>
            Trader IDs
          </Text>
          <Group gap="xs">
            {traderIds.map(id => (
              <Chip
                key={id}
                checked={!tradeFilters.hiddenTraderIds.has(id)}
                onChange={() => handleToggleTrader(id)}
                size="xs"
              >
                {id || '(anonymous)'}
              </Chip>
            ))}
          </Group>
        </div>

        <Group gap="sm">
          <NumberInput
            label="Min quantity"
            size="xs"
            placeholder="No min"
            value={tradeFilters.minQuantity ?? ''}
            onChange={val => setMinQuantity(val === '' ? null : Number(val))}
            min={0}
            style={{ width: 120 }}
          />
          <NumberInput
            label="Max quantity"
            size="xs"
            placeholder="No max"
            value={tradeFilters.maxQuantity ?? ''}
            onChange={val => setMaxQuantity(val === '' ? null : Number(val))}
            min={0}
            style={{ width: 120 }}
          />
          <Button size="xs" variant="subtle" onClick={handleReset} mt={22}>
            Reset
          </Button>
        </Group>
      </Stack>
    </VisualizerCard>
  );
}
