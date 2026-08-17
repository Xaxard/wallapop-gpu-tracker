import { ListRowsSkeleton, PageHeaderSkeleton } from "@/components/skeletons";
import { Skeleton } from "@/components/ui/skeleton";

export default function SearchesLoading() {
  return (
    <div className="space-y-6">
      <PageHeaderSkeleton />
      <div className="flex gap-1">
        <Skeleton className="h-8 w-24 rounded-md" />
        <Skeleton className="h-8 w-24 rounded-md" />
      </div>
      <div className="rounded-lg border bg-card">
        <div className="border-b px-4 py-3">
          <Skeleton className="h-4 w-32" />
        </div>
        <div className="px-4">
          <ListRowsSkeleton rows={5} />
        </div>
      </div>
    </div>
  );
}
