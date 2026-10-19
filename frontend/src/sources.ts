// Where a city comes from: the backend for real users, or bundled files in demo mode.
//
// The city itself never needs fetching again while you move through time (today's city holds
// every building's history), but the music does: it's composed from the profile as it was at
// the end of each year. The rest of the page only talks to a CitySource, so the demo and real
// users work the same way.

import { getCity, loadDemoCity, type CityResponse, type MusicParams } from "./api";

export interface CitySource {
  /** Today's city (and music). */
  readonly latest: CityResponse;
  /** The music for the end of `year`, or today's music when `year` is omitted. */
  music(year?: number): Promise<MusicParams>;
}

/**
 * Remembers the music of every year it has fetched, so scrubbing back and forth only asks once
 * per year. It stores the *promise*, so two quick requests for the same year share one network
 * call; a failed request is forgotten so it can be retried.
 */
class CachedSource implements CitySource {
  private readonly cache = new Map<number, Promise<MusicParams>>();

  constructor(
    readonly latest: CityResponse,
    private readonly fetchMusic: (until: string) => Promise<MusicParams>,
  ) {}

  music(year?: number): Promise<MusicParams> {
    if (year === undefined) {
      return Promise.resolve(this.latest.music);
    }
    let pending = this.cache.get(year);
    if (pending === undefined) {
      pending = this.fetchMusic(`${year}-12-31`);
      pending.catch(() => this.cache.delete(year));
      this.cache.set(year, pending);
    }
    return pending;
  }
}

/**
 * A real user's city. A past year's music comes from that year's city on the server (with the
 * private repos too, for your own city with `includePrivate`).
 */
export function apiSource(latest: CityResponse, { includePrivate = false } = {}): CitySource {
  return new CachedSource(
    latest,
    async (until) => (await getCity(latest.username, until, { includePrivate })).music,
  );
}

/** The demo profile: today's city, plus a bundled file with the music at the end of each year. */
export async function demoSource(): Promise<CitySource> {
  const latest = await loadDemoCity();
  // Only downloaded once someone plays the music of a past year.
  let years: Promise<Record<string, MusicParams>> | null = null;
  return new CachedSource(latest, async (until) => {
    years ??= fetch("/demo-music.json").then((response) => {
      if (!response.ok) {
        years = null; // Let the next request try again.
        throw new Error("Couldn't load the demo's music.");
      }
      return response.json() as Promise<Record<string, MusicParams>>;
    });
    const music = (await years)[until];
    if (music === undefined) {
      throw new Error(`The demo has no music for ${until}.`);
    }
    return music;
  });
}
