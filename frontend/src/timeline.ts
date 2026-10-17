// The timeline slider: scrub or play through the years while every building rises floor by
// floor as its commits land.
//
// Today's city already holds each building's history (Building.growth and Building.lights), so
// moving through time needs no network at all: the slider picks a day, and the 3D view shows
// the city as it was at the end of that day.

export interface TimelineElements {
  container: HTMLElement;
  slider: HTMLInputElement;
  play: HTMLButtonElement;
  start: HTMLElement;
  date: HTMLElement;
}

export interface TimelineHost {
  /** Show the city at the end of `date` (YYYY-MM-DD); null means today's city. */
  showDate(date: string | null): void;
  /**
   * The year on screen changed; undefined means "now" (today, or any day of this year).
   * Used for the share link and the music, which change once a year rather than every day.
   */
  yearChanged(year: number | undefined): void;
}

const DAY_MS = 24 * 60 * 60 * 1000;
// Playing the whole history takes about 3 seconds per year, but at least 10 seconds (so a
// young profile doesn't flash by) and at most 30 (so a long one doesn't drag).
const SECONDS_PER_YEAR = 3;
const MIN_PLAY_SECONDS = 10;
const MAX_PLAY_SECONDS = 30;

export class Timeline {
  // The slider's values are day numbers (days since 1970-01-01), so one step is one day.
  private firstDay = 0;
  private lastDay = 0;
  private year: number | undefined;
  /** The pending requestAnimationFrame while playing. */
  private frame: number | undefined;

  constructor(
    private readonly elements: TimelineElements,
    private readonly host: TimelineHost,
  ) {
    elements.slider.addEventListener("input", () => {
      this.pause();
      this.goTo(Number(elements.slider.value));
    });
    elements.play.addEventListener("click", () => (this.playing ? this.pause() : this.play()));
  }

  /**
   * Show the slider for a new city, running from `first` to `last` (YYYY-MM-DD) and starting at
   * the end. `year` (from a share link) jumps straight to the end of that year instead.
   */
  attach(first: string, last: string, year?: number): void {
    this.pause();
    this.firstDay = dayNumber(first);
    this.lastDay = Math.max(dayNumber(last), this.firstDay);
    const { slider, container, start } = this.elements;
    slider.min = String(this.firstDay);
    slider.max = String(this.lastDay);
    start.textContent = first.slice(0, 4);
    // A city younger than a week has nothing to play through.
    container.hidden = this.lastDay - this.firstDay < 7;
    this.year = undefined;
    const target = year === undefined ? this.lastDay : dayNumber(`${year}-12-31`);
    this.goTo(Math.min(Math.max(target, this.firstDay), this.lastDay));
  }

  detach(): void {
    this.pause();
    this.elements.container.hidden = true;
  }

  private get playing(): boolean {
    return this.frame !== undefined;
  }

  /** Show one day (the last day means today's city). */
  private goTo(day: number): void {
    const now = day >= this.lastDay;
    const date = now ? null : isoDate(day);
    this.elements.slider.value = String(day);
    this.elements.date.textContent = date === null ? "Now" : formatDate(date, { day: false });
    this.host.showDate(date);

    // Days of the current year count as "now": the share link and music for them are today's.
    const year = date === null || date.slice(0, 4) === isoDate(this.lastDay).slice(0, 4) ? undefined : Number(date.slice(0, 4));
    if (year !== this.year) {
      this.year = year;
      this.host.yearChanged(year);
    }
  }

  /**
   * Play from where the slider is (or from the start, if it's at the end). Each animation frame
   * moves the date forward by however much time passed since the last frame, so playback runs
   * at the same speed on a fast or a slow computer.
   */
  private play(): void {
    if (Number(this.elements.slider.value) >= this.lastDay) {
      this.goTo(this.firstDay);
    }
    const span = this.lastDay - this.firstDay;
    const seconds = Math.min(Math.max((span / 365) * SECONDS_PER_YEAR, MIN_PLAY_SECONDS), MAX_PLAY_SECONDS);
    const daysPerMs = span / (seconds * 1000);
    // The slider only holds whole days; keep the exact position here.
    let position = Number(this.elements.slider.value);
    let previous: number | undefined;
    this.elements.play.textContent = "⏸";

    const step = (time: number) => {
      position = Math.min(position + (previous === undefined ? 0 : (time - previous) * daysPerMs), this.lastDay);
      previous = time;
      this.goTo(Math.floor(position));
      if (position >= this.lastDay) {
        this.pause();
      } else {
        this.frame = requestAnimationFrame(step);
      }
    };
    this.frame = requestAnimationFrame(step);
  }

  private pause(): void {
    if (this.frame !== undefined) {
      cancelAnimationFrame(this.frame);
      this.frame = undefined;
    }
    this.elements.play.textContent = "▶";
  }
}

/** Days since 1970-01-01 for a YYYY-MM-DD date (in UTC, so time zones never shift a day). */
function dayNumber(date: string): number {
  return Math.floor(Date.parse(`${date}T00:00:00Z`) / DAY_MS);
}

function isoDate(day: number): string {
  return new Date(day * DAY_MS).toISOString().slice(0, 10);
}

// Made once and reused: building a date formatter is slow (~0.5 ms), too slow to do on every
// frame while the timeline plays.
const WITH_DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const MONTH_ONLY = new Intl.DateTimeFormat("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });

/** "14 Mar 2019", or "Mar 2019" without the day. */
export function formatDate(date: string, { day = true } = {}): string {
  return (day ? WITH_DAY : MONTH_ONLY).format(new Date(`${date}T00:00:00Z`));
}

/** The day before a YYYY-MM-DD date. */
export function dayBefore(date: string): string {
  return isoDate(dayNumber(date) - 1);
}
