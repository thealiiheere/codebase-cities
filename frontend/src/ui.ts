// The page's HTML controls (defined in index.html): finding them and updating them.

import type { Building, Job, MusicParams } from "./api";

export interface Ui {
  form: HTMLFormElement;
  username: HTMLInputElement;
  submit: HTMLButtonElement;
  status: HTMLElement;
  play: HTMLButtonElement;
  volume: HTMLInputElement;
  dayNight: HTMLButtonElement;
  viewport: HTMLElement;
  cityInfo: HTMLElement;
  nowPlaying: HTMLElement;
  tooltip: HTMLElement;
}

function find<T extends HTMLElement>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (element === null) {
    throw new Error(`index.html is missing ${selector}`);
  }
  return element;
}

export function getUi(): Ui {
  return {
    form: find("#search"),
    username: find("#username"),
    submit: find('#search button[type="submit"]'),
    status: find("#status"),
    play: find("#play"),
    volume: find("#volume"),
    dayNight: find("#daynight"),
    viewport: find("#viewport"),
    cityInfo: find("#city-info"),
    nowPlaying: find("#now-playing"),
    tooltip: find("#tooltip"),
  };
}

/** The play button names what it will do. */
export function setPlayLabel(ui: Ui, isPlaying: boolean): void {
  ui.play.textContent = isPlaying ? "⏸ Pause" : "▶ Play";
}

/** e.g. "♪ 96 BPM · F dorian · Python piano, Go fm-bass, HTML pad" */
export function describeMusic(music: MusicParams): string {
  if (music.tracks.length === 0) {
    return "♪ No code to play yet";
  }
  const tracks = music.tracks.map((track) => `${track.language} ${track.instrument}`).join(", ");
  return `♪ ${music.tempo_bpm} BPM · ${music.key} ${music.scale} · ${tracks}`;
}

export function setStatus(ui: Ui, text: string, isError = false): void {
  ui.status.textContent = text;
  ui.status.classList.toggle("error", isError);
}

export function setBusy(ui: Ui, busy: boolean): void {
  ui.submit.disabled = busy;
  ui.username.disabled = busy;
}

/** A friendly line for each stage of a backend job. */
export function describeJob(job: Job): string {
  switch (job.status) {
    case "queued":
      return `Waiting for a free worker to build ${job.username}'s city…`;
    case "fetching":
      return `Fetching ${job.username}'s repositories from GitHub…`;
    case "building":
      return "Laying out the streets…";
    case "done":
      return "Done!";
    case "failed":
      return job.error ?? "Building this city failed.";
  }
}

/** The day/night button names what it will switch to. */
export function setDayNightLabel(ui: Ui, isNight: boolean): void {
  ui.dayNight.textContent = isNight ? "☀️ Day" : "🌙 Night";
}

export function showTooltip(ui: Ui, building: Building, x: number, y: number): void {
  // Built from text nodes rather than an HTML string, so names can never inject markup.
  const line = (text: string) => {
    const element = document.createElement("div");
    element.textContent = text;
    return element;
  };
  const name = document.createElement("strong");
  name.textContent = building.repo;
  const language = line(building.district);
  language.className = "language";
  language.style.color = building.color;

  const plural = (count: number, word: string) => `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
  ui.tooltip.replaceChildren(
    name,
    language,
    line(`${plural(building.floors, "floor")} · ${plural(building.commits, "commit")}`),
    line(`★ ${building.stars.toLocaleString()} · ${plural(building.forks, "fork")}`),
    line(
      building.recent_commits > 0
        ? `${plural(building.recent_commits, "commit")} in the last 90 days`
        : "Quiet for the last 90 days",
    ),
  );
  ui.tooltip.hidden = false;
  // Keep the tooltip on screen near the right and bottom edges.
  const { offsetWidth: width, offsetHeight: height } = ui.tooltip;
  const left = Math.min(x + 16, window.innerWidth - width - 8);
  const top = Math.min(y + 16, window.innerHeight - height - 8);
  ui.tooltip.style.transform = `translate(${left}px, ${top}px)`;
}

export function hideTooltip(ui: Ui): void {
  ui.tooltip.hidden = true;
}
