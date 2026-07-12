import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppLayout } from "@/components/layout/AppLayout";
import { Dashboard } from "@/pages/Dashboard";
import { Scanner } from "@/pages/Scanner";
import { Monitor } from "@/pages/Monitor";
import { Conviction } from "@/pages/Conviction";
import { Explore } from "@/pages/Explore";
import { CoveredCalls } from "@/pages/CoveredCalls";
import { Settings } from "@/pages/Settings";
import { ResearchHome } from "@/pages/research/ResearchHome";
import { StocksDashboard } from "@/pages/stocks/Dashboard";
import { StocksConviction } from "@/pages/stocks/Conviction";
import { ConvictionHistory } from "@/pages/stocks/History";
import { DeepDipDashboard } from "@/pages/stocks/DeepDips";
import { Alpha } from "@/pages/stocks/Alpha";
import { Screener } from "@/pages/stocks/Screener";
import { StockDeepDive } from "@/pages/stocks/DeepDive";
import { IntelligenceDashboard } from "@/pages/intelligence/Dashboard";
import { IntelligenceNews } from "@/pages/intelligence/News";
import { IntelligenceFilings } from "@/pages/intelligence/Filings";
import { IntelligenceInsider } from "@/pages/intelligence/Insider";

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
            <Route path="/options/monitor" element={<Monitor />} />
            <Route path="/options/covered-calls" element={<CoveredCalls />} />

            {/* Stocks module */}
            <Route path="/stocks" element={<StocksDashboard />} />
            <Route path="/stocks/scanner" element={<Navigate to="/stocks" replace />} />
            <Route path="/stocks/conviction" element={<StocksConviction />} />
            <Route path="/stocks/history" element={<ConvictionHistory />} />
            <Route path="/stocks/deep-dips" element={<DeepDipDashboard />} />
            <Route path="/stocks/alpha" element={<Alpha />} />
            <Route path="/stocks/screener" element={<Screener />} />
            <Route path="/stocks/deep-dive" element={<StockDeepDive />} />

            {/* Intelligence module */}
            <Route path="/intelligence" element={<IntelligenceDashboard />} />
            <Route path="/intelligence/news" element={<IntelligenceNews />} />
            <Route path="/intelligence/filings" element={<IntelligenceFilings />} />
            <Route path="/intelligence/insider" element={<IntelligenceInsider />} />

            {/* Research module */}
            <Route path="/research" element={<ResearchHome />} />

            {/* Global pages */}
            <Route path="/settings" element={<Settings />} />

            {/* Root redirect */}
            <Route path="/" element={<Navigate to="/options" replace />} />

            {/* Legacy redirects */}
            <Route path="/scanner" element={<Navigate to="/options/scanner" replace />} />
            <Route path="/conviction" element={<Navigate to="/options/conviction" replace />} />
            <Route path="/monitor" element={<Navigate to="/options/monitor" replace />} />
            <Route path="/options/settings" element={<Navigate to="/settings" replace />} />
            <Route path="/options/alerts" element={<Navigate to="/stocks" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
