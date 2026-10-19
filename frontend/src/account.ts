// Signing in with GitHub. Optional: the server says whether it's set up (GET /api/auth/me).
//
// Signing in adds a "My city" shortcut, a higher limit on building new cities, and (when the
// server is set up for it) the private repos you choose to share, shown only to you. The session
// lives in an HttpOnly cookie that the browser sends by itself, so this code never sees a token
// or a session id.

import { getAuthStatus, signInUrl, signOut, type AuthStatus, type AuthUser } from "./api";

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
  /** Signed out: the server has forgotten the private repos you shared. */
  signedOut(): void;
}

/** The account controls at the right end of the top bar. */
export class Account {
  constructor(
    private readonly root: HTMLElement,
    private readonly actions: AccountActions,
  ) {}

  /** Ask the server who is signed in, show the matching controls, and return what it said. */
  async load(): Promise<AuthStatus | null> {
    try {
      const status = await getAuthStatus();
      if (status.enabled) {
        this.render(status.user, status.private_repos?.install_url ?? null);
      }
      return status;
    } catch {
      // No backend (like the static demo) or a hiccup: simply offer no sign-in.
      this.root.hidden = true;
      return null;
    }
  }

  private render(user: AuthUser | null, installUrl: string | null = null): void {
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
        this.actions.signedOut();
        // Signing out here doesn't sign you out of github.com, which matters on a shared
        // computer. (Signing in always lets you pick the account, so nobody lands in yours.)
        this.actions.message("Signed out. On a shared computer, also sign out of github.com.");
      } catch {
        signOutButton.disabled = false;
        this.actions.message("Couldn't sign out. Please try again.", true);
      }
    });
    const controls: HTMLElement[] = [myCity];
    if (installUrl !== null) {
      // GitHub's page for choosing which private repos the site may read. Afterwards GitHub
      // sends you back here (see takeAppSetup), and they're fetched right away.
      const share = document.createElement("a");
      share.href = installUrl;
      share.className = "link-button";
      share.textContent = "🔒 Share private repos";
      share.title = "Choose which private repos appear in your city. Only you will see them.";
      controls.push(share);
    }
    this.root.replaceChildren(...controls, signOutButton);
  }
}

/**
 * True when GitHub just sent you back from choosing private repos to share (it adds
 * setup_action and installation_id to the address). Removes those from the address bar.
 */
export function takeAppSetup(): boolean {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("setup_action")) {
    return false;
  }
  url.searchParams.delete("setup_action");
  url.searchParams.delete("installation_id");
  window.history.replaceState(null, "", url.pathname + url.search);
  return true;
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
