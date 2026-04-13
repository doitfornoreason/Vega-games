import { Table } from '@mantine/core';
import { ReactNode } from 'react';
import { ProsperitySymbol, Trade } from '../../models.ts';
import { useStore } from '../../store.ts';
import { getAskColor, getBidColor } from '../../utils/colors.ts';
import { formatNumber } from '../../utils/format.ts';
import { SimpleTable } from './SimpleTable.tsx';

export interface TradesTableProps {
  trades: Record<ProsperitySymbol, Trade[]>;
}

export function TradesTable({ trades }: TradesTableProps): ReactNode {
  const tradeFilters = useStore(state => state.tradeFilters);

  const rows: ReactNode[] = [];
  for (const symbol of Object.keys(trades).sort((a, b) => a.localeCompare(b))) {
    for (let i = 0; i < trades[symbol].length; i++) {
      const trade = trades[symbol][i];

      // Apply trade filters
      if (tradeFilters) {
        const qty = Math.abs(trade.quantity);
        if (tradeFilters.minQuantity !== null && qty < tradeFilters.minQuantity) continue;
        if (tradeFilters.maxQuantity !== null && qty > tradeFilters.maxQuantity) continue;
        if (tradeFilters.hiddenTraderIds.has(trade.buyer) || tradeFilters.hiddenTraderIds.has(trade.seller)) continue;
      }

      let color: string;
      if (trade.buyer === 'SUBMISSION') {
        color = getBidColor(0.1);
      } else if (trade.seller === 'SUBMISSION') {
        color = getAskColor(0.1);
      } else {
        color = 'transparent';
      }

      rows.push(
        <Table.Tr key={`${symbol}-${i}`} style={{ backgroundColor: color }}>
          <Table.Td>{trade.symbol}</Table.Td>
          <Table.Td>{trade.buyer || '(anonymous)'}</Table.Td>
          <Table.Td>{trade.seller || '(anonymous)'}</Table.Td>
          <Table.Td>{formatNumber(trade.price)}</Table.Td>
          <Table.Td>{formatNumber(trade.quantity)}</Table.Td>
          <Table.Td>{formatNumber(trade.timestamp)}</Table.Td>
        </Table.Tr>,
      );
    }
  }

  return (
    <SimpleTable label="trades" columns={['Symbol', 'Buyer', 'Seller', 'Price', 'Quantity', 'Timestamp']} rows={rows} />
  );
}
