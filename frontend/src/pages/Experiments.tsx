import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { EmptyState } from "../components/ui/EmptyState";
import { LoadingState } from "../components/ui/LoadingState";
import { ServerCrash, FlaskConical } from "lucide-react";

export function Experiments() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    async function loadData() {
      try {
        await api.getExperiments();
      } catch (err) {
        if (err instanceof ApiError) setError(err);
        else setError(new ApiError(500, "Unknown error"));
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  if (loading) return <LoadingState />;

  if (error) {
    if (error.status === 404) {
      return (
        <div className="space-y-6">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Experiments</h1>
            <p className="text-muted-foreground mt-1">Manage and review large-scale evaluations.</p>
          </div>
          <EmptyState
            title="Backend Endpoint Missing"
            description="The GET /experiments endpoint is not implemented on the backend. Advanced experiment analytics belongs to Phase 2."
            isError
            icon={FlaskConical}
          />
        </div>
      );
    }
    return <EmptyState title="System Error" description={error.message} isError icon={ServerCrash} />;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Experiments</h1>
        <p className="text-muted-foreground mt-1">Manage and review large-scale evaluations.</p>
      </div>
      
      <EmptyState
        title="No Experiments Found"
        description="There are currently no experiment records available."
        icon={FlaskConical}
      />
    </div>
  );
}
