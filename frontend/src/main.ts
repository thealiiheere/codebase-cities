// Entry point: connects the page's controls, the backend API, and the 3D view.

import "./style.css";
import { ApiError, buildCity, loadDemoCity, type CityResponse } from "./api";
import { CityView } from "./city/scene";
import { describeJob, getUi, hideTooltip, setBusy, setDayNightLabel, setStatus, showTooltip } from "./ui";

const ui = getUi();
const view = new CityView(ui.viewport);

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
  const repos = response.city.buildings.length;
  ui.info.textContent = `${response.username} · ${repos} repo${repos === 1 ? "" : "s"} · as of ${response.as_of}`;
}

ui.dayNight.addEventListener("click", () => setDayNightLabel(ui, view.toggleDayNight()));

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
