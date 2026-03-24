import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navbar } from "@/components/Navbar";
import { Dashboard } from "@/pages/Dashboard";
import { Scanner } from "@/pages/Scanner";
import { Orders } from "@/pages/Orders";
import { Monitor } from "@/pages/Monitor";
import { Settings } from "@/pages/Settings";

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
        <div className="min-h-screen bg-gray-950 text-gray-100">
          <Navbar />
          <main className="mx-auto max-w-7xl px-4 py-6">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/scanner" element={<Scanner />} />
              <Route path="/orders" element={<Orders />} />
              <Route path="/monitor" element={<Monitor />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
