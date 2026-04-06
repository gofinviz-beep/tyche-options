import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppLayout } from "@/components/layout/AppLayout";
import { Dashboard } from "@/pages/Dashboard";
import { Scanner } from "@/pages/Scanner";
import { Orders } from "@/pages/Orders";
import { Monitor } from "@/pages/Monitor";
import { Intents } from "@/pages/Intents";
import { Conviction } from "@/pages/Conviction";
import { Explore } from "@/pages/Explore";
import { Settings } from "@/pages/Settings";
import { ResearchHome } from "@/pages/research/ResearchHome";
import { StocksDashboard } from "@/pages/stocks/Dashboard";
import { StocksConviction } from "@/pages/stocks/Conviction";
import { ConvictionHistory } from "@/pages/stocks/History";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            {/* Options module */}
            <Route path="/options" element={<Dashboard />} />
            <Route path="/options/scanner" element={<Scanner />} />
            <Route path="/options/conviction" element={<Conviction />} />
            <Route path="/options/explore" element={<Explore />} />
            <Route path="/options/intents" element={<Intents />} />
            <Route path="/options/orders" element={<Orders />} />
            <Route path="/options/monitor" element={<Monitor />} />

            {/* Stocks module */}
            <Route path="/stocks" element={<StocksDashboard />} />
            <Route path="/stocks/scanner" element={<Navigate to="/stocks" replace />} />
            <Route path="/stocks/conviction" element={<StocksConviction />} />
            <Route path="/stocks/history" element={<ConvictionHistory />} />

            {/* Research module */}
            <Route path="/research" element={<ResearchHome />} />

            {/* Global pages */}
            <Route path="/settings" element={<Settings />} />

            {/* Root redirect */}
            <Route path="/" element={<Navigate to="/options" replace />} />

            {/* Legacy redirects */}
            <Route path="/scanner" element={<Navigate to="/options/scanner" replace />} />
            <Route path="/conviction" element={<Navigate to="/options/conviction" replace />} />
            <Route path="/intents" element={<Navigate to="/options/intents" replace />} />
            <Route path="/orders" element={<Navigate to="/options/orders" replace />} />
            <Route path="/monitor" element={<Navigate to="/options/monitor" replace />} />
            <Route path="/options/settings" element={<Navigate to="/settings" replace />} />
            <Route path="/options/alerts" element={<Navigate to="/stocks" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
