export class LoadError extends Error {
  constructor(
    message: string,
    readonly url: string,
    readonly status: number | null,
  ) {
    super(message);
  }
}

/** Fetch with a timeout and abort support. The site is static; failures are shown, never hidden. */
export async function fetchText(url: string, signal?: AbortSignal, timeoutMs = 15000): Promise<string> {
  const timeout = AbortSignal.timeout(timeoutMs);
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
  let res: Response;
  try {
    res = await fetch(url, { signal: combined, cache: "no-cache" });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new LoadError(timeout.aborted ? "The request timed out." : "The network request failed.", url, null);
  }
  if (!res.ok) throw new LoadError(`The server answered ${res.status}.`, url, res.status);
  return res.text();
}

export async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const text = await fetchText(url, signal);
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new LoadError("The file is not valid JSON.", url, null);
  }
}
