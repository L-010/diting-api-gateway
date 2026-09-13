import { ToolView } from "@/lib/admin-tools";
import { api } from "@/lib/api";

let cachedTools: ToolView[] | null = null;
let pendingRequest: Promise<ToolView[]> | null = null;

export function peekAdminTools(): ToolView[] | null {
  return cachedTools;
}

export function loadAdminTools(force = false): Promise<ToolView[]> {
  if (!force && cachedTools) return Promise.resolve(cachedTools);
  if (pendingRequest) return pendingRequest;
  pendingRequest = api<ToolView[]>("/api/admin/tools")
    .then((items) => {
      cachedTools = items;
      return items;
    })
    .finally(() => {
      pendingRequest = null;
    });
  return pendingRequest;
}

export function clearAdminToolsCache() {
  cachedTools = null;
  pendingRequest = null;
}
