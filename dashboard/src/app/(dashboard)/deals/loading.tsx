import { FiltersRowSkeleton, PageHeaderSkeleton, TableSkeleton } from "@/components/skeletons";

export default function DealsLoading() {
  return (
    <div className="space-y-6">
      <PageHeaderSkeleton />
      <div className="space-y-4">
        <FiltersRowSkeleton />
        <TableSkeleton rows={10} cols={8} />
      </div>
    </div>
  );
}
