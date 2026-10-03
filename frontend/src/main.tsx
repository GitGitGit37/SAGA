import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "@/components/Layout";
import { SessionProvider } from "@/lib/session";
import { AskMachine } from "@/pages/AskMachine";
import { AssetTimeline } from "@/pages/AssetTimeline";
import { FleetDashboard } from "@/pages/FleetDashboard";
import { InferenceDetailPage } from "@/pages/InferenceDetail";
import { Ingest } from "@/pages/Ingest";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SessionProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<FleetDashboard />} />
            <Route path="assets/:tag" element={<AssetTimeline />} />
            <Route path="inferences/:id" element={<InferenceDetailPage />} />
            <Route path="ingest" element={<Ingest />} />
            <Route path="ask" element={<AskMachine />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  </StrictMode>,
);
