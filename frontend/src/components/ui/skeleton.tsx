import { cn } from "@/lib/utils";

function Skeleton({ className, ...props }) {
  return (
    <div
      className={cn("sig-skeleton", className)}
      {...props}
    />
  );
}

export { Skeleton };
