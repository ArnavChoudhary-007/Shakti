import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ConnectionProvider } from "./lib/connection";
import { PullsProvider } from "./lib/pulls";
import { WorkspaceProvider } from "./lib/workspace";
import { UploadsProvider } from "./lib/uploads";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 },
  },
});

// HashRouter: works unchanged on GitHub Pages (no server rewrites) and when
// served by the companion's static file mount.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <ConnectionProvider>
          <WorkspaceProvider>
            <PullsProvider>
              <UploadsProvider>
                <App />
              </UploadsProvider>
            </PullsProvider>
          </WorkspaceProvider>
        </ConnectionProvider>
      </HashRouter>
    </QueryClientProvider>
  </StrictMode>,
);
