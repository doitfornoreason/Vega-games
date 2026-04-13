import { MantineColorScheme } from '@mantine/core';
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { Algorithm } from './models.ts';

export interface TradeFilters {
  hiddenTraderIds: Set<string>;
  minQuantity: number | null;
  maxQuantity: number | null;
}

export interface FeatureToggles {
  showSpreadChart: boolean;
  showInventoryHeatmap: boolean;
  showOrderVisualization: boolean;
  showTradePnlColoring: boolean;
  showLogViewer: boolean;
  normalizationMode: 'none' | 'mid' | 'custom';
  normalizationCustomValue: number | null;
  downsamplingFactor: number;
}

export interface ComparisonAlgorithm {
  name: string;
  pnlData: [number, number][];
}

export interface State {
  colorScheme: MantineColorScheme;

  idToken: string;
  round: string;

  algorithm: Algorithm | null;

  tradeFilters: TradeFilters;
  featureToggles: FeatureToggles;

  hoveredTimestamp: number | null;

  comparisonAlgorithms: ComparisonAlgorithm[];

  setColorScheme: (colorScheme: MantineColorScheme) => void;
  setIdToken: (idToken: string) => void;
  setRound: (round: string) => void;
  setAlgorithm: (algorithm: Algorithm | null) => void;
  setHiddenTraderIds: (ids: Set<string>) => void;
  setMinQuantity: (min: number | null) => void;
  setMaxQuantity: (max: number | null) => void;
  setFeatureToggles: (toggles: Partial<FeatureToggles>) => void;
  setHoveredTimestamp: (timestamp: number | null) => void;
  addComparisonAlgorithm: (algo: ComparisonAlgorithm) => void;
  removeComparisonAlgorithm: (name: string) => void;
  clearComparisonAlgorithms: () => void;
}

const defaultFeatureToggles: FeatureToggles = {
  showSpreadChart: false,
  showInventoryHeatmap: false,
  showOrderVisualization: false,
  showTradePnlColoring: false,
  showLogViewer: false,
  normalizationMode: 'none',
  normalizationCustomValue: null,
  downsamplingFactor: 1,
};

export const useStore = create<State>()(
  persist(
    set => ({
      colorScheme: 'auto',

      idToken: '',
      round: 'ROUND0',

      algorithm: null,

      tradeFilters: {
        hiddenTraderIds: new Set<string>(),
        minQuantity: null,
        maxQuantity: null,
      },

      featureToggles: { ...defaultFeatureToggles },

      hoveredTimestamp: null,

      comparisonAlgorithms: [],

      setColorScheme: colorScheme => set({ colorScheme }),
      setIdToken: idToken => set({ idToken }),
      setRound: round => set({ round }),
      setAlgorithm: algorithm => set({ algorithm }),
      setHiddenTraderIds: ids =>
        set(state => ({
          tradeFilters: { ...state.tradeFilters, hiddenTraderIds: ids },
        })),
      setMinQuantity: min =>
        set(state => ({
          tradeFilters: { ...state.tradeFilters, minQuantity: min },
        })),
      setMaxQuantity: max =>
        set(state => ({
          tradeFilters: { ...state.tradeFilters, maxQuantity: max },
        })),
      setFeatureToggles: toggles =>
        set(state => ({
          featureToggles: { ...state.featureToggles, ...toggles },
        })),
      setHoveredTimestamp: timestamp => set({ hoveredTimestamp: timestamp }),
      addComparisonAlgorithm: algo =>
        set(state => ({
          comparisonAlgorithms: [...state.comparisonAlgorithms, algo],
        })),
      removeComparisonAlgorithm: name =>
        set(state => ({
          comparisonAlgorithms: state.comparisonAlgorithms.filter(a => a.name !== name),
        })),
      clearComparisonAlgorithms: () => set({ comparisonAlgorithms: [] }),
    }),
    {
      name: 'imc-prosperity-3-visualizer',
      partialize: state => ({
        colorScheme: state.colorScheme,
        idToken: state.idToken,
        round: state.round,
        featureToggles: state.featureToggles,
      }),
    },
  ),
);
