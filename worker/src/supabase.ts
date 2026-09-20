import { validateAssessment, type Assessment } from "./contracts";

export interface Env {
  SUPABASE_URL: string;
  SUPABASE_PUBLISHABLE_KEY: string;
  SUPABASE_READER_JWT: string;
}
export const DATABASE_PATH = "/rest/v1/rpc/get_latest_risk_assessment";

// Stream limits apply even when Content-Length is absent or dishonest.
export async function readJson(message: Request | Response, maximumBytes: number): Promise<unknown> {
  const reader = message.body?.getReader();
  if (!reader) throw new Error("Missing body");
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maximumBytes) throw new Error("Body limit");
      chunks.push(value);
    }
  } catch (error) {
    void reader.cancel().catch(() => undefined);
    throw error;
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes));
}

export function configuration(env: Env): { url: string; key: string; jwt: string } {
  // Fixed hosted Supabase origin only: no arbitrary endpoints, paths, redirects or ports.
  if (!/^https:\/\/[a-z0-9]+\.supabase\.co\/?$/.test(env.SUPABASE_URL)
    || !/^sb_publishable_[A-Za-z0-9_-]+$/.test(env.SUPABASE_PUBLISHABLE_KEY)
    || typeof env.SUPABASE_READER_JWT !== "string" || env.SUPABASE_READER_JWT.length > 8192
    || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(env.SUPABASE_READER_JWT)) {
    throw new Error("Invalid configuration");
  }
  const payload = env.SUPABASE_READER_JWT.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  const claims = JSON.parse(atob(payload.padEnd(Math.ceil(payload.length / 4) * 4, "=")));
  // This is a configuration guard, not signature verification. Supabase verifies
  // the signed JWT and enforces the role's function-only grants on every request.
  if (claims.role !== "risk_api_reader" || !Number.isSafeInteger(claims.exp)
    || claims.exp <= Date.now() / 1000) throw new Error("Invalid configuration");
  return { url: new URL(DATABASE_PATH, env.SUPABASE_URL).href,
    key: env.SUPABASE_PUBLISHABLE_KEY, jwt: env.SUPABASE_READER_JWT };
}

export async function latestAssessment(env: Env): Promise<Assessment> {
  let stage: "CONFIGURATION" | "SUPABASE_FETCH_TIMEOUT" | "SUPABASE_FETCH_ERROR" | "SUPABASE_HTTP_STATUS" | "RESPONSE_READ" | "RESPONSE_VALIDATION" = "CONFIGURATION";
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    const config = configuration(env);
    const abort = new AbortController();
    timeout = setTimeout(() => abort.abort(), 5000);
    stage = "SUPABASE_FETCH_ERROR";
    // Empty args use the database's fixed scope.
    let response: Response;
    try {
      response = await fetch(config.url, {
        // Workers supports manual/follow only. Never follow redirects with credentials;
        // the HTTP-200-only gate below rejects every redirect response.
        method: "POST", redirect: "manual", signal: abort.signal,
        headers: { "Content-Type": "application/json", Accept: "application/json",
          apikey: config.key, Authorization: `Bearer ${config.jwt}`, "Cache-Control": "no-store" },
        body: "{}",
      });
    } catch (error) {
      // Only our five-second timer aborts this controller. Never emit exception data.
      stage = abort.signal.aborted ? "SUPABASE_FETCH_TIMEOUT" : "SUPABASE_FETCH_ERROR";
      throw error;
    }
    stage = "SUPABASE_HTTP_STATUS";
    if (response.status !== 200 || response.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
      void response.body?.cancel().catch(() => undefined);
      throw new Error("Database unavailable");
    }
    stage = "RESPONSE_READ";
    const payload = await readJson(response, 65_536);
    stage = "RESPONSE_VALIDATION";
    return validateAssessment(payload);
  } catch (error) {
    // Only a fixed identifier: never include the error, configuration, or payload.
    console.error(`risk_api_stage_failure=${stage}`);
    throw error;
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
  }
}
