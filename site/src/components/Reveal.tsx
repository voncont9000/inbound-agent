import type { CSSProperties, ElementType, ReactNode } from "react";
import { useReveal } from "../hooks/motion";

type RevealProps = {
  as?: ElementType;
  delay?: number;
  className?: string;
  style?: CSSProperties;
  id?: string;
  children: ReactNode;
};

export function Reveal({ as: Tag = "div", delay = 0, className, style, id, children }: RevealProps) {
  const ref = useReveal<HTMLElement>({ delay });
  return (
    <Tag ref={ref} className={className} style={style} id={id}>
      {children}
    </Tag>
  );
}
