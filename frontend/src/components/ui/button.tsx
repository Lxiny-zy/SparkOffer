import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)] cursor-pointer active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground border border-primary hover:-translate-y-px hover:shadow-[3px_3px_0_0_var(--sig-fg)]",
        destructive:
          "bg-red text-white border border-[color:var(--sig-danger)] hover:-translate-y-px hover:shadow-[3px_3px_0_0_var(--sig-fg)]",
        outline:
          "border border-[color:var(--sig-line-2)] bg-transparent text-text hover:border-[color:var(--sig-fg)] hover:bg-secondary",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/70",
        ghost:
          "text-muted-fg hover:bg-secondary hover:text-text",
        link:
          "text-primary underline-offset-4 hover:underline",
        cta:
          "bg-primary text-primary-foreground border border-primary font-semibold tracking-wide hover:-translate-y-px hover:shadow-[4px_4px_0_0_var(--sig-fg)]",
      },
      size: {
        default: "h-10 px-6 py-2",
        sm: "h-8 px-4 text-xs",
        lg: "h-12 px-10 text-base",
        xl: "h-14 px-12 text-base",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  /** Subtle magnetic pull toward the cursor on hover (driven by PointerFX). */
  magnetic?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, magnetic = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }), magnetic && "magnetic")}
        data-magnetic={magnetic ? "" : undefined}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
