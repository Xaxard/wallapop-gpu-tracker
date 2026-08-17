"use client";

import { useState, useTransition } from "react";
import { Switch } from "@/components/ui/switch";
import { toggleSearchActive } from "@/lib/actions/toggle-search";

export function SearchToggle({ id, active }: { id: number; active: boolean }) {
  const [checked, setChecked] = useState(active);
  const [pending, startTransition] = useTransition();

  function onChange(next: boolean) {
    setChecked(next);
    startTransition(async () => {
      try {
        await toggleSearchActive(id, next);
      } catch {
        setChecked(!next);
      }
    });
  }

  return <Switch checked={checked} onCheckedChange={onChange} disabled={pending} />;
}
