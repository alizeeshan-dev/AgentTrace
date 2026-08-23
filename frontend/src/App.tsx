import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AppShell } from "./components/ui/Layout";
import { Dashboard } from "./pages/Dashboard";
import { NewRun } from "./pages/NewRun";
import { Runs } from "./pages/Runs";
import { RunDetail } from "./pages/RunDetail";
import { Experiments } from "./pages/Experiments";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AppShell />}>
          <Route index element={<Dashboard />} />
          <Route path="new-run" element={<NewRun />} />
          <Route path="runs" element={<Runs />} />
          <Route path="runs/:id" element={<RunDetail />} />
          <Route path="experiments" element={<Experiments />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
