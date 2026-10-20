/** Yelvo components. Global: window.Yelvo. Needs React 18. */
import type { ReactNode, ButtonHTMLAttributes } from "react";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** cta for the one primary action, accent for a single highlight, outline (default) for secondary, ghost for tertiary */
  variant?: "cta" | "accent" | "outline" | "ghost";
  /** sm is 38px high, lg is 48px high */
  size?: "sm" | "lg";
  /** trailing arrow for forward actions */
  icon?: "arrow";
  /** renders an anchor instead of a button */
  href?: string;
  children: ReactNode;
}
export declare function Button(props: ButtonProps): JSX.Element;

export interface BadgeProps { tone?: "accent" | "neutral" | "solid"; children: ReactNode; }
export declare function Badge(props: BadgeProps): JSX.Element;

export interface EyebrowProps { children: ReactNode; }
export declare function Eyebrow(props: EyebrowProps): JSX.Element;

export interface TestimonialCardProps { quote: ReactNode; name: string; role: string; /** image URL; initials shown when absent */ avatar?: string; }
export declare function TestimonialCard(props: TestimonialCardProps): JSX.Element;

export interface StepItemProps { number: number; title: ReactNode; children?: ReactNode; }
export declare function StepItem(props: StepItemProps): JSX.Element;

export interface AccordionProps { items: { question: ReactNode; answer: ReactNode }[]; /** index of the item open on mount */ defaultOpen?: number; }
export declare function Accordion(props: AccordionProps): JSX.Element;

export interface UrlInputProps { onSubmit?: (url: string) => void; placeholder?: string; label?: string; action?: string; hints?: string[]; }
export declare function UrlInput(props: UrlInputProps): JSX.Element;

export interface TrustBadgeProps { count?: string; source?: string; rating?: number; }
export declare function TrustBadge(props: TrustBadgeProps): JSX.Element;
