/** The choices for a model drop-down: what the gateway serves, and the model set now when the gateway doesn't
 * serve it (renamed or removed there), marked, so opening the page never changes it silently. `except` leaves a
 * model out: a review model shouldn't be its own writer. */
export interface ModelChoice { value: string; label: string; served: boolean }

export function modelChoices(current: string | null | undefined, served: string[], except?: string | null): ModelChoice[] {
  const list: ModelChoice[] = [...new Set(served)].filter((m) => m !== except || m === current)
    .map((m) => ({ value: m, label: m, served: true }));
  if (current && !served.includes(current)) {
    list.unshift({ value: current, label: `${current} (not served by the gateway)`, served: false });
  }
  return list;
}
