import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import HomePage from "./pages/HomePage";
import WatchPage from "./pages/WatchPage";
import StatsPage from "./pages/StatsPage";
import MaintenancePage from "./pages/MaintenancePage";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/watch/:videoId" element={<WatchPage />} />
        <Route path="/stats" element={<StatsPage />} />
        <Route path="/maintenance" element={<MaintenancePage />} />
      </Routes>
    </Layout>
  );
}
