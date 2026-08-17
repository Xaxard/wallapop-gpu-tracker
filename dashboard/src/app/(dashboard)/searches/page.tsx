import { SearchesList } from "@/components/searches/searches-list";
import { JunkTable } from "@/components/searches/junk-table";
import { getJunkExclusions, getSearchesWithCounts } from "@/lib/queries";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export const dynamic = "force-dynamic";

export default async function SearchesPage() {
  const [searches, junk] = await Promise.all([getSearchesWithCounts(), getJunkExclusions()]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Searches & Junk</h1>
        <p className="text-sm text-muted-foreground">
          What the bot is watching, and what it&apos;s throwing away.
        </p>
      </div>

      <Tabs defaultValue="searches">
        <TabsList>
          <TabsTrigger value="searches">Searches</TabsTrigger>
          <TabsTrigger value="junk">Junk audit</TabsTrigger>
        </TabsList>
        <TabsContent value="searches" className="mt-4">
          {searches.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">No searches configured.</p>
          ) : (
            <SearchesList searches={searches} />
          )}
        </TabsContent>
        <TabsContent value="junk" className="mt-4">
          <JunkTable rows={junk} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
