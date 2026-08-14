/* The API client: token storage, requests, and error classification.
 *
 * The API is bearer-token only and has no session or login endpoint, so the
 * token the operator pastes into the header is the whole of authentication.
 * It is kept in `localStorage` under one key so a reload does not lose it,
 * and the page says so out loud: a token in `localStorage` is readable by any
 * script that ever runs on this origin. "Forget token" removes it. When
 * `localStorage` is unavailable (private windows, blocked storage) the token
 * lives in memory for the life of the tab instead, and the page says that too.
 */

const TOKEN_KEY = "cfm.api-token";

/** Where the token actually ended up: "browser" (localStorage) or "memory". */
export let tokenStorage = "browser";

let memoryToken = "";

function storage() {
  try {
    const probe = "cfm.storage-probe";
    window.localStorage.setItem(probe, "1");
    window.localStorage.removeItem(probe);
    return window.localStorage;
  } catch {
    tokenStorage = "memory";
    return null;
  }
}

export function readToken() {
  const store = storage();
  if (store === null) {
    return memoryToken;
  }
  return store.getItem(TOKEN_KEY) ?? "";
}

export function writeToken(value) {
  const token = value.trim();
  memoryToken = token;
  const store = storage();
  if (store !== null) {
    if (token === "") {
      store.removeItem(TOKEN_KEY);
    } else {
      store.setItem(TOKEN_KEY, token);
    }
  }
  return token;
}

export function hasToken() {
  return readToken() !== "";
}

/** An error carrying the API's RFC 9457 problem document. */
export class ApiError extends Error {
  constructor(status, problem, retryAfter) {
    const detail = problem && typeof problem.detail === "string" ? problem.detail : "";
    super(detail || `The API answered ${status}.`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.errorCode = problem && typeof problem.error_code === "string" ? problem.error_code : "";
    this.title = problem && typeof problem.title === "string" ? problem.title : "";
    this.invalidParams = (problem && problem.invalid_params) || [];
    this.retryAfter = retryAfter;
  }
}

/** The request never reached the API (offline, DNS, CORS, server down). */
export class NetworkError extends Error {
  constructor(cause) {
    super("The API could not be reached.");
    this.name = "NetworkError";
    this.cause = cause;
  }
}

/** The token box is empty; no request was made. */
export class NoTokenError extends Error {
  constructor() {
    super("No API token has been entered.");
    this.name = "NoTokenError";
  }
}

export function createClient(apiPrefix) {
  async function request(path, { method = "GET", body = undefined, params = undefined } = {}) {
    if (!hasToken()) {
      throw new NoTokenError();
    }
    const url = new URL(`${apiPrefix}${path}`, window.location.origin);
    for (const [name, value] of Object.entries(params ?? {})) {
      if (value !== null && value !== undefined) {
        url.searchParams.set(name, String(value));
      }
    }
    const headers = { Authorization: `Bearer ${readToken()}`, Accept: "application/json" };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }

    let response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (cause) {
      throw new NetworkError(cause);
    }

    if (response.status === 204) {
      return null;
    }
    const contentType = response.headers.get("content-type") ?? "";
    const payload = contentType.includes("json") ? await response.json() : null;
    if (!response.ok) {
      throw new ApiError(response.status, payload, response.headers.get("retry-after"));
    }
    return payload;
  }

  /**
   * Every page of a list endpoint, in order.
   *
   * The API caps `limit` at 200, so a long list costs several round trips.
   * That is deliberate for the three views here, which show whole (small)
   * inventories; it is not a pattern to carry to an installation with tens of
   * thousands of assets. `maxPages` stops a runaway loop if `total` and the
   * returned pages ever disagree.
   */
  async function listAll(path, params = {}, { pageSize = 200, maxPages = 50 } = {}) {
    const items = [];
    let offset = 0;
    let total = 0;
    for (let page = 0; page < maxPages; page += 1) {
      const envelope = await request(path, { params: { ...params, limit: pageSize, offset } });
      items.push(...envelope.items);
      total = envelope.total;
      offset += envelope.items.length;
      if (envelope.items.length === 0 || items.length >= total) {
        break;
      }
    }
    return { items, total };
  }

  return { request, listAll };
}

/**
 * Turn any thrown error into something an operator can act on.
 *
 * 401 and 403 are kept firmly apart, because they mean different things and
 * call for different reactions: 401 is about the token itself, 403 is about
 * what that (accepted) token is allowed to do. The API's own `detail` is
 * always shown underneath, verbatim.
 */
export function describeError(error) {
  if (error instanceof NoTokenError) {
    return {
      level: "warning",
      heading: "No API token entered",
      detail: "Paste a token into the field at the top of the page to load data.",
      code: "",
    };
  }
  if (error instanceof NetworkError) {
    return {
      level: "error",
      heading: "The API could not be reached",
      detail: "The request never got a response. Check that the API is running and reload.",
      code: "",
    };
  }
  if (!(error instanceof ApiError)) {
    return {
      level: "error",
      heading: "Unexpected error in the page",
      detail: String(error && error.message ? error.message : error),
      code: "",
    };
  }

  const base = { level: "error", detail: error.detail, code: error.errorCode };
  if (error.status === 401) {
    return {
      ...base,
      heading:
        error.errorCode === "auth.credentials_required"
          ? "Not authenticated (401): no token was sent"
          : "Not authenticated (401): the API rejected this token",
      hint:
        error.errorCode === "auth.credentials_required"
          ? "Enter a token above."
          : "The token may be mistyped, revoked, or expired. The API deliberately does not say which.",
    };
  }
  if (error.status === 403) {
    const scoped = error.errorCode === "auth.scope_forbidden";
    return {
      ...base,
      heading: scoped
        ? "Refused (403): no site grant covers this record"
        : "Refused (403): this token's role may not do that",
      hint: scoped
        ? "The token authenticated fine, but its write authority does not reach this record's site. Site grants are only readable with an admin token, so this page cannot know in advance which sites a token covers - it finds out by asking."
        : "The token authenticated fine. Writing needs the engineer or admin role; a viewer token reads what its site grants cover and changes nothing.",
    };
  }
  if (error.status === 429) {
    return {
      ...base,
      heading: "Rate limited (429)",
      hint: error.retryAfter
        ? `Too many failed authentications from this source. Retry after ${error.retryAfter} seconds.`
        : "Too many failed authentications from this source.",
    };
  }
  if (error.status === 409) {
    return { ...base, heading: "Refused (409): conflicts with the current state" };
  }
  if (error.status === 422) {
    const params = error.invalidParams
      .map((entry) => `${entry.name}: ${entry.reason}`)
      .join("; ");
    return {
      ...base,
      heading: "Rejected (422): the request broke a rule",
      hint: params || undefined,
    };
  }
  if (error.status === 404) {
    return { ...base, heading: "Not found (404)" };
  }
  return { ...base, heading: `The API answered ${error.status}` };
}
