import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppLayout } from "@/components/layout/AppLayout";

// Route components are code-split so the first paint ships only the shell plus
// the one page being visited. This matters most for Deep Dive: it is the sole
// consumer of recharts, which is by far the heaviest dependency, so keeping it
// out of the entry chunk shrinks the initial download for every other page.
//
// The pages use named exports, so each import is mapped to the shape React.lazy
// expects rather than adding a default export to all 19 files.
const Dashboard = lazy(() =>
  import("@/pages/Dashboard").then((m) => ({ default: m.Dashboard })),
);
const Scanner = lazy(() =>
  import("@/pages/Scanner").then((m) => ({ default: m.Scanner })),
);
const Monitor = lazy(() =>
  import("@/pages/Monitor").then((m) => ({ default: m.Monitor })),
);
const Conviction = lazy(() =>
  import("@/pages/Conviction").then((m) => ({ default: m.Conviction })),
);
const Explore = lazy(() =>
  import("@/pages/Explore").then((m) => ({ default: m.Explore })),
);
const CoveredCalls = lazy(() =>
  import("@/pages/CoveredCalls").then((m) => ({ default: m.CoveredCalls })),
);
const Settings = lazy(() =>
  import("@/pages/Settings").then((m) => ({ default: m.Settings })),
);
const ResearchHome = lazy(() =>
  import("@/pages/research/ResearchHome").then((m) => ({ default: m.ResearchHome })),
);
const StocksDashboard = lazy(() =>
  import("@/pages/stocks/Dashboard").then((m) => ({ default: m.StocksDashboard })),
);
const StocksConviction = lazy(() =>
  import("@/pages/stocks/Conviction").then((m) => ({ default: m.StocksConviction })),
);
const ConvictionHistory = lazy(() =>
  import("@/pages/stocks/History").then((m) => ({ default: m.ConvictionHistory })),
);
const DeepDipDashboard = lazy(() =>
  import("@/pages/stocks/DeepDips").then((m) => ({ default: m.DeepDipDashboard })),
);
const Alpha = lazy(() =>
  import("@/pages/stocks/Alpha").then((m) => ({ default: m.Alpha })),
);
const Screener = lazy(() =>
  import("@/pages/stocks/Screener").then((m) => ({ default: m.Screener })),
);
const StockDeepDive = lazy(() =>
  import("@/pages/stocks/DeepDive").then((m) => ({ default: m.StockDeepDive })),
);
const IntelligenceDashboard = lazy(() =>
  import("@/pages/intelligence/Dashboard").then((m) => ({
    default: m.IntelligenceDashboard,
  })),
);
const IntelligenceNews = lazy(() =>
  import("@/pages/intelligence/News").then((m) => ({ default: m.IntelligenceNews })),
);
const IntelligenceFilings = lazy(() =>
  import("@/pages/intelligence/Filings").then((m) => ({
    default: m.IntelligenceFilings,
  })),
);
const IntelligenceInsider = lazy(() =>
  import("@/pages/intelligence/Insider").then((m) => ({
    default: m.IntelligenceInsider,
  })),
);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
    },
  },
});

function RouteFallback() {
  return (
    <div className="flex items-center justify-center py-24" data-print="hide">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Suspense fallback={<RouteFallback />}>
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
        </Suspense>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
