// Entry point: connects the page's controls, the URL, the backend API, the 3D view, the
// timeline, and the music.

import "./style.css";
import { ApiError, buildCity, type CityResponse } from "./api";
import { CityView } from "./city/scene";
import { Overlay } from "./overlay";
import { currentRoute, setRoute, type CityRoute, type Route } from "./router";
import { MusicPlayer } from "./sound/player";
import { apiSource, demoSource } from "./sources";
import { Timeline } from "./timeline";
import {
  describeMusic,
  flashStatus,
  getUi,
  hideTooltip,
  setBusy,
  setDayNightLabel,
  setPlayLabel,
  setStatus,
  showTooltip,
} from "./ui";

const ui = getUi();
const view = new CityView(ui.viewport);
const player = new MusicPlayer();
const overlay = new Overlay(ui.overlay);

/** What the page is showing: whose city (or the demo), at which year (undefined = today). */
let shown: { route: CityRoute; response: CityResponse } | null = null;

const timeline = new Timeline(ui.timeline, {
  show: (response) => show(response, { keepCamera: true }),
  yearChanged: (year) => {
    if (shown) {
      // Keep the share link pointing at the year on screen (without filling the history).
      setRoute({ ...shown.route, year }, { replace: true });
      shown.route = { ...shown.route, year };
    }
  },
  // While music plays, stay one bar on each year so the city and the music change together.
  stepMilliseconds: () =>
    player.isPlaying && shown ? (4 * 60_000) / shown.response.music.tempo_bpm : 1500,
  showError: (message) => setStatus(ui, message, true),
});

// --- Loading cities ---

ui.form.addEventListener("submit", (event) => {
  event.preventDefault(); // Stop the browser from reloading the page.
  const username = ui.username.value.trim();
  if (username) {
    void openRoute({ kind: "city", username }, { addToHistory: true });
  }
});

/** Show whatever a route asks for: the welcome card, the demo, or someone's city. */
async function openRoute(route: Route, { addToHistory = false } = {}): Promise<void> {
  if (addToHistory) {
    setRoute(route);
  }
  if (route.kind === "home") {
    timeline.detach();
    overlay.welcome();
  } else if (route.kind === "demo") {
    await openDemo(route.year);
  } else {
    await openCity(route.username, route.year);
  }
}

async function openCity(username: string, year?: number): Promise<void> {
  ui.username.value = username;
  setBusy(ui, true);
  setStatus(ui, "");
  timeline.detach();
  overlay.building(username);
  try {
    const latest = await buildCity(username, (job) => overlay.building(username, job));
    overlay.hide();
    // The URL may have used different capitalization; show the canonical lowercase login.
    shown = { route: { kind: "city", username: latest.username, year }, response: latest };
    setRoute({ kind: "city", username: latest.username, year }, { replace: true });
    show(latest);
    timeline.attach(apiSource(latest), latest, year);
  } catch (error) {
    const message = error instanceof ApiError ? error.message : "Couldn't reach the server.";
    // Retrying can't fix an unknown or invalid username, so only offer it for errors that
    // can clear up on their own (network trouble, rate limits, a failed job).
    const permanent = error instanceof ApiError && (error.status === 404 || error.status === 422);
    overlay.error(message, permanent ? undefined : () => void openCity(username, year));
  } finally {
    setBusy(ui, false);
  }
}

async function openDemo(year?: number): Promise<void> {
  overlay.building("the demo");
  try {
    const { source, latest } = await demoSource();
    overlay.hide();
    shown = { route: { kind: "demo", year }, response: latest };
    show(latest);
    timeline.attach(source, latest, year);
    setStatus(ui, "Demo city from a made-up profile. Enter a GitHub username to build a real one.");
  } catch {
    overlay.error("Couldn't load the demo city.", () => void openDemo(year));
  }
}

/** Draw a city and switch to its music. */
function show(response: CityResponse, { keepCamera = false } = {}): void {
  if (shown) {
    shown.response = response;
  }
  view.show(response.city, { keepCamera });
  player.load(response.music);
  const repos = response.city.buildings.length;
  const when = response.until ? `end of ${response.until.slice(0, 4)}` : `as of ${response.as_of}`;
  ui.cityInfo.textContent = `${response.username} · ${repos} repo${repos === 1 ? "" : "s"} · ${when}`;
  ui.nowPlaying.textContent = describeMusic(response.music);
  ui.share.hidden = false;
  // A year with no code yet has no music; keep the button usable while something is playing.
  ui.play.disabled = !player.hasMusic && !player.isPlaying;
  setPlayLabel(ui, player.isPlaying);
}

// The browser's Back and Forward buttons change the URL without reloading; follow along.
window.addEventListener("popstate", () => void openRoute(currentRoute()));

// --- Share link ---

ui.share.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(window.location.href);
    flashStatus(ui, "Link copied: anyone opening it sees this city.");
  } catch {
    // Clipboard access can be refused (e.g. on plain http); show the link to copy by hand.
    setStatus(ui, `Copy this link: ${window.location.href}`);
  }
});

// --- Day and night ---

ui.dayNight.addEventListener("click", () => setDayNightLabel(ui, view.toggleDayNight()));

// --- Music ---

ui.play.addEventListener("click", async () => {
  if (player.isPlaying) {
    player.pause();
  } else {
    await player.play(); // Inside the click handler, so the browser allows sound.
  }
  setPlayLabel(ui, player.isPlaying);
});

// The volume is remembered in this browser between visits. Storage can be unavailable
// (private windows, blocked cookies), so every access is guarded and has a default.
const VOLUME_KEY = "codebase-cities:volume";

function savedVolume(): number {
  try {
    const raw = localStorage.getItem(VOLUME_KEY);
    const stored = raw === null ? Number.NaN : Number(raw);
    return stored >= 0 && stored <= 1 ? stored : 0.7; // NaN fails both checks.
  } catch {
    return 0.7;
  }
}

const initialVolume = savedVolume();
ui.volume.value = String(Math.round(initialVolume * 100));
player.setVolume(initialVolume);
ui.volume.addEventListener("input", () => {
  const volume = Number(ui.volume.value) / 100;
  player.setVolume(volume);
  try {
    localStorage.setItem(VOLUME_KEY, String(volume));
  } catch {
    // Not remembered this time; the slider still works.
  }
});

// --- Hover tooltips ---

// Raycasting on every pointer event would be wasteful, so do it at most once per animation
// frame, with the latest pointer position.
let pointer: { x: number; y: number } | null = null;
let hoverQueued = false;

function queueHover(): void {
  if (!hoverQueued) {
    hoverQueued = true;
    requestAnimationFrame(updateHover);
  }
}

view.canvas.addEventListener("pointermove", (event) => {
  pointer = { x: event.clientX, y: event.clientY };
  queueHover();
});
view.canvas.addEventListener("pointerleave", () => {
  pointer = null;
  hideTooltip(ui);
});
// The camera keeps gliding after a drag, moving buildings out from under a still mouse, so
// check again what's under the pointer whenever the view changes.
view.onCameraMove(() => {
  if (pointer) {
    queueHover();
  }
});

function updateHover(): void {
  hoverQueued = false;
  const building = pointer && view.buildingAt(pointer.x, pointer.y);
  if (pointer && building) {
    showTooltip(ui, building, pointer.x, pointer.y);
  } else {
    hideTooltip(ui);
  }
}

// --- Start ---

void openRoute(currentRoute());
