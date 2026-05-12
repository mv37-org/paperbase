import type { CSSProperties } from "react";

import type { ModalFlashKind } from "./appTypes";
import { staticAssetPath } from "./appUtils";

export function SettingsSectionHeading({ title, caption }: { title: string; caption: string }) {
  return (
    <div className="settings-section-heading">
      <h3 className="settings-section-title">{title}</h3>
      <p className="settings-section-copy">{caption}</p>
    </div>
  );
}

export function ModalFlash({
  message,
  kind,
  className = "",
}: {
  message: string;
  kind: ModalFlashKind;
  className?: string;
}) {
  return (
    <div
      id="modal-flash"
      className={`modal-flash${kind === "error" ? " warning" : " note"}${className ? ` ${className}` : ""}`}
      role={kind === "error" ? "alert" : "status"}
    >
      {kind === "error" ? (
        <span className="modal-flash-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M12 4 3.75 18h16.5L12 4Z" />
            <path d="M12 9v4" />
            <path d="M12 16.5h.01" />
          </svg>
        </span>
      ) : null}
      <span className="modal-flash-text">{message}</span>
    </div>
  );
}

export function AppIcon({ src, className = "" }: { src: string; className?: string }) {
  return (
    <img
      src={src}
      alt=""
      aria-hidden="true"
      className={className ? `icon-image ${className}` : "icon-image"}
    />
  );
}

export function MaskedAssetIcon({ asset, className }: { asset: string; className: string }) {
  return (
    <span
      className={className}
      aria-hidden="true"
      style={
        {
          "--icon-url": `url("${staticAssetPath(asset)}")`,
        } as CSSProperties
      }
    />
  );
}

export function EmptyStateActionIcon({ asset }: { asset: string }) {
  return <MaskedAssetIcon asset={asset} className="chat-empty-action-icon-glyph" />;
}
