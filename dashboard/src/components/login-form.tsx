"use client";

import { useActionState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { login } from "@/lib/actions/auth";
import { initialLoginState } from "@/lib/actions/auth-state";

export function LoginForm({ next, configured }: { next: string; configured: boolean }) {
  const [state, formAction, pending] = useActionState(login, initialLoginState);

  if (!configured) {
    return (
      <p className="text-sm text-muted-foreground">
        This deployment has no <code className="font-mono text-xs">DASHBOARD_PASSWORD</code> or{" "}
        <code className="font-mono text-xs">SESSION_SECRET</code> configured, so it cannot
        authenticate anyone and is serving nothing. Set both in the Vercel project&apos;s
        environment variables and redeploy.
      </p>
    );
  }

  return (
    <form action={formAction} className="space-y-4">
      <input type="hidden" name="next" value={next} />
      <div className="space-y-1.5">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          autoFocus
          required
          aria-invalid={state.error !== null || undefined}
          aria-describedby={state.error ? "password-error" : undefined}
        />
      </div>

      {state.error ? (
        <p id="password-error" role="alert" className="text-sm text-destructive">
          {state.error}
        </p>
      ) : null}

      <Button type="submit" size="lg" className="w-full" disabled={pending}>
        {pending ? "Checking…" : "Sign in"}
      </Button>
    </form>
  );
}
