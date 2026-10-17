// Signing in with GitHub. Optional: the server says whether it's set up (GET /api/auth/me).
//
// Signing in doesn't change what anyone can see (every city is public). It adds a "My city"
// shortcut and a higher limit on building new cities. The session lives in an HttpOnly cookie
// that the browser sends by itself, so this code never sees a token or a session id.

import { getAuthStatus, signInUrl, signOut, type AuthUser } from "./api";

/** Messages for the ?auth_error=<code> the server adds when a sign-in fails. */
const SIGN_IN_ERRORS: Record<string, string> = {
  denied: "Sign-in was cancelled.",
  state: "That sign-in didn't start in this browser. Please try again.",
  expired: "That sign-in took too long. Please try again.",
  github: "GitHub couldn't confirm the sign-in. Please try again.",
};

export interface AccountActions {
  openCity(username: string): void;
  message(text: string, isError?: boolean): void;
}

/** The account controls at the right end of the top bar. */
export class Account {
  constructor(
    private readonly root: HTMLElement,
    private readonly actions: AccountActions,
  ) {}

  /** Ask the server who is signed in, and show the matching controls. */
  async load(): Promise<void> {
    try {
      const status = await getAuthStatus();
      if (status.enabled) {
        this.render(status.user);
      }
    } catch {
      // No backend (like the static demo) or a hiccup: simply offer no sign-in.
      this.root.hidden = true;
    }
  }

  private render(user: AuthUser | null): void {
    this.root.hidden = false;
    if (user === null) {
      const signIn = button("Sign in with GitHub");
      signIn.addEventListener("click", () => {
        // A real page visit, not a fetch: the browser goes to GitHub's sign-in page and comes
        // back here afterwards (to your own city when starting from the home page).
        window.location.href = signInUrl(window.location.pathname + window.location.search);
      });
      this.root.replaceChildren(signIn);
      return;
    }

    const avatar = document.createElement("img");
    // GitHub serves avatars at the size asked for with s=...: 44 px for a sharp 22 px image.
    avatar.src = `${user.avatar_url}${user.avatar_url.includes("?") ? "&" : "?"}s=44`;
    avatar.alt = "";
    avatar.className = "avatar";
    const myCity = button("My city");
    myCity.prepend(avatar);
    myCity.title = `Signed in as ${user.login}`;
    myCity.addEventListener("click", () => this.actions.openCity(user.login));

    const signOutButton = button("Sign out");
    signOutButton.className = "link-button";
    signOutButton.addEventListener("click", async () => {
      signOutButton.disabled = true;
      try {
        await signOut();
        this.render(null);
        this.actions.message("Signed out.");
      } catch {
        signOutButton.disabled = false;
        this.actions.message("Couldn't sign out. Please try again.", true);
      }
    });
    this.root.replaceChildren(myCity, signOutButton);
  }
}

/**
 * If GitHub sent the visitor back with ?auth_error=..., return what went wrong and remove it
 * from the address bar (so a reload or a shared link doesn't show the error again).
 */
export function takeSignInError(): string | null {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("auth_error");
  if (code === null) {
    return null;
  }
  url.searchParams.delete("auth_error");
  window.history.replaceState(null, "", url.pathname + url.search);
  return SIGN_IN_ERRORS[code] ?? "Signing in didn't work. Please try again.";
}

function button(text: string): HTMLButtonElement {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = text;
  return element;
}
