import type { CSSProperties } from "react";

import { googleLoginUrl } from "../api";
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

export function GoogleButton() {
  return (
    <button
      className="oauth-button"
      data-google-login="true"
      type="button"
      onClick={() => (window.location.href = googleLoginUrl())}
    >
      <svg viewBox="0 0 18 18" aria-hidden="true">
        <path
          fill="#4285F4"
          d="M17.64 9.2c0-.63-.06-1.24-.16-1.82H9v3.44h4.84a4.14 4.14 0 0 1-1.8 2.71v2.25h2.9c1.7-1.57 2.7-3.88 2.7-6.58Z"
        />
        <path
          fill="#34A853"
          d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.9-2.25c-.8.54-1.84.86-3.06.86-2.35 0-4.34-1.59-5.05-3.72H.96v2.32A9 9 0 0 0 9 18Z"
        />
        <path
          fill="#FBBC05"
          d="M3.95 10.71A5.4 5.4 0 0 1 3.67 9c0-.6.1-1.19.28-1.71V4.97H.96A9 9 0 0 0 0 9c0 1.45.35 2.82.96 4.03l2.99-2.32Z"
        />
        <path
          fill="#EA4335"
          d="M9 3.58c1.32 0 2.5.45 3.43 1.33l2.57-2.57C13.46.9 11.42 0 9 0A9 9 0 0 0 .96 4.97l2.99 2.32C4.66 5.17 6.65 3.58 9 3.58Z"
        />
      </svg>
      <span>Continue with Google</span>
    </button>
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
