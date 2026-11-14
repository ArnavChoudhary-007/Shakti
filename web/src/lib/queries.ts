import { useQuery } from "@tanstack/react-query";
import { searchModelsReady } from "./api";
import { useApi } from "./connection";

/** Ollama + chosen-model status. Polls faster while something isn't ready. */
export function useSetupStatus() {
  const api = useApi();
  return useQuery({
    queryKey: ["setup-status"],
    queryFn: api.setupStatus,
    refetchInterval: (q) => {
      const s = q.state.data;
      if (!s) return 3000;
      if (s.install.state === "downloading" || s.install.state === "verifying" || s.install.state === "installer_running") return 1500;
      const job = s.search_models?.job.state;
      if (job === "downloading" || job === "loading") return 1000;
      return s.running && s.chat_model_installed && searchModelsReady(s) ? 30000 : 3000;
    },
  });
}

export function useSetupComplete(): boolean | undefined {
  const { data } = useSetupStatus();
  return data ? data.running && data.chat_model_installed && searchModelsReady(data) : undefined;
}

export function useRecommendations(enabled = true) {
  const api = useApi();
  return useQuery({ queryKey: ["recommendations"], queryFn: api.recommendations, enabled, retry: false });
}

export function useWorkspaces() {
  const api = useApi();
  return useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
}
