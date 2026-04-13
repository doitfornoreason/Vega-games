export function getBidColor(alpha: number): string {
  return `rgba(39, 174, 96, ${alpha})`;
}

export function getAskColor(alpha: number): string {
  return `rgba(192, 57, 43, ${alpha})`;
}

export function getOwnTradeColor(alpha: number): string {
  return `rgba(52, 152, 219, ${alpha})`;
}

export function getMarketTradeColor(alpha: number): string {
  return `rgba(155, 89, 182, ${alpha})`;
}

export function getProfitableTradeColor(alpha: number): string {
  return `rgba(46, 204, 113, ${alpha})`;
}

export function getUnprofitableTradeColor(alpha: number): string {
  return `rgba(231, 76, 60, ${alpha})`;
}
