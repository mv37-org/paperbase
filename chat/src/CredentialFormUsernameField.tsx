import type { CSSProperties } from "react";

const HIDDEN_USERNAME_STYLE: CSSProperties = {
  position: "absolute",
  left: "-10000px",
  width: "1px",
  height: "1px",
  opacity: 0,
  pointerEvents: "none",
};

export function CredentialFormUsernameField({ email = "" }: { email?: string }) {
  return (
    <input
      type="email"
      name="username"
      autoComplete="username"
      value={email}
      readOnly
      tabIndex={-1}
      aria-hidden="true"
      style={HIDDEN_USERNAME_STYLE}
    />
  );
}
