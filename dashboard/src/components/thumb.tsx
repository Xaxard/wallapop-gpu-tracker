import { ImageOff } from "lucide-react";
import { cn } from "@/lib/utils";

export function Thumb({
  src,
  alt,
  className,
}: {
  src: string | null | undefined;
  alt: string;
  className?: string;
}) {
  if (!src) {
    return (
      <div
        className={cn(
          "flex shrink-0 items-center justify-center rounded-md border bg-muted text-muted-foreground",
          className,
        )}
      >
        <ImageOff className="size-4" />
      </div>
    );
  }
  return (
    // Listing photos come from arbitrary Wallapop CDN hosts — next/image
    // would need every one allow-listed in next.config.ts remotePatterns.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      loading="lazy"
      className={cn("shrink-0 rounded-md border object-cover", className)}
    />
  );
}
