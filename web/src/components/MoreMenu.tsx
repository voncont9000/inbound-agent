import { useEffect, useId, useRef, useState } from "react";

export interface MoreMenuItem {
  label: string;
  onSelect: () => void;
}

export function MoreMenu({ label, items }: { label: string; items: MoreMenuItem[] }) {
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (event.target instanceof Node && !menuRef.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return <div className="channel-menu-wrap" ref={menuRef}>
    <button
      ref={triggerRef}
      type="button"
      className="button-ghost button-sm channel-menu-trigger"
      aria-label={`More options for ${label}`}
      aria-haspopup="menu"
      aria-expanded={open}
      aria-controls={menuId}
      onClick={() => setOpen((value) => !value)}
    >⋯</button>
    {open && <div className="channel-row-menu" role="menu" id={menuId}>
      {items.map((item) => <button
        key={item.label}
        type="button"
        role="menuitem"
        onClick={() => { setOpen(false); item.onSelect(); }}
      >{item.label}</button>)}
    </div>}
  </div>;
}
