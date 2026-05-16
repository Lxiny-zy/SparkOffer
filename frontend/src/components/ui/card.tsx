import * as React from "react";
import { cn } from "@/lib/utils";

type CardVariant = "tech" | "glass";
interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: CardVariant;
}

const Card = React.forwardRef<HTMLDivElement, CardProps>(({ className, variant, ...props }, ref) => {
  const variantClass =
    variant === "tech"
      ? "rounded-3xl glass-subtle tech-border text-text shadow-[0_4px_24px_rgba(0,0,0,0.06)] transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)]"
      : variant === "glass"
      ? "rounded-3xl glass-strong text-text transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)]"
      : "rounded-3xl bg-card/80 backdrop-blur-sm text-text border border-border/50 shadow-[0_1px_3px_rgba(0,0,0,0.04),0_4px_12px_rgba(0,0,0,0.03)] hover:shadow-[0_4px_12px_rgba(0,0,0,0.06),0_8px_32px_rgba(0,0,0,0.06)] hover:border-border/80 transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)]";
  return (
    <div
      ref={ref}
      className={cn(variantClass, className)}
      {...props}
    />
  );
});
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
