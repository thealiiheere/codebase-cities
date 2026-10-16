// Where a user's cities come from: the backend for real users, or bundled files in demo mode.
// The timeline only talks to a CitySource, so it works the same way in both.

import { getCity, loadDemoCity, type CityResponse } from "./api";

export interface CitySource {
  readonly username: string;
  /** The city at the end of `until` (YYYY-MM-DD), or today's city when `until` is omitted. */
  city(until?: string): Promise<CityResponse>;
}

/**
 * Remembers every city it has fetched, so scrubbing back and forth over the timeline only asks
 * the server once per year. It stores the *promise*, so two quick requests for the same year
 * share one network call; a failed request is forgotten so it can be retried.
 */
class CachedSource implements CitySource {
  private readonly cache = new Map<string, Promise<CityResponse>>();

  constructor(
    readonly username: string,
    latest: CityResponse,
    private readonly fetchCity: (until: string) => Promise<CityResponse>,
  ) {
    this.cache.set("latest", Promise.resolve(latest));
  }

  city(until?: string): Promise<CityResponse> {
    const key = until ?? "latest";
    let pending = this.cache.get(key);
    if (pending === undefined) {
      pending = this.fetchCity(key);
      pending.catch(() => this.cache.delete(key));
      this.cache.set(key, pending);
    }
    return pending;
  }
}

/** Cities for a real user, starting from the latest city we already have. */
export function apiSource(latest: CityResponse): CitySource {
  return new CachedSource(latest.username, latest, (until) => getCity(latest.username, until));
}

/** The demo profile: today's city plus a bundled file with the city at the end of each year. */
export async function demoSource(): Promise<{ source: CitySource; latest: CityResponse }> {
  const [latest, timeline] = await Promise.all([
    loadDemoCity(),
    fetch("/demo-timeline.json").then((response) => response.json() as Promise<Record<string, CityResponse>>),
  ]);
  const source = new CachedSource(latest.username, latest, async (until) => {
    const city = timeline[until];
    if (city === undefined) {
      throw new Error(`The demo has no city for ${until}.`);
    }
    return city;
  });
  return { source, latest };
}
