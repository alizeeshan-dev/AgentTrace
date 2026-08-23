import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms == null) return "-"
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

export function formatTokens(tokens: number): string {
  return tokens.toLocaleString()
}

export function formatCost(cost: number | null | undefined): string {
  if (cost == null) return "-"
  return `$${cost.toFixed(4)}`
}
