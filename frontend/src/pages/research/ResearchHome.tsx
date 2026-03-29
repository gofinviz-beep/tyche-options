import { useState } from "react";
import { Card } from "@/components/Card";
import { BookOpen, Search } from "lucide-react";

export function ResearchHome() {
  const [ticker, setTicker] = useState("");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Company Research</h1>
        <p className="mt-1 text-sm text-gray-500">
          Deep-dive analysis on companies using AI-powered research. Enter a
          ticker to get started.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder="Enter a ticker (e.g. AAPL)..."
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            className="w-full rounded-lg border border-gray-300 bg-white py-2 pl-10 pr-3 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>
        <button
          disabled
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white opacity-50 cursor-not-allowed"
          title="Coming soon"
        >
          Research
        </button>
      </div>

      <Card>
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-gray-100">
            <BookOpen className="h-6 w-6 text-gray-400" />
          </div>
          <h3 className="mt-4 text-sm font-semibold text-gray-700">
            No research reports yet
          </h3>
          <p className="mt-1 max-w-sm text-sm text-gray-400">
            Enter a ticker above to run an AI-powered deep dive. Reports will
            appear here once generated.
          </p>
        </div>
      </Card>
    </div>
  );
}
