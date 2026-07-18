import type {
  CallRecord,
  CallView,
  CreateCallInput,
  OutcomeType,
  StructuredCallOutcome,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1/calls${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail ?? `Caller request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const callerApi = {
  create(input: CreateCallInput): Promise<CallRecord> {
    return request("", { method: "POST", body: JSON.stringify(input) });
  },

  start(callId: string): Promise<unknown> {
    return request(`/${callId}/start`, { method: "POST" });
  },

  sendEvent(callId: string, event: unknown): Promise<unknown> {
    return request(`/${callId}/events`, {
      method: "POST",
      body: JSON.stringify(event),
    });
  },

  finalize(
    callId: string,
    requestedOutcome?: OutcomeType,
  ): Promise<StructuredCallOutcome> {
    return request(`/${callId}/finalize`, {
      method: "POST",
      body: JSON.stringify({ requested_outcome: requestedOutcome }),
    });
  },

  get(callId: string): Promise<CallView> {
    return request(`/${callId}`);
  },
};

