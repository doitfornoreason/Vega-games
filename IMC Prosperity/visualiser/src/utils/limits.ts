import { Algorithm, ProsperitySymbol } from '../models.ts';

const knownLimits: Record<string, number> = {
  INTARIAN_PEPPER_ROOT: 80,
  ASH_COATED_OSMIUM: 80
};

export function getLimit(algorithm: Algorithm, symbol: ProsperitySymbol): number {
  if (knownLimits[symbol] !== undefined) {
    return knownLimits[symbol];
  }

  // Fallback: guess from observed positions when the product isn't in the known list
  const positions = algorithm.data.map(row => row.state.position[symbol] || 0);
  const minPosition = Math.min(...positions);
  const maxPosition = Math.max(...positions);

  return Math.max(Math.abs(minPosition), maxPosition, 1);
}
