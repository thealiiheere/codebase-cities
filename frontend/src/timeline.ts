// The year slider: scrub or fly through the years while the city grows and the music changes.

import type { CityResponse } from "./api";
import type { CitySource } from "./sources";

export interface TimelineElements {
  container: HTMLElement;
  slider: HTMLInputElement;
  play: HTMLButtonElement;
  start: HTMLElement;
  year: HTMLElement;
  spinner: HTMLElement;
}

export interface TimelineHost {
  /** Draw this city (keeping the camera where it is). */
  show(response: CityResponse): void;
  /** The year being shown changed: undefined means "now". Used to update the share link. */
  yearChanged(year: number | undefined): void;
  /** How long to stay on each year while flying through, in milliseconds. */
  stepMilliseconds(): number;
  showError(message: string): void;
}

// While dragging, wait this long after the slider stops before fetching, so sweeping across
// ten years asks for one city instead of ten.
const SCRUB_DELAY_MS = 120;

export class Timeline {
  private source: CitySource | null = null;
  private firstYear = 0;
  private lastYear = 0;
  private scrubTimer: number | undefined;
  private flyTimer: number | undefined;
  // Every request gets a number; a response is only shown if it's still the newest request.
  // Otherwise a slow 2017 response could arrive after 2019's and overwrite it.
  private requestNumber = 0;

  constructor(
    private readonly elements: TimelineElements,
    private readonly host: TimelineHost,
  ) {
    elements.slider.addEventListener("input", () => {
      this.stopFlying();
      this.showLabel();
      window.clearTimeout(this.scrubTimer);
      this.scrubTimer = window.setTimeout(() => void this.goTo(this.selectedYear()), SCRUB_DELAY_MS);
    });
    elements.play.addEventListener("click", () => (this.flying ? this.stopFlying() : this.fly()));
  }

  /** Show the slider for a new city. `year` (from a share link) jumps straight to that year. */
  attach(source: CitySource, latest: CityResponse, year?: number): void {
    this.stopFlying();
    this.source = source;
    this.firstYear = Number(latest.timeline.start.slice(0, 4));
    this.lastYear = Number(latest.timeline.end.slice(0, 4));
    const { slider, container, start } = this.elements;
    slider.min = String(this.firstYear);
    slider.max = String(this.lastYear);
    slider.value = String(this.lastYear);
    start.textContent = String(this.firstYear);
    // A profile younger than a year has nothing to scrub through.
    container.hidden = this.lastYear <= this.firstYear;
    this.showLabel();
    if (year !== undefined && year >= this.firstYear && year < this.lastYear) {
      slider.value = String(year);
      this.showLabel();
      void this.goTo(year);
    }
  }

  detach(): void {
    this.stopFlying();
    this.source = null;
    this.elements.container.hidden = true;
  }

  private get flying(): boolean {
    return this.flyTimer !== undefined;
  }

  private selectedYear(): number {
    return Number(this.elements.slider.value);
  }

  /** The API date for a year: the last day of it, or nothing (today) for the current year. */
  private untilFor(year: number): string | undefined {
    return year >= this.lastYear ? undefined : `${year}-12-31`;
  }

  private showLabel(): void {
    const year = this.selectedYear();
    this.elements.year.textContent = year >= this.lastYear ? "Now" : String(year);
  }

  private async goTo(year: number): Promise<void> {
    if (this.source === null) {
      return;
    }
    const request = ++this.requestNumber;
    this.elements.spinner.hidden = false;
    try {
      const response = await this.source.city(this.untilFor(year));
      if (request === this.requestNumber) {
        this.host.show(response);
        this.host.yearChanged(year >= this.lastYear ? undefined : year);
        this.prefetch(year + 1);
      }
    } catch (error) {
      if (request === this.requestNumber) {
        this.stopFlying();
        this.host.showError(error instanceof Error ? error.message : "Couldn't load that year.");
      }
    } finally {
      if (request === this.requestNumber) {
        this.elements.spinner.hidden = true;
      }
    }
  }

  /** Start fetching a year before it's needed, so flying through never waits on the network. */
  private prefetch(year: number): void {
    if (this.source !== null && year <= this.lastYear) {
      void this.source.city(this.untilFor(year)).catch(() => undefined);
    }
  }

  /** Fly through the years, one step at a time, from the start (or from where the slider is). */
  private fly(): void {
    if (this.selectedYear() >= this.lastYear) {
      this.elements.slider.value = String(this.firstYear);
    }
    this.elements.play.textContent = "⏸";
    const step = () => {
      const year = this.selectedYear();
      this.elements.slider.value = String(year);
      this.showLabel();
      void this.goTo(year).then(() => {
        if (!this.flying) return;
        if (year >= this.lastYear) {
          this.stopFlying();
        } else {
          this.elements.slider.value = String(year + 1);
          this.flyTimer = window.setTimeout(step, this.host.stepMilliseconds());
        }
      });
    };
    this.flyTimer = window.setTimeout(step, 0);
  }

  private stopFlying(): void {
    window.clearTimeout(this.flyTimer);
    this.flyTimer = undefined;
    this.elements.play.textContent = "▶";
  }
}
