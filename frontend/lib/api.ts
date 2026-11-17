// HTTP client to the FastAPI backend.
//
// Purpose: a single place for all API calls, with uniform errors (ApiError) so that
// components only need to display the message, without parsing responses themselves.
//
// Important: the frontend communicates only with this backend. Odoo credentials are NOT here
// (only the backend may hold them). The only setting is NEXT_PUBLIC_API_URL.
//
// Related: used by hooks/useInventory.ts, hooks/useMovements.ts, hooks/useSyncInventory.ts.

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// Time limit for a single request. Without this, a hanging backend causes the loading skeleton to display
// indefinitely and the "Try again" button never appears (and the sale form is locked).
const REQUEST_TIMEOUT_MS = 10_000;

// API error with the HTTP status and the machine code from the backend (e.g. INSUFFICIENT_STOCK).
// status 0 = server unreachable (network down / backend down).
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown, // e.g. {available, requested} for INSUFFICIENT_STOCK
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
  let res: Response;
  try {
    // no-store: do not let the browser keep outdated stock (consistent with the backend header).
    res = await fetch(`${BASE_URL}${path}`, {
      ...init,
      cache: "no-store",
      credentials: "include", // the admin session cookie (httpOnly) is sent along to the backend
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new ApiError(0, "TIMEOUT", "The server took too long to respond. Try again.");
    }
    throw new ApiError(0, "NETWORK_ERROR", "Cannot reach the server. Check that the backend is running.");
  }
  if (!res.ok) {
    // The backend always uses the form {"error": {"code", "message"}}; otherwise, use a generic message.
    const body = await res.json().catch(() => null);
    throw new ApiError(
      res.status,
      body?.error?.code ?? "HTTP_ERROR",
      body?.error?.message ?? `Request failed (${res.status})`,
      body?.error?.details,
    );
  }
  if (res.status === 204) return undefined as T; // e.g. logout: no response body
  return res.json() as Promise<T>;
}

// Empty/undefined parameters are not sent to the server.
export function apiGet<T>(path: string, params: Record<string, string | undefined> = {}): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) query.set(key, value);
  }
  const qs = query.toString();
  return request<T>(qs ? `${path}?${qs}` : path);
}

// timeoutMs is optional: an Odoo connection check can take longer than an ordinary request.
export function apiPost<T>(path: string, body: unknown = {}, timeoutMs?: number): Promise<T> {
  return request<T>(
    path,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    timeoutMs,
  );
}

// Modifies data (e.g. saving the Odoo configuration, changing the password).
export function apiPut<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Deletes/disconnects something on the server (e.g. Disconnect of the Odoo configuration).
export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}
