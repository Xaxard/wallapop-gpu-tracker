"use client";

import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { logout } from "@/lib/actions/auth";

/** Posts to the `logout` Server Action, which clears the session cookie and
 *  redirects to /login. A plain form rather than an onClick so it still works
 *  before hydration. */
export function LogoutButton({ variant = "icon" }: { variant?: "icon" | "full" }) {
  return (
    <form action={logout}>
      {variant === "icon" ? (
        <Button
          type="submit"
          variant="ghost"
          size="icon"
          className="size-8 text-muted-foreground hover:text-foreground"
          aria-label="Sign out"
        >
          <LogOut className="size-4" />
        </Button>
      ) : (
        <Button
          type="submit"
          variant="ghost"
          className="w-full justify-start gap-2.5 px-2.5 text-muted-foreground hover:text-foreground"
        >
          <LogOut className="size-4" />
          Sign out
        </Button>
      )}
    </form>
  );
}
