// The card in the middle of the screen: welcome, building progress, or an error.
// Everything is built from DOM nodes with textContent, so user input can never inject markup.

import type { Job } from "./api";

const STEPS = ["Waiting for a free worker", "Fetching repositories from GitHub", "Laying out the city"];
const STEP_FOR_STATUS: Record<Job["status"], number> = {
  queued: 0,
  fetching: 1,
  building: 2,
  done: 3,
  failed: -1,
};

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text?: string,
  className?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className !== undefined) node.className = className;
  return node;
}

export class Overlay {
  constructor(private readonly root: HTMLElement) {}

  welcome(): void {
    const demo = element("a", "explore the demo city");
    demo.href = "/?demo";
    const hint = element("p");
    hint.append("Enter a GitHub username above to build their city, or ", demo, ".");
    this.render(
      element("h2", "Every repo is a building"),
      element("p", "Floors are commits, districts are languages, lit windows are recent work, and the city plays its own music."),
      hint,
    );
  }

  /** Progress while a city is being built. Without a job yet, just "looking up...". */
  building(username: string, job?: Job): void {
    const current = job ? STEP_FOR_STATUS[job.status] : -1;
    const steps = element("ol", undefined, "steps");
    STEPS.forEach((label, index) => {
      const state = index < current ? "done" : index === current ? "active" : "todo";
      steps.append(element("li", label, state));
    });
    this.render(
      element("div", undefined, "spinner large"),
      element("h2", `Building ${username}'s city`),
      job ? steps : element("p", "Looking them up on GitHub…"),
    );
  }

  error(message: string, retry?: () => void): void {
    const nodes: Node[] = [element("h2", "Something went wrong"), element("p", message, "error-text")];
    if (retry) {
      const button = element("button", "Try again");
      button.type = "button";
      button.addEventListener("click", retry);
      nodes.push(button);
    }
    this.render(...nodes);
  }

  hide(): void {
    this.root.hidden = true;
  }

  private render(...children: Node[]): void {
    const card = element("div", undefined, "card");
    card.append(...children);
    this.root.replaceChildren(card);
    this.root.hidden = false;
  }
}
