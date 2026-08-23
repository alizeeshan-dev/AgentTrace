import type { Run, RunDetail, Task } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${endpoint}`;
  try {
    const response = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
    });

    if (!response.ok) {
      if (response.status === 404) {
        throw new ApiError(404, `Endpoint ${endpoint} not implemented in backend.`);
      }
      throw new ApiError(response.status, `API request failed: ${response.statusText}`);
    }

    return await response.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(503, "Could not connect to the backend server. Make sure it is running.");
  }
}

export const api = {
  getRuns: () => fetchApi<Run[]>("/runs"),
  getRun: (id: string) => fetchApi<RunDetail>(`/runs/${id}`),
  getTasks: () => fetchApi<Task[]>("/tasks"),
  createRun: (payload: any) => fetchApi<{ run_id: string }>("/runs", {
    method: "POST",
    body: JSON.stringify(payload)
  }),
  getExperiments: () => fetchApi<any[]>("/experiments"),
  getHealth: () => fetchApi<{status: string}>("/health"),
};
