/** The kubectl commands for one pod (#419): only those that apply to it, with its real name and namespace. Kept apart
 * from the page so it can be tested without a browser. */
import type { Pod, Workload } from "./podRows";

export type Command = { command: string; what: string };
export type CommandGroup = { title: string; changes: boolean; commands: Command[] };

/** The container the workload is named after, else the first: the one `-c` picks when a pod has several. */
export function mainContainer(w: Workload, p: Pod): string | null {
  return p.containers.find((c) => c.name === w.name)?.name ?? p.containers[0]?.name ?? null;
}

export function podCommands(w: Workload, p: Pod): CommandGroup[] {
  const k = `kubectl -n ${p.namespace ?? "NAMESPACE"}`;
  const kind = w.kind === "statefulset" ? "statefulset" : "deployment";
  const several = p.containers.length > 1;
  const main = mainContainer(w, p);
  const c = several && main ? ` -c ${main}` : "";

  const logs: Command[] = [];
  if (several) {
    logs.push({ command: `${k} logs ${p.name} --all-containers`, what: "Every container's log" });
    for (const x of p.containers) {
      logs.push({ command: `${k} logs ${p.name} -c ${x.name}`, what: `The ${x.name} container's log` });
    }
  } else {
    logs.push({ command: `${k} logs ${p.name}`, what: "Its log" });
  }
  logs.push({ command: `${k} logs -f ${p.name}${c}`, what: "Its log as it's written (Ctrl+C to stop)" });
  logs.push({ command: `${k} logs --since=1h ${p.name}${c}`, what: "The last hour of its log" });
  if (p.restarts > 0) {
    logs.push({ command: `${k} logs --previous ${p.name}${c}`, what: "The log of the run before the last restart" });
  }

  const look: Command[] = [
    { command: `${k} describe pod ${p.name}`, what: "Its settings, state and recent events" },
    { command: `${k} get pod ${p.name} -o yaml`, what: "Everything the cluster holds about it" },
    { command: `${k} get events --field-selector involvedObject.name=${p.name} --sort-by=.lastTimestamp`,
      what: "What happened to it, oldest first" },
    { command: `${k} top pod ${p.name} --containers`, what: "CPU and memory now, per container" },
    { command: `${k} rollout status ${kind}/${w.name}`,
      what: `Whether the ${w.name} ${kind} has finished rolling out` },
  ];
  if (w.scaler === "keda") {
    look.push({ command: `${k} get scaledobject ${w.name}`,
      what: "KEDA's scaler: its pod count and whether it's active" });
  } else if (w.scaler === "hpa") {
    look.push({ command: `${k} get hpa`, what: "The autoscalers: their targets and pod counts" });
  }
  const port = w.about?.port;
  if (port) {
    look.push({ command: `${k} port-forward pod/${p.name} ${port}:${port}`,
      what: `Reach it at http://localhost:${port} from this computer (Ctrl+C to stop)` });
  }

  const changes: Command[] = [
    { command: `${k} rollout restart ${kind}/${w.name}`, what: `Replace the ${w.name} pods one by one` },
    { command: `${k} delete pod ${p.name}`, what: `Remove this pod; the ${kind} starts a new one` },
  ];
  if (!w.scaler) {
    changes.push({ command: `${k} scale ${kind}/${w.name} --replicas=${w.desired}`,
      what: `Set how many pods it runs (${w.desired} now)` });
  }
  if (w.about?.shell) {
    changes.push({ command: `${k} exec -it ${p.name}${c} -- sh`, what: "A shell inside it (exit to leave)" });
  }

  return [
    { title: "Its log", changes: false, commands: logs },
    { title: "Look at it", changes: false, commands: look },
    { title: "These change the cluster", changes: true, commands: changes },
  ];
}
