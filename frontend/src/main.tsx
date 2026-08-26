import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { Shell } from "./Shell";
import { Overview } from "./screens/Overview";
import { Queries } from "./screens/Queries";

// The approved design system, unchanged. It is the same file the server-
// rendered console uses — one source of truth while both exist, and the file
// moves here when that one is deleted.
import "../../src/tms/web/static/tms.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter basename="/app">
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Overview />} />
          <Route path="queries" element={<Queries />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
