import type { Metadata } from "next";
import { LoginForm } from "@/components/login-form";
import { authEnv } from "@/lib/auth/session";

export const metadata: Metadata = {
  title: "Sign in · Wallapop Tracker",
};

// The gate has to reflect the live environment, not whatever was true when this
// page was last built — and it must never be served from a shared cache.
export const dynamic = "force-dynamic";

/** Only same-origin paths are accepted (see proxy.ts, safeNextPath). */
function safeNextPath(value: string | string[] | undefined): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value;
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[] }>;
}) {
  const { next } = await searchParams;

  return (
    <main className="flex min-h-svh items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex items-center gap-2">
          <div className="flex size-7 items-center justify-center rounded-md border border-foreground">
            <span className="text-sm font-semibold">W</span>
          </div>
          <span className="text-sm font-semibold tracking-tight">Wallapop Tracker</span>
        </div>

        <div>
          <h1 className="text-lg font-semibold tracking-tight">Sign in</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            This dashboard reads the tracker&apos;s full history. Enter the dashboard password to
            continue.
          </p>
        </div>

        <div className="rounded-lg border bg-card p-4">
          <LoginForm next={safeNextPath(next)} configured={authEnv() !== null} />
        </div>
      </div>
    </main>
  );
}
