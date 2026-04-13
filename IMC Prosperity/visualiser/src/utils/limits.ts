import { Algorithm, ProsperitySymbol } from '../models.ts';

const knownLimits: Record<string, number> = {
  RAINFOREST_RESIN: 50,
  KELP: 50,
  SQUID_INK: 50,
  CROISSANTS: 250,
  JAMS: 350,
  DJEMBES: 60,
  PICNIC_BASKET1: 60,
  PICNIC_BASKET2: 100,
  VOLCANIC_ROCK: 400,
  VOLCANIC_ROCK_VOUCHER_9500: 200,
  VOLCANIC_ROCK_VOUCHER_9750: 200,
  VOLCANIC_ROCK_VOUCHER_10000: 200,
  VOLCANIC_ROCK_VOUCHER_10250: 200,
  VOLCANIC_ROCK_VOUCHER_10500: 200,
  MAGNIFICENT_MACARONS: 75,
  TOMATOES: 50,
  EMERALDS: 50,
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
