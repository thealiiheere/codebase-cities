// Entry point: connects the page's controls, the URL, the backend API, the 3D view, the
// timeline, and the music.

import "./style.css";
import { Account, takeSignInError } from "./account";
import { ApiError, buildCity, type CityResponse, type MusicParams } from "./api";
import { CityView } from "./city/scene";
import { Overlay } from "./overlay";
import { currentRoute, setRoute, type CityRoute, type Route } from "./router";
import type { MusicPlayer } from "./sound/player";
import { apiSource, demoSource, type CitySource } from "./sources";
import { dayBefore, formatDate, Timeline } from "./timeline";
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
// The music player (and Tone.js, ~280 KB) is only downloaded the first time someone presses
// Play; until then `player` is null.
let player: MusicPlayer | null = null;
/** The music for the year on screen, once known. */
let music: MusicParams | null = null;
let audioContext: AudioContext | null = null;
const overlay = new Overlay(ui.overlay);
const account = new Account(ui.account, {
  openCity: (username) => void openRoute({ kind: "city", username }, { addToHistory: true }),
  message: (text, isError = false) => (isError ? setStatus(ui, text, true) : flashStatus(ui, text)),
});

/** What the page is showing: whose city (or the demo), at which year (undefined = today). */
let shown: { route: CityRoute; source: CitySource } | null = null;

const timeline = new Timeline(ui.timeline, {
  showDate: (date) => {
    const standing = view.setTime(date);
    if (shown) {
      ui.cityInfo.textContent = describeCity(shown.source.latest, standing, date);
    }
  },
  yearChanged: (year) => {
    if (shown) {
      // Keep the share link pointing at the year on screen (without filling the history).
      shown.route = { ...shown.route, year };
      setRoute(shown.route, { replace: true });
      void syncMusic();
    }
  },
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
    show(apiSource(latest), { kind: "city", username: latest.username, year });
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
    const source = await demoSource();
    overlay.hide();
    show(source, { kind: "demo", year });
    setStatus(ui, "Demo city from a made-up profile. Enter a GitHub username to build a real one.");
  } catch {
    overlay.error("Couldn't load the demo city.", () => void openDemo(year));
  }
}

/** Draw a new city, with its timeline and music, and jump to the route's year (if any). */
function show(source: CitySource, route: CityRoute): void {
  const { latest } = source;
  shown = { route: { ...route, year: undefined }, source };
  setRoute(shown.route, { replace: true });
  view.show(latest.city);
  ui.share.hidden = false;
  ui.play.disabled = latest.music.tracks.length === 0;
  void syncMusic();
  // The timeline starts the day before the first repo, on an empty plaza. Attaching it shows
  // the city on the route's year, which updates the info line, link, and music.
  const first = latest.city.buildings.map((building) => building.growth[0]?.[0]).sort()[0];
  timeline.attach(first ? dayBefore(first) : latest.timeline.start, latest.timeline.end, route.year);
}

/** e.g. "octocat · 8 repos · as of 14 Mar 2019" */
function describeCity(latest: CityResponse, standing: number, date: string | null): string {
  const repos = `${standing} repo${standing === 1 ? "" : "s"}`;
  return `${latest.username} · ${repos} · as of ${formatDate(date ?? latest.as_of)}`;
}

// Each music request gets a number, and only the newest one's answer is used: otherwise a slow
// answer for 2017 could arrive after 2019's and replace it.
let musicRequest = 0;

/**
 * Switch the music to the year on screen. A past year's music is only fetched once someone has
 * pressed Play, so moving the slider costs no network for people who never listen.
 */
async function syncMusic(): Promise<void> {
  const request = ++musicRequest;
  if (shown === null) {
    return;
  }
  if (player === null && shown.route.year !== undefined) {
    music = null;
    ui.nowPlaying.textContent = "";
    return;
  }
  try {
    const params = await shown.source.music(shown.route.year);
    if (request === musicRequest) {
      music = params;
      player?.load(params);
      ui.nowPlaying.textContent = describeMusic(params);
    }
  } catch {
    if (request === musicRequest) {
      setStatus(ui, "Couldn't load the music for this year.", true);
    }
  }
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
  if (player?.isPlaying) {
    player.pause();
    setPlayLabel(ui, false);
    return;
  }
  // This must happen right away, inside the click: browsers only let a page start audio in
  // direct response to the user, and the download below takes a moment.
  audioContext ??= new AudioContext();
  void audioContext.resume();
  try {
    if (player === null) {
      ui.play.textContent = "Loading…";
      const { MusicPlayer } = await import("./sound/player");
      player = new MusicPlayer(audioContext);
      player.setVolume(volume);
      if (music) player.load(music);
      else await syncMusic();
    }
    await player.play();
  } catch {
    setStatus(ui, "Couldn't start the music.", true);
  }
  setPlayLabel(ui, player?.isPlaying ?? false);
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

let volume = savedVolume();
ui.volume.value = String(Math.round(volume * 100));
ui.volume.addEventListener("input", () => {
  volume = Number(ui.volume.value) / 100;
  player?.setVolume(volume);
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
  const hit = pointer && view.buildingAt(pointer.x, pointer.y);
  if (pointer && hit) {
    showTooltip(ui, hit.building, hit.floors, pointer.x, pointer.y);
  } else {
    hideTooltip(ui);
  }
}

// --- Start ---

// Read (and tidy away) a failed sign-in's ?auth_error before the URL is used for anything.
const signInError = takeSignInError();
void openRoute(currentRoute()).then(() => {
  if (signInError) {
    setStatus(ui, signInError, true);
  }
});
void account.load();
