import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Display name for an LLM provider id from the API ("claude", "gemini"). */
export function llmLabel(provider: string | null | undefined): string {
  return provider === "gemini" ? "Gemini" : "Claude";
}

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const pct = (v: number | null | undefined, digits = 0) =>
  v == null ? "–" : `${(v * 100).toFixed(digits)}%`;

export const humanize = (s: string | null | undefined) => (s ? s.replace(/_/g, " ") : "–");

export const fmtDate = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "–";

export const fmtDateTime = (iso: string | null | undefined) =>
  iso
    ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : "–";

export const ROLE_LABEL: Record<string, string> = {
  operator: "Operator",
  technician: "Technician",
  fleet_manager: "Fleet manager",
};
