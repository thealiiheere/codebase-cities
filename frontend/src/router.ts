// Share links: the page's URL says which city (and year) to show.
//
//   /                     the welcome screen
//   /city/octocat         octocat's city today
//   /city/octocat?year=2019   ...as it was at the end of 2019
//   /?demo                the demo city (add &year=2020 for a year)
//
// This is a single-page app: every path is served the same index.html (Vite does that in
// development; nginx is configured to in production), and this module reads the path.

export type Route =
  | { kind: "home" }
  | { kind: "demo"; year?: number }
  | { kind: "city"; username: string; year?: number };

/** A route that shows a city (and so has a year). */
export type CityRoute = Exclude<Route, { kind: "home" }>;

export function currentRoute(): Route {
  const params = new URLSearchParams(window.location.search);
  const year = parseYear(params.get("year"));
  if (params.has("demo")) {
    return { kind: "demo", year };
  }
  const match = /^\/city\/([^/]+)\/?$/.exec(window.location.pathname);
  if (match?.[1]) {
    return { kind: "city", username: decodeURIComponent(match[1]), year };
  }
  return { kind: "home" };
}

/** The URL for a route. */
export function urlFor(route: Route): string {
  const year = "year" in route && route.year !== undefined ? route.year : undefined;
  switch (route.kind) {
    case "home":
      return "/";
    case "demo":
      return year === undefined ? "/?demo" : `/?demo&year=${year}`;
    case "city": {
      const path = `/city/${encodeURIComponent(route.username)}`;
      return year === undefined ? path : `${path}?year=${year}`;
    }
  }
}

/**
 * Put a route in the address bar. A new city adds a history entry (so Back returns to the
 * previous one); moving the year slider replaces the current entry instead, so Back doesn't
 * step through every year you scrubbed past.
 */
export function setRoute(route: Route, { replace = false } = {}): void {
  const url = urlFor(route);
  if (url === window.location.pathname + window.location.search) {
    return;
  }
  if (replace) {
    window.history.replaceState(null, "", url);
  } else {
    window.history.pushState(null, "", url);
  }
}

function parseYear(value: string | null): number | undefined {
  const year = Number(value);
  return value !== null && Number.isInteger(year) && year >= 2008 && year <= 2100 ? year : undefined;
}
