import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { Shell } from "./Shell";
import { Account } from "./screens/Account";
import { Login } from "./screens/Login";
import { Overview } from "./screens/Overview";
import { Audit } from "./screens/Audit";
import { Benchmark } from "./screens/Benchmark";
import { BenchmarkRun } from "./screens/BenchmarkRun";
import { Fleet } from "./screens/Fleet";
import { FleetJob } from "./screens/FleetJob";
import { QueryHistory } from "./screens/QueryHistory";
import { QuerySet } from "./screens/QuerySet";
import { QuerySets } from "./screens/QuerySets";
import { Schedules } from "./screens/Schedules";
import { ResourceGroupHistory } from "./screens/ResourceGroupHistory";
import { ResourceGroups } from "./screens/ResourceGroups";
import { Restart, RestartSequenceScreen } from "./screens/Restart";
import { Gateway } from "./screens/Gateway";
import { Health } from "./screens/Health";
import { Queries } from "./screens/Queries";
import { Work } from "./screens/Work";
import { WorkItem } from "./screens/WorkItem";
import { Workload } from "./screens/Workload";

// The approved design system, unchanged. Vite hashes it into the bundle, so
// a stylesheet change cannot be served from a browser cache that still holds
// the previous one.
import "./tms.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        {/* Outside the Shell: there is no navigation to offer someone who is
            not signed in, and every screen inside it would 401 on its first
            read. */}
        <Route path="/login" element={<Login />} />
        <Route element={<Shell />}>
          <Route index element={<Overview />} />
          <Route path="queries" element={<Queries />} />
          {/* ⛔ Not /health: that is the server's liveness probe, registered
              before the console's catch-all and documented in deploy.md. It
              wins, and the screen was simply unreachable. */}
          <Route path="cluster-health" element={<Health />} />
          <Route path="gateway" element={<Gateway />} />
          <Route path="workload" element={<Workload />} />
          <Route path="restart" element={<Restart />} />
          <Route path="restarts/:id" element={<RestartSequenceScreen />} />
          <Route path="fleet" element={<Fleet />} />
          <Route path="fleet/jobs/:id" element={<FleetJob />} />
          <Route path="resource-groups" element={<ResourceGroups />} />
          <Route path="resource-groups/history" element={<ResourceGroupHistory />} />
          <Route path="benchmark" element={<Benchmark />} />
          <Route path="benchmark/runs/:id" element={<BenchmarkRun />} />
          <Route path="benchmark/schedules" element={<Schedules />} />
          <Route path="benchmark/sets" element={<QuerySets />} />
          <Route path="benchmark/sets/:key" element={<QuerySet />} />
          <Route path="benchmark/sets/:key/queries/:name/history"
                 element={<QueryHistory />} />
          <Route path="work" element={<Work />} />
          <Route path="work/:key" element={<WorkItem />} />
          <Route path="audit" element={<Audit />} />
          <Route path="account" element={<Account />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
