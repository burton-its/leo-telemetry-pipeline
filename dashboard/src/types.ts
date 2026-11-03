export type Observation = {
  id: string;
  noradId: number;
  satellite: string;
  timestamp: string;
  station: string;
  stationId: number | null;
  frameHex: string;
  parseStatus: 'parsed' | 'unparsed' | 'error';
};

export type DashboardSnapshot = {
  observations: Observation[];
  updatedAt: string;
};
