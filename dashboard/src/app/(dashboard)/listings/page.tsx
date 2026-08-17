import { ListingsTable } from "@/components/listings/listings-table";
import { getListings } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function ListingsPage() {
  const listings = await getListings();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Listings</h1>
        <p className="text-sm text-muted-foreground">
          Every listing the tracker has ever seen. Click a row for its full timeline.
        </p>
      </div>
      <ListingsTable listings={listings} />
    </div>
  );
}
