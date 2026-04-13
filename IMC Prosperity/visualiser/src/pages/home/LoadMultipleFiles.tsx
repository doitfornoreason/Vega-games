import { Badge, Button, Group, Stack, Text } from '@mantine/core';
import { Dropzone } from '@mantine/dropzone';
import { IconUpload, IconX } from '@tabler/icons-react';
import { ReactNode, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ErrorAlert } from '../../components/ErrorAlert.tsx';
import { useStore } from '../../store.ts';
import { parseAlgorithmLogs } from '../../utils/algorithm.tsx';
import { HomeCard } from './HomeCard.tsx';

export function LoadMultipleFiles(): ReactNode {
  const navigate = useNavigate();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(false);

  const setAlgorithm = useStore(state => state.setAlgorithm);
  const addComparisonAlgorithm = useStore(state => state.addComparisonAlgorithm);
  const comparisonAlgorithms = useStore(state => state.comparisonAlgorithms);
  const removeComparisonAlgorithm = useStore(state => state.removeComparisonAlgorithm);
  const clearComparisonAlgorithms = useStore(state => state.clearComparisonAlgorithms);

  const onDrop = async (files: File[]) => {
    setError(undefined);
    setLoading(true);

    try {
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const text = await file.text();
        const algo = parseAlgorithmLogs(text);

        if (i === 0) {
          // First file becomes the main algorithm
          setAlgorithm(algo);
        }

        // Extract PnL series for comparison
        const pnlByTimestamp = new Map<number, number>();
        for (const row of algo.activityLogs) {
          if (!pnlByTimestamp.has(row.timestamp)) {
            pnlByTimestamp.set(row.timestamp, row.profitLoss);
          } else {
            pnlByTimestamp.set(row.timestamp, pnlByTimestamp.get(row.timestamp)! + row.profitLoss);
          }
        }

        const pnlData: [number, number][] = [...pnlByTimestamp.entries()].sort((a, b) => a[0] - b[0]);

        addComparisonAlgorithm({
          name: file.name,
          pnlData,
        });
      }

      navigate('/visualizer');
    } catch (err: any) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <HomeCard title="Compare multiple algorithms">
      <Text>
        Load 2 or more log files to overlay their PnL curves for comparison. The first file will be loaded as the main
        algorithm; all files will appear in the PnL comparison chart.
      </Text>

      {comparisonAlgorithms.length > 0 && (
        <Stack gap="xs">
          <Group gap="xs" wrap="wrap">
            {comparisonAlgorithms.map(algo => (
              <Badge
                key={algo.name}
                rightSection={
                  <IconX
                    size={14}
                    style={{ cursor: 'pointer' }}
                    onClick={() => removeComparisonAlgorithm(algo.name)}
                  />
                }
                variant="light"
              >
                {algo.name}
              </Badge>
            ))}
          </Group>
          <Button variant="subtle" size="xs" onClick={clearComparisonAlgorithms}>
            Clear all
          </Button>
        </Stack>
      )}

      {error && <ErrorAlert error={error} />}

      <Dropzone onDrop={onDrop} multiple={true} loading={loading}>
        <Dropzone.Idle>
          <Group justify="center" gap="xl" style={{ minHeight: 80, pointerEvents: 'none' }}>
            <IconUpload size={40} />
            <Text size="xl" inline={true}>
              Drag multiple files here or click to select files
            </Text>
          </Group>
        </Dropzone.Idle>
        <Dropzone.Accept>
          <Group justify="center" gap="xl" style={{ minHeight: 80, pointerEvents: 'none' }}>
            <IconUpload size={40} />
            <Text size="xl" inline={true}>
              Drop files to compare
            </Text>
          </Group>
        </Dropzone.Accept>
      </Dropzone>
    </HomeCard>
  );
}
