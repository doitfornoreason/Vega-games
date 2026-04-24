import { Algorithm, ProsperitySymbol } from '../models.ts';

const knownLimits: Record<string, number> = {
  INTARIAN_PEPPER_ROOT: 80,
  ASH_COATED_OSMIUM: 80,
  HYDROGEL_PACK: 200,
  VELVETFRUIT_EXTRACT: 200, 
  VEV_4000: 300, VEV_4500: 300, VEV_5000: 300, VEV_5100: 300, VEV_5200: 300, VEV_5300: 300, VEV_5400: 300, VEV_5500: 300, VEV_6000: 300, VEV_6500: 300,
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
