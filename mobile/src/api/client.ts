import { joinUrl, nextFallbackHost, rememberWorkingHost, usingDefaultHost } from '../native/config';
import { getServerUrl, onSignedOut } from '../native/session';

export type ApiErrorKind = 'http' | 'network' | 'timeout' | 'aborted';

export class ApiError extends Error {
  status: number;
  kind: ApiErrorKind;
  constructor(message: string, status: number, kind: ApiErrorKind) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.kind = kind;
  }
}

export const isAbort = (e: unknown): boolean => e instanceof ApiError && e.kind === 'aborted';
export const isOffline = (e: unknown): boolean =>
  e instanceof ApiError && (e.kind === 'network' || e.kind === 'timeout');
export const isNotFound = (e: unknown): boolean => e instanceof ApiError && e.status === 404;

export function errorMessage(e: unknown, fallback = 'Something went wrong.'): string {
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  query?: Query;
  signal?: AbortSignal;
  timeoutMs?: number;
};

export function buildQuery(query?: Query): string {
  if (!query) return '';
  const parts: string[] = [];
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '' || v === false) continue;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

let signingOut = false;

async function handleUnauthorized() {
  if (signingOut) return;
  signingOut = true;
  try {
    await onSignedOut();
  } catch {
    /* sign-out is best effort */
  } finally {
    signingOut = false;
  }
}

function detailOf(data: unknown): string | null {
  if (!data || typeof data !== 'object') return null;
  const d = (data as { detail?: unknown; message?: unknown }).detail ??
    (data as { message?: unknown }).message;
  if (typeof d === 'string') return d;
  // FastAPI validation errors arrive as [{loc, msg}, ...].
  if (Array.isArray(d) && d.length && typeof d[0]?.msg === 'string') return d[0].msg;
  return d ? JSON.stringify(d) : null;
}

export async function resolveServerUrl(): Promise<string> {
  return getServerUrl();
}

/** Turns a site-relative path from the API (e.g. "/api/deals/x/image") into an absolute URL. */
export function absoluteUrl(base: string, path: string | null | undefined): string | null {
  if (!path) return null;
  if (/^(https?:|data:)/i.test(path)) return path;
  return joinUrl(base, path);
}

/** One attempt at `path` against `base`. Throws ApiError('network'|'timeout'|'aborted') or returns the raw Response. */
async function attempt(base: string, path: string, opts: RequestOptions): Promise<Response> {
  const url = joinUrl(base, path) + buildQuery(opts.query);
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, opts.timeoutMs ?? 25_000);

  const outer = opts.signal;
  const forward = () => controller.abort();
  if (outer) {
    if (outer.aborted) controller.abort();
    else outer.addEventListener('abort', forward);
  }

  try {
    return await fetch(url, {
      method: opts.method ?? 'GET',
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: controller.signal,
    });
  } catch (e) {
    if (timedOut) throw new ApiError('The server took too long to answer. Try again.', 0, 'timeout');
    if (controller.signal.aborted) throw new ApiError('Request cancelled.', 0, 'aborted');
    throw new ApiError(
      "Couldn't reach the server. Check your connection and try again.",
      0,
      'network',
    );
  } finally {
    clearTimeout(timer);
    outer?.removeEventListener('abort', forward);
  }
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const base = await getServerUrl();
  let res: Response;
  try {
    res = await attempt(base, path, opts);
  } catch (e) {
    // A genuine "can't even connect" failure (DNS/adblock/firewall — not a
    // timeout, not a cancel) on the default, unconfigured host: some phones'
    // DNS filtering or ad/tracker blockers block one hostname outright, so
    // try the other one this app answers to before giving up. Never kicks in
    // for a base URL someone explicitly configured on the Setup screen.
    const fallback = e instanceof ApiError && e.kind === 'network' && (await usingDefaultHost())
      ? nextFallbackHost(base)
      : null;
    if (!fallback) throw e;
    res = await attempt(fallback, path, opts);
    // Only trust this host going forward if it actually answered for real —
    // a fetch that "succeeds" with a blanket 404/5xx (e.g. a retired host
    // whose routing was disabled but that still resolves and connects) must
    // never get remembered, or every later request gets stuck failing
    // against it for the rest of the session.
    if (res.ok) rememberWorkingHost(fallback);
  }

  let data: unknown = null;
  try {
    const text = await res.text();
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/api/auth/')) void handleUnauthorized();
    const message = detailOf(data) ?? `Request failed (${res.status})`;
    throw new ApiError(message, res.status, 'http');
  }
  return data as T;
}
