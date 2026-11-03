import { useEffect, useState } from 'react';
import { getDashboardSnapshot } from './api';
import type { Observation } from './types';

const ISS_NORAD_ID = 25544;

function formatTime(value: string): string {
  return new Date(value).toLocaleString('en-US', { timeZone: 'UTC' }) + ' UTC';
}

function ReceptionChart({ observations }: { observations: Observation[] }) {
  const buckets = Array.from({ length: 12 }, (_, index) => {
    const end = Date.now() - (11 - index) * 30 * 60_000;
    const start = end - 30 * 60_000;
    const count = observations.filter((item) => {
      const time = Date.parse(item.timestamp);
      return time >= start && time < end;
    }).length;

    return { label: new Date(end).toISOString().slice(11, 16), count };
  });

  const maximum = Math.max(1, ...buckets.map((bucket) => bucket.count));

  return (
    <div className="chart">
      {buckets.map((bucket, index) => (
        <div className="chart-column" key={index}>
          <span>{bucket.count}</span>
          <div
            className="chart-bar"
            style={{ height: `${(bucket.count / maximum) * 120}px` }}
          />
          <small>{bucket.label}</small>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const [observations, setObservations] = useState<Observation[]>([]);
  const [updatedAt, setUpdatedAt] = useState('');
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(false);

  async function refresh() {
    setLoading(true);

    try {
      const snapshot = await getDashboardSnapshot();
      if (snapshot === null) {
        setConnected(false);
        setObservations([]);
        setUpdatedAt('');
      } else {
        setConnected(true);
        setObservations(
          snapshot.observations.filter((item) => item.noradId === ISS_NORAD_ID),
        );
        setUpdatedAt(snapshot.updatedAt);
      }
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load observations');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const recent = [...observations].sort(
    (a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp),
  );

  const stations = new Set(observations.map((item) => item.station));

  return (
    <main>
      <h1>ISS telemetry dashboard</h1>
      <p>International Space Station · NORAD 25544 · AX.25 / APRS</p>

      <button onClick={() => void refresh()} disabled={loading}>
        {loading ? 'Loading...' : 'Refresh'}
      </button>

      {!connected && !error && (
        <p>Backend not connected. Set VITE_API_BASE_URL when the team API is ready.</p>
      )}
      {error && <p className="error">{error}</p>}
      {updatedAt && <p>Updated: {formatTime(updatedAt)}</p>}

      <h2>Reception summary</h2>
      <table>
        <tbody>
          <tr><th>Frames</th><td>{observations.length}</td></tr>
          <tr><th>Receiving stations</th><td>{stations.size}</td></tr>
          <tr><th>Last frame</th><td>{recent[0] ? formatTime(recent[0].timestamp) : '—'}</td></tr>
        </tbody>
      </table>

      <h2>Frames received (30-minute intervals)</h2>
      <ReceptionChart observations={observations} />

      <h2>Recent frames</h2>
      <table>
        <thead>
          <tr>
            <th>Time (UTC)</th>
            <th>Station</th>
            <th>Station ID</th>
            <th>Parse status</th>
            <th>Frame (hex)</th>
          </tr>
        </thead>
        <tbody>
          {recent.slice(0, 30).map((item) => (
            <tr key={item.id}>
              <td>{formatTime(item.timestamp)}</td>
              <td>{item.station}</td>
              <td>{item.stationId ?? '—'}</td>
              <td>{item.parseStatus}</td>
              <td className="hex">{item.frameHex}</td>
            </tr>
          ))}
          {recent.length === 0 && (
            <tr><td colSpan={5}>No frames loaded.</td></tr>
          )}
        </tbody>
      </table>
    </main>
  );
}
