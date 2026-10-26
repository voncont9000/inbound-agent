import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Icon } from "./Icon";

export type AccordionItem = { question: string; answer: ReactNode };

export function Accordion({ items, defaultOpen }: { items: AccordionItem[]; defaultOpen?: number }) {
  const [open, setOpen] = useState<number | null>(defaultOpen ?? null);
  const baseId = useId();
  const triggers = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = items.length - 1;
    const target =
      event.key === "ArrowDown" ? (index === last ? 0 : index + 1)
      : event.key === "ArrowUp" ? (index === 0 ? last : index - 1)
      : event.key === "Home" ? 0
      : event.key === "End" ? last
      : null;
    if (target === null) return;
    event.preventDefault();
    triggers.current[target]?.focus();
  };

  return (
    <div className="dt-acc">
      {items.map((item, index) => {
        const isOpen = open === index;
        const triggerId = `${baseId}-trigger-${index}`;
        const panelId = `${baseId}-panel-${index}`;
        return (
          <div className="dt-acc-item" data-open={isOpen} key={item.question}>
            <h3>
              <button
                ref={(node) => {
                  triggers.current[index] = node;
                }}
                id={triggerId}
                type="button"
                className="dt-acc-trigger"
                aria-expanded={isOpen}
                aria-controls={panelId}
                onClick={() => setOpen(isOpen ? null : index)}
                onKeyDown={(event) => onKeyDown(event, index)}
              >
                {item.question}
                <Icon name="plus" className="dt-acc-icon" />
              </button>
            </h3>
            <div className="dt-acc-region" id={panelId} role="region" aria-labelledby={triggerId} inert={!isOpen} aria-hidden={!isOpen}>
              <div className="dt-acc-inner">
                <div className="dt-acc-panel">{item.answer}</div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
