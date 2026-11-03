import type { DashboardSnapshot } from './types';

export async function getDashboardSnapshot(): Promise<DashboardSnapshot | null> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL?.trim();

  // Return Null until API URL is configured
  if (!baseUrl) {
    return null;
  }

  const url = `${baseUrl.replace(/\/$/, '')}/api/dashboard`;
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`Data request failed: HTTP ${response.status}`);
  }

  return (await response.json()) as DashboardSnapshot;
}
