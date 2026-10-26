import type { ReactNode } from "react";
import wordmarkUrl from "../assets/yelvo-wordmark.svg";
import { Icon } from "./Icon";

type ButtonProps = {
  variant?: "cta" | "accent" | "outline" | "ghost";
  size?: "sm" | "lg";
  icon?: "arrow";
  href?: string;
  type?: "button" | "submit";
  className?: string;
  onClick?: () => void;
  children: ReactNode;
};

export function Button({ variant = "outline", size = "sm", icon, href, type = "button", className, onClick, children }: ButtonProps) {
  const classes = ["dt-btn", `dt-btn-${variant}`, `dt-btn-${size}`, className].filter(Boolean).join(" ");
  const content = (
    <>
      {children}
      {icon === "arrow" && <Icon name="arrow" className="dt-btn-arrow" />}
    </>
  );
  return href ? (
    <a className={classes} href={href} onClick={onClick}>
      {content}
    </a>
  ) : (
    <button className={classes} type={type} onClick={onClick}>
      {content}
    </button>
  );
}

export function Badge({ tone = "accent", children, className }: { tone?: "accent" | "neutral" | "solid"; children: ReactNode; className?: string }) {
  return <span className={["dt-badge", `dt-badge-${tone}`, className].filter(Boolean).join(" ")}>{children}</span>;
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <span className="dt-eyebrow">
      <span className="dt-eyebrow-dot" aria-hidden="true" />
      {children}
    </span>
  );
}

export function StepItem({ number, title, children }: { number: number; title: ReactNode; children?: ReactNode }) {
  return (
    <div className="dt-step">
      <span className="dt-step-num" aria-hidden="true">
        {String(number).padStart(2, "0")}
      </span>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}

export function Wordmark({ height = 30 }: { height?: number }) {
  return <img src={wordmarkUrl} alt="Yelvo" width={Math.round((height * 302) / 110)} height={height} className="wordmark" />;
}

/** The three rounded sound bars from the logo, as a speaking indicator. */
export function SoundBars({ className, label }: { className?: string; label?: string }) {
  return (
    <span className={["sound-bars", className].filter(Boolean).join(" ")} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <span />
      <span />
      <span />
    </span>
  );
}
