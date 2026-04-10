const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api/v1";
const TOKEN_STORAGE_KEY = "owlclaw_token";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

type ApiFetchOptions = Omit<RequestInit, "headers"> & {
  headers?: HeadersInit;
};

function readToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const token = readToken();
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    const fallback = { error: { code: "http_error", message: response.statusText } };
    const payload = (await response.json().catch(() => fallback)) as {
      error?: { code?: string; message?: string };
    };
    throw new ApiError(
      response.status,
      payload.error?.code ?? "http_error",
      payload.error?.message ?? "Request failed"
    );
  }

  return (await response.json()) as T;
}

export function saveApiToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}
