// The page's HTML controls (defined in index.html): finding them and updating them.

import type { Building, MusicParams } from "./api";
import type { TimelineElements } from "./timeline";

export interface Ui {
  form: HTMLFormElement;
  username: HTMLInputElement;
  submit: HTMLButtonElement;
  status: HTMLElement;
  play: HTMLButtonElement;
  volume: HTMLInputElement;
  dayNight: HTMLButtonElement;
  viewport: HTMLElement;
  overlay: HTMLElement;
  timeline: TimelineElements;
  cityInfo: HTMLElement;
  share: HTMLButtonElement;
  nowPlaying: HTMLElement;
  tooltip: HTMLElement;
  account: HTMLElement;
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
    overlay: find("#overlay"),
    timeline: {
      container: find("#timeline"),
      slider: find("#timeline-slider"),
      play: find("#timeline-play"),
      start: find("#timeline-start"),
      date: find("#timeline-date"),
    },
    cityInfo: find("#city-info"),
    share: find("#share"),
    nowPlaying: find("#now-playing"),
    tooltip: find("#tooltip"),
    account: find("#account"),
  };
}

/** Show a status message for a few seconds, then clear it (unless something replaced it). */
export function flashStatus(ui: Ui, text: string, milliseconds = 2500): void {
  setStatus(ui, text);
  window.setTimeout(() => {
    if (ui.status.textContent === text) {
      setStatus(ui, "");
    }
  }, milliseconds);
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

/** The day/night button names what it will switch to. */
export function setDayNightLabel(ui: Ui, isNight: boolean): void {
  ui.dayNight.textContent = isNight ? "☀️ Day" : "🌙 Night";
}

/** Details of a building; `floors` is how many it has at the date on screen. */
export function showTooltip(ui: Ui, building: Building, floors: number, x: number, y: number): void {
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
    line(
      floors < building.floors
        ? `${floors} of ${plural(building.floors, "floor")} so far · ${plural(building.commits, "commit")} today`
        : `${plural(building.floors, "floor")} · ${plural(building.commits, "commit")}`,
    ),
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
