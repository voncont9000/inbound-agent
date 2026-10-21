import { ChannelIcon } from "./ChannelIcon";

type ChannelKind = "phone" | "whatsapp" | "gmail";
type GmailMode = "off" | "fake" | "google";

export function ChannelPicker({ id, gmailMode, gmailAvailable, onSelect, onCancel }: {
  id?: string;
  gmailMode: GmailMode | undefined;
  gmailAvailable: boolean;
  onSelect: (kind: ChannelKind) => void;
  onCancel: () => void;
}) {
  return <div className="post-action-picker channel-add-picker" id={id}>
    <h3>Choose a channel</h3>
    <div className="post-action-picker-grid">
      <button type="button" className="post-action-picker-card" onClick={() => onSelect("phone")}>
        <ChannelIcon kind="phone" size={20} />
        <span className="post-action-picker-copy"><strong>Phone number</strong><span>Connect a number routed to an assistant.</span></span>
      </button>
      <button type="button" className="post-action-picker-card" onClick={() => onSelect("whatsapp")}>
        <ChannelIcon kind="whatsapp" size={20} />
        <span className="post-action-picker-copy"><strong>WhatsApp</strong><span>Connect using a WhatsApp phone number ID.</span></span>
      </button>
      {gmailMode && gmailMode !== "off" && gmailAvailable && <button type="button" className="post-action-picker-card" onClick={() => onSelect("gmail")}>
        <ChannelIcon kind="gmail" size={20} />
        <span className="post-action-picker-copy"><strong>Gmail inbox</strong><span>{gmailMode === "google" ? "Connect with Google." : "Connect a mailbox for draft-only email triage."}</span></span>
      </button>}
    </div>
    <button type="button" className="button-ghost post-action-picker-cancel" onClick={onCancel}>Cancel</button>
  </div>;
}
