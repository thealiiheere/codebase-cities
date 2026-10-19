// Typed client for the backend API.
//
// The interfaces below mirror the backend's response models (backend/app/schemas/ and
// backend/app/mapping/). If you change a field there, change it here too.

export interface Building {
  repo: string;
  district: string;
  /** Center of the footprint on the ground (meters). */
  x: number;
  z: number;
  /** Side length of the square footprint. */
  size: number;
  floors: number;
  /** Always floors * floor height. */
  height: number;
  /**
   * The timeline, as [YYYY-MM-DD, value] pairs, oldest first: `growth` is the building's floors
   * from each date on (the first entry is the day the repo was created), `lights` its
   * window_brightness from each date on.
   */
  growth: [string, number][];
  lights: [string, number][];
  color: string;
  /** 0..1: the share of floors with lit windows at night. */
  window_brightness: number;
  roof_lights: number;
  /** 0..3: flat roof, setback top, antenna, rooftop box. */
  style: number;
  stars: number;
  forks: number;
  commits: number;
  recent_commits: number;
  /** A private repo its owner shared: only ever in their own view of their city. */
  private: boolean;
}

export interface District {
  language: string;
  color: string;
  x: number;
  z: number;
  width: number;
  depth: number;
  repo_count: number;
}

export interface Bridge {
  from_repo: string;
  to_repo: string;
  /** 0..1 */
  strength: number;
}

export interface CityLayout {
  version: number;
  username: string;
  as_of: string;
  plaza: { size: number };
  districts: District[];
  buildings: Building[];
  bridges: Bridge[];
  /** Distance from the center to the farthest district corner. */
  radius: number;
}

export interface Track {
  language: string;
  role: string;
  instrument: string;
  volume: number;
  share: number;
  steps: string[][];
}

export interface MusicParams {
  version: number;
  username: string;
  as_of: string;
  tempo_bpm: number;
  tempo_variation: number;
  key: string;
  scale: string;
  intensity: number;
  bars: number;
  steps_per_bar: number;
  tracks: Track[];
}

export interface CityResponse {
  username: string;
  fetched_at: string;
  as_of: string;
  until: string | null;
  timeline: { start: string; end: string };
  /** How many buildings are private repos (only ever non-zero in your own city). */
  private_repos: number;
  city: CityLayout;
  music: MusicParams;
}

export type JobStatus = "queued" | "fetching" | "building" | "done" | "failed";

export interface Job {
  id: string;
  username: string;
  status: JobStatus;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

/** An error response from the API, which always looks like {"error": {"code", "message"}}. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(method: "GET" | "POST", path: string): Promise<{ status: number; body: T }> {
  const response = await fetch(path, { method, headers: { Accept: "application/json" } });
  // A response without a JSON body (e.g. a proxy error page) still needs a sensible error.
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (body as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "http_error",
      error?.message ?? `The server answered ${response.status}.`,
    );
  }
  return { status: response.status, body: body as T };
}

const cityPath = (username: string) => `/api/cities/${encodeURIComponent(username)}`;

/** Ask for a city: either it's ready now (200), or a job was started (202). */
export async function requestCity(
  username: string,
): Promise<{ kind: "ready"; city: CityResponse } | { kind: "queued"; job: Job }> {
  const { status, body } = await request<CityResponse | Job>("POST", cityPath(username));
  return status === 202 ? { kind: "queued", job: body as Job } : { kind: "ready", city: body as CityResponse };
}

export async function getJob(id: string): Promise<Job> {
  return (await request<Job>("GET", `/api/jobs/${encodeURIComponent(id)}`)).body;
}

/**
 * A built city, today's or (with `until`) as it was at the end of a past day. `includePrivate`
 * adds the private repos you shared, and only works for your own city.
 */
export async function getCity(
  username: string,
  until?: string,
  { includePrivate = false } = {},
): Promise<CityResponse> {
  const params = new URLSearchParams();
  if (until) params.set("until", until);
  if (includePrivate) params.set("private", "true");
  const query = params.toString() ? `?${params}` : "";
  return (await request<CityResponse>("GET", cityPath(username) + query)).body;
}

// --- Signing in with GitHub ---

export interface AuthUser {
  login: string;
  name: string | null;
  avatar_url: string;
}

export interface AuthStatus {
  /** False when the server has no GitHub OAuth App set up: offer no sign-in. */
  enabled: boolean;
  user: AuthUser | null;
  /** Set when you're signed in and the server can show private repos. */
  private_repos: { install_url: string } | null;
}

/** Whether sign-in is available, and who is signed in (the browser sends the session cookie). */
export async function getAuthStatus(): Promise<AuthStatus> {
  return (await request<AuthStatus>("GET", "/api/auth/me")).body;
}

export async function signOut(): Promise<void> {
  await request<null>("POST", "/api/auth/logout");
}

/** The address that starts signing in. Afterwards the visitor comes back to `returnTo`. */
export function signInUrl(returnTo: string): string {
  return `/api/auth/login?next=${encodeURIComponent(returnTo)}`;
}

/** A city built from a made-up profile, so the frontend works without a backend. */
export async function loadDemoCity(): Promise<CityResponse> {
  return (await request<CityResponse>("GET", "/demo-city.json")).body;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Get a user's city, waiting for the backend to build it if needed.
 * `onProgress` is called with the job each time its status is checked.
 */
export async function buildCity(
  username: string,
  onProgress: (job: Job) => void,
  pollIntervalMs = 1000,
): Promise<CityResponse> {
  const first = await requestCity(username);
  if (first.kind === "ready") {
    return first.city;
  }
  await waitForJob(first.job, onProgress, pollIntervalMs);
  return getCity(username);
}

/**
 * Your own city with the private repos you shared, waiting while they're fetched (with your
 * GitHub token, on the server). `refresh` fetches them again even if they're recent.
 */
export async function buildPrivateCity(
  onProgress: (job: Job) => void,
  { refresh = false } = {},
  pollIntervalMs = 1000,
): Promise<CityResponse> {
  const path = `/api/me/private-city${refresh ? "?refresh=true" : ""}`;
  const { status, body } = await request<CityResponse | Job>("POST", path);
  if (status !== 202) {
    return body as CityResponse;
  }
  const job = body as Job;
  await waitForJob(job, onProgress, pollIntervalMs);
  return getCity(job.username, undefined, { includePrivate: true });
}

/** Poll a job until it's done; throws if it fails. */
async function waitForJob(first: Job, onProgress: (job: Job) => void, pollIntervalMs: number): Promise<void> {
  let job = first;
  onProgress(job);
  while (job.status !== "done") {
    if (job.status === "failed") {
      throw new ApiError(0, "job_failed", job.error ?? "Building this city failed.");
    }
    await sleep(pollIntervalMs);
    job = await getJob(job.id);
    onProgress(job);
  }
}
