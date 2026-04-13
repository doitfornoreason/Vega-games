import { Code, ScrollArea, Tabs, Text, TextInput } from '@mantine/core';
import { IconSearch } from '@tabler/icons-react';
import { ReactNode, useMemo, useState } from 'react';
import { useStore } from '../../store.ts';
import { VisualizerCard } from './VisualizerCard.tsx';

export function LogViewer(): ReactNode {
  const algorithm = useStore(state => state.algorithm)!;
  const hoveredTimestamp = useStore(state => state.hoveredTimestamp);
  const [searchText, setSearchText] = useState('');

  // Build a map of timestamp → { algorithmLogs, sandboxLogs }
  const logMap = useMemo(() => {
    const map = new Map<number, { algorithmLogs: string; sandboxLogs: string }>();
    for (const row of algorithm.data) {
      map.set(row.state.timestamp, {
        algorithmLogs: row.algorithmLogs || '',
        sandboxLogs: row.sandboxLogs || '',
      });
    }
    return map;
  }, [algorithm]);

  // Find closest timestamp
  const closestEntry = useMemo(() => {
    if (hoveredTimestamp === null) return null;

    // Try exact match first
    const exact = logMap.get(hoveredTimestamp);
    if (exact) return { timestamp: hoveredTimestamp, ...exact };

    // Find closest
    let bestTs = -1;
    let bestDist = Infinity;
    for (const ts of logMap.keys()) {
      const dist = Math.abs(ts - hoveredTimestamp);
      if (dist < bestDist) {
        bestDist = dist;
        bestTs = ts;
      }
    }

    if (bestTs >= 0) {
      const entry = logMap.get(bestTs)!;
      return { timestamp: bestTs, ...entry };
    }

    return null;
  }, [hoveredTimestamp, logMap]);

  // Filter logs by search text
  const filterLog = (log: string): string => {
    if (!searchText) return log;
    return log
      .split('\n')
      .filter(line => line.toLowerCase().includes(searchText.toLowerCase()))
      .join('\n');
  };

  if (hoveredTimestamp === null || closestEntry === null) {
    return (
      <VisualizerCard>
        <Text fw={600} mb="sm">
          Log Viewer
        </Text>
        <Text c="dimmed" ta="center" py="xl">
          Hover over a price chart to see algorithm logs for that timestamp
        </Text>
      </VisualizerCard>
    );
  }

  const algoLogs = filterLog(closestEntry.algorithmLogs);
  const sandboxLogs = filterLog(closestEntry.sandboxLogs);

  return (
    <VisualizerCard>
      <Text fw={600} mb="sm">
        Log Viewer — Timestamp {closestEntry.timestamp.toLocaleString()}
      </Text>
      <TextInput
        placeholder="Search logs..."
        leftSection={<IconSearch size={16} />}
        value={searchText}
        onChange={e => setSearchText(e.currentTarget.value)}
        mb="sm"
      />
      <Tabs defaultValue="algorithm">
        <Tabs.List>
          <Tabs.Tab value="algorithm">Algorithm Logs</Tabs.Tab>
          <Tabs.Tab value="sandbox">Sandbox Logs</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="algorithm" pt="sm">
          <ScrollArea h={400}>
            {algoLogs ? (
              <Code block style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {algoLogs}
              </Code>
            ) : (
              <Text c="dimmed" ta="center">
                No algorithm logs at this timestamp
              </Text>
            )}
          </ScrollArea>
        </Tabs.Panel>

        <Tabs.Panel value="sandbox" pt="sm">
          <ScrollArea h={400}>
            {sandboxLogs ? (
              <Code block style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {sandboxLogs}
              </Code>
            ) : (
              <Text c="dimmed" ta="center">
                No sandbox logs at this timestamp
              </Text>
            )}
          </ScrollArea>
        </Tabs.Panel>
      </Tabs>
    </VisualizerCard>
  );
}
