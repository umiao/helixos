import { useEffect, useState } from "react";
import { fetchCostDashboard } from "../api";
import type { CostDashboardResponse } from "../types";

function formatUSD(value: number): string {
  return `$${value.toFixed(4)}`;
}

export default function CostDashboard() {
  const [data, setData] = useState<CostDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchCostDashboard()
      .then((resp) => {
        if (!cancelled) setData(resp);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        Loading cost data...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-red-500 text-sm">
        Failed to load costs: {error}
      </div>
    );
  }

  if (!data || data.projects.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        No review cost data available yet.
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto p-4">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-gray-200 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
            <th className="py-2 pr-4">Project</th>
            <th className="py-2 pr-4 text-right">Reviews</th>
            <th className="py-2 pr-4 text-right">Total Cost</th>
            <th className="py-2 text-right">Avg Cost</th>
          </tr>
        </thead>
        <tbody>
          {data.projects.map((p) => (
            <tr key={p.project_id} className="border-b border-gray-100 hover:bg-gray-50">
              <td className="py-2 pr-4 font-medium text-gray-800">{p.name}</td>
              <td className="py-2 pr-4 text-right text-gray-600 tabular-nums">
                {p.total_reviews}
              </td>
              <td className="py-2 pr-4 text-right text-gray-800 tabular-nums font-mono">
                {formatUSD(p.total_cost_usd)}
              </td>
              <td className="py-2 text-right text-gray-600 tabular-nums font-mono">
                {formatUSD(p.avg_cost)}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-gray-300 font-semibold">
            <td className="py-2 pr-4 text-gray-800">Total</td>
            <td className="py-2 pr-4 text-right text-gray-600 tabular-nums">
              {data.projects.reduce((s, p) => s + p.total_reviews, 0)}
            </td>
            <td className="py-2 pr-4 text-right text-gray-800 tabular-nums font-mono">
              {formatUSD(data.grand_total_cost_usd)}
            </td>
            <td className="py-2"></td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
