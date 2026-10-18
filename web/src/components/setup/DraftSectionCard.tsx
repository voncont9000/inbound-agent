import { Badge } from "../States";

export type DraftSectionStatus = "pending" | "accepted" | "discarded";

const statusTone: Record<DraftSectionStatus, "neutral" | "success" | "warning"> = {
  pending: "neutral",
  accepted: "success",
  discarded: "warning",
};

export function DraftSectionCard({
  title,
  status,
  editing,
  children,
  onAccept,
  onEdit,
  onSave,
  onCancel,
  onDiscard,
  onUndo,
}: {
  title: string;
  status: DraftSectionStatus;
  editing: boolean;
  children: React.ReactNode;
  onAccept: () => void;
  onEdit: () => void;
  onSave: () => void;
  onCancel: () => void;
  onDiscard: () => void;
  onUndo: () => void;
}) {
  return <section className="panel" aria-label={title}>
    <div className="list-row">
      <h2>{title}</h2>
      <Badge tone={statusTone[status]}>{status[0].toUpperCase() + status.slice(1)}</Badge>
    </div>
    {children}
    <div className="button-row">
      {editing ? <>
        <button className="button-primary" type="button" onClick={onSave}>Save and accept</button>
        <button type="button" onClick={onCancel}>Cancel edit</button>
      </> : status === "discarded" ? (
        <button type="button" onClick={onUndo}>Undo</button>
      ) : <>
        <button className="button-primary" type="button" onClick={onAccept}>Accept</button>
        <button type="button" onClick={onEdit}>Edit</button>
        <button type="button" onClick={onDiscard}>Discard</button>
      </>}
    </div>
  </section>;
}
