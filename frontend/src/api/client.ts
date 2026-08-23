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

  // External repository workflow
  registerExternalRepository: (payload: any) => fetchApi<any>("/repositories/external", {
    method: "POST",
    body: JSON.stringify(payload)
  }),
  getRepositories: () => fetchApi<any[]>("/repositories"),
  setRepositoryTrust: (id: string, payload: any) => fetchApi<any>(`/repositories/${id}/trust`, {
    method: "PATCH",
    body: JSON.stringify(payload)
  }),
  createExternalTask: (payload: any) => fetchApi<any>("/tasks/external", {
    method: "POST",
    body: JSON.stringify(payload)
  }),

  // Report generation
  generateRunReport: (runId: string) => fetchApi<any>(`/runs/${runId}/report`, {
    method: "POST"
  }),
  getRunReport: (runId: string) => fetchApi<any>(`/runs/${runId}/report`),
  getRunReportMarkdown: async (runId: string) => {
    const url = `${API_BASE}/runs/${runId}/report/markdown`;
    const response = await fetch(url);
    if (!response.ok) {
      throw new ApiError(response.status, "Failed to fetch markdown report.");
    }
    return response.text();
  },
};
