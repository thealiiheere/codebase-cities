// Entry point: connects the page's controls, the backend API, the 3D view, and the music.

import "./style.css";
import { ApiError, buildCity, loadDemoCity, type CityResponse } from "./api";
import { CityView } from "./city/scene";
import { MusicPlayer } from "./sound/player";
import {
  describeJob,
  describeMusic,
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

ui.form.addEventListener("submit", (event) => {
  event.preventDefault(); // Stop the browser from reloading the page.
  const username = ui.username.value.trim();
  if (username) {
    void loadCity(username);
  }
});

async function loadCity(username: string): Promise<void> {
  setBusy(ui, true);
  setStatus(ui, `Looking up ${username}…`);
  try {
    const response = await buildCity(username, (job) => setStatus(ui, describeJob(job)));
    show(response);
    setStatus(ui, "");
  } catch (error) {
    setStatus(ui, error instanceof ApiError ? error.message : "Couldn't reach the server.", true);
  } finally {
    setBusy(ui, false);
  }
}

function show(response: CityResponse): void {
  view.show(response.city);
  player.load(response.music);
  const repos = response.city.buildings.length;
  ui.cityInfo.textContent = `${response.username} · ${repos} repo${repos === 1 ? "" : "s"} · as of ${response.as_of}`;
  ui.nowPlaying.textContent = describeMusic(response.music);
  ui.play.disabled = !player.hasMusic;
  setPlayLabel(ui, player.isPlaying);
}

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

// Hovering: raycasting on every pointer event would be wasteful, so do it at most once per
// animation frame, with the latest pointer position.
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

// /?demo shows a city from a made-up profile, no backend needed.
if (new URLSearchParams(window.location.search).has("demo")) {
  setStatus(ui, "Loading the demo city…");
  loadDemoCity()
    .then((response) => {
      show(response);
      setStatus(ui, "Demo city (a made-up profile). Enter a GitHub username to build a real one.");
    })
    .catch(() => setStatus(ui, "Couldn't load the demo city.", true));
} else {
  setStatus(ui, "Enter a GitHub username to build their city.");
}
