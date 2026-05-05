import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CredentialFormUsernameField } from "./CredentialFormUsernameField";

describe("CredentialFormUsernameField", () => {
  it("renders a hidden username field for password-manager accessibility", () => {
    render(<CredentialFormUsernameField email="admin@example.com" />);

    const input = screen.getByDisplayValue("admin@example.com");
    expect(input.getAttribute("name")).toBe("username");
    expect(input.getAttribute("autocomplete")).toBe("username");
    expect(input.getAttribute("type")).toBe("email");
    expect(input.getAttribute("readonly")).not.toBeNull();
    expect(input.getAttribute("aria-hidden")).toBe("true");
    expect(input.getAttribute("tabindex")).toBe("-1");
  });
});
