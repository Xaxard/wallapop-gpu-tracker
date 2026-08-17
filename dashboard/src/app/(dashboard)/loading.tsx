import { KpiRowSkeleton, ListRowsSkeleton, PageHeaderSkeleton } from "@/components/skeletons";
import { Skeleton } from "@/components/ui/skeleton";

export default function OverviewLoading() {
  return (
    <div className="space-y-6">
      <PageHeaderSkeleton />
      <KpiRowSkeleton />
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <section className="rounded-lg border bg-card">
          <div className="border-b px-4 py-3">
            <Skeleton className="h-4 w-24" />
          </div>
          <div className="px-4">
            <ListRowsSkeleton />
          </div>
        </section>
        <section className="rounded-lg border bg-card">
          <div className="border-b px-4 py-3">
            <Skeleton className="h-4 w-28" />
          </div>
          <div className="px-4">
            <ListRowsSkeleton />
          </div>
        </section>
      </div>
    </div>
  );
}
