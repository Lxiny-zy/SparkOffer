import * as React from "react";
import { cn } from "@/lib/utils";

type CardVariant = "tech" | "glass";
interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: CardVariant;
  /** Cursor-following radial highlight on hover. On by default — set false to opt out. */
  interactive?: boolean;
  /** Lift + glow on hover. Off by default; enable for tile-like / clickable cards. */
  hoverLift?: boolean;
}

const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, variant, interactive = true, hoverLift = false, ...props }, ref) => {
    const variantClass =
      variant === "glass"
        ? "sig-card text-text transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]"
        : variant === "tech"
        ? "sig-card text-text transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]"
        : "sig-card text-text transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]";
    return (
      <div
        ref={ref}
        data-spotlight={interactive ? "" : undefined}
        className={cn(variantClass, interactive && "spotlight", hoverLift && "sig-card-hover", className)}
        {...props}
      />
    );
  }
);
Card.displayName = "Card";

type DivProps = React.HTMLAttributes<HTMLDivElement>;

const CardHeader = React.forwardRef<HTMLDivElement, DivProps>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("flex flex-col space-y-1.5 p-5 md:p-6", className)} {...props} />
));
CardHeader.displayName = "CardHeader";

const CardTitle = React.forwardRef<HTMLDivElement, DivProps>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("font-semibold leading-none tracking-tight", className)} {...props} />
));
CardTitle.displayName = "CardTitle";

const CardDescription = React.forwardRef<HTMLDivElement, DivProps>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("text-sm text-dim", className)} {...props} />
));
CardDescription.displayName = "CardDescription";

const CardContent = React.forwardRef<HTMLDivElement, DivProps>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("p-5 md:p-6 pt-0", className)} {...props} />
));
CardContent.displayName = "CardContent";

const CardFooter = React.forwardRef<HTMLDivElement, DivProps>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("flex items-center p-5 md:p-6 pt-0", className)} {...props} />
));
CardFooter.displayName = "CardFooter";

export { Card, CardHeader, CardFooter, CardTitle, CardDescription, CardContent };
