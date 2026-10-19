import { useEffect, useRef, type ReactNode } from "react";

export function ConfirmDialog({ title, children, onConfirm, onCancel, confirmLabel = "Confirm", tone }: {
  title: string;
  children: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
  confirmLabel?: string;
  tone?: "danger";
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
      if (event.key === "Tab" && dialogRef.current) {
        const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])")];
        const first = focusable[0];
        const last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      returnFocus?.focus();
    };
  }, [onCancel]);
  return <div className="dialog-backdrop"><div className="dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title" ref={dialogRef}>
    <h2 id="confirm-title">{title}</h2>
    <div>{children}</div>
    <div className="button-row"><button type="button" ref={cancelRef} onClick={onCancel}>Cancel</button><button type="button" className={tone === "danger" ? "button-danger" : "button-primary"} onClick={onConfirm}>{confirmLabel}</button></div>
  </div></div>;
}
