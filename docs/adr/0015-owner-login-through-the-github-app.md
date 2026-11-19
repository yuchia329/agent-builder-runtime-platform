# 0015. Owner login through the GitHub App, with stateless signed links

- Status: Accepted
- Date: 2026-11-18

## Context

ADR 0003 lets an agent act as its owner (`acts_as: owner`, the publisher): the gateway calls
GitHub with the owner's own token, which a person gets by logging in once. The M4 plan said
"GitHub OAuth App". Two questions had to be settled before building it:

1. Which kind of GitHub app the person logs in through.
2. How the person gets a login link, given there are no users yet: an owner is a principal such
   as `api:tenant-demo` (the tenant's API key), and roles are an open question after M4.

The gateway runs on Lambda (ADR 0007), so the login flow should not depend on memory kept between
the start request and GitHub's callback.

Options for 1:

- **A separate OAuth App.** Scopes are coarse: `repo` is read and write on every repository the
  person can reach, private ones included. Tokens do not expire. A second app to register.
- **The team bot's GitHub App** (user-to-server tokens). The token can do only what both the App
  (its permissions, its installed repositories) and the person may do: here Issues on the sandbox.
  It expires after 8 hours and comes with a 6-month refresh token that GitHub replaces on each
  refresh. Actions show as the person, marked as done through the App.

Options for 2:

- **The gateway signs the link** and puts it in the owner run's tool error.
- **A control-plane endpoint behind the API key** returns the link. A contract change and work in
  another service, for a distinction (several users per tenant) that does not exist yet.

## Decision

- Owners log in through the tenant's GitHub App. Settings: `GITHUB_OAUTH_CLIENT_ID`,
  `GITHUB_OAUTH_CLIENT_SECRET` (the App's client id and secret), `OAUTH_STATE_SECRET`,
  `GATEWAY_PUBLIC_URL`. The App's redirect URI is `<GATEWAY_PUBLIC_URL>/oauth/github/callback`;
  token expiry stays on.
- An owner run without a usable login fails with a tool error naming
  `<GATEWAY_PUBLIC_URL>/oauth/github/start?ticket=...`. The ticket is a note signed with
  `OAUTH_STATE_SECRET` (HMAC-SHA256): tenant, owner, provider, 10 minutes.
- No server memory between requests: `/start` makes a nonce, keeps it in an HttpOnly, SameSite=Lax
  cookie, and sends GitHub a signed `state` (tenant, owner, nonce, 10 minutes) and a PKCE S256
  challenge. The verifier is derived from the nonce with the secret, never stored. `/callback`
  requires a valid state whose nonce equals the cookie's, so a login finishes in the browser that
  started it.
- The token pair, expiry times and GitHub login are stored in the `TokenStore` under
  `(tenant, owner, github)`. Calls refresh it within 5 minutes of expiry, and once when GitHub
  answers 401, under a lock, saving the new pair. A refused refresh deletes the login and asks
  for a new one.
- `/oauth/*` needs no run token (a browser calls it). Pages and the request log never show a
  ticket, state, code or token.
- Accepted for now: anyone holding a valid link can connect their GitHub account as that owner.
  With one API key per tenant there is no other user to take it from. When users and roles
  arrive, the link moves behind the user's own sign-in (the control plane hands it out), and the
  ticket gains the user it is for.

## Consequences

- No second GitHub app; an owner's token is limited by the App's permissions and installation,
  the same limits as the team bot plus the person's own access.
- The flow works on Lambda unchanged; the secret must be the same on every instance.
- Logins live in the `TokenStore`: with the in-memory store (local) a restart forgets them; SSM
  (M6) keeps them.
- Another provider (Slack, Notion) adds a provider class next to `oauth/github.py` and its own
  routes; tickets, state and refresh handling are shared.
- Rotating `OAUTH_STATE_SECRET` voids links in flight (10 minutes at most), not stored logins.
