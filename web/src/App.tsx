import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppLayout, Logo } from "@/components/Layout";
import { Spinner } from "@/components/ui";
import { useConnection } from "@/lib/connection";
import { useSetupComplete } from "@/lib/queries";
import LandingPage from "@/pages/Landing";
import { ConnectPage, PairPage } from "@/pages/Pair";
import SetupPage from "@/pages/Setup";
import ChatPage from "@/pages/Chat";
import DocumentsPage from "@/pages/Documents";
import ModelsPage from "@/pages/Models";
import SettingsPage from "@/pages/Settings";

// The graph page pulls in d3-force; load it only when visited.
const GraphPage = lazy(() => import("@/pages/Graph"));

function Splash() {
  return (
    <div className="grid h-full place-items-center">
      <div className="flex flex-col items-center gap-4">
        <Logo className="text-lg" />
        <Spinner className="text-brand-600" />
      </div>
    </div>
  );
}

function Home() {
  const complete = useSetupComplete();
  if (complete === undefined) return <Splash />;
  return <Navigate to={complete ? "/chat" : "/setup"} replace />;
}

export default function App() {
  const { phase } = useConnection();
  const location = useLocation();

  if (location.pathname === "/connect") return <ConnectPage />;
  if (phase === "checking") return <Splash />;
  if (phase === "missing") return <LandingPage />;
  if (phase === "unpaired") return <PairPage />;

  return (
    <AppLayout>
      <Suspense fallback={<Splash />}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/setup" element={<SetupPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/graph" element={<GraphPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </AppLayout>
  );
}
