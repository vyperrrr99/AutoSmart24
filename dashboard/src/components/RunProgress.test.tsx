import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunProgress, formatDuration } from "./RunProgress";

describe("formatDuration", () => {
  it("formats hours and minutes", () => {
    expect(formatDuration(5300)).toBe("1h 28m");
  });

  it("formats minutes only under an hour", () => {
    expect(formatDuration(480)).toBe("8m");
  });
});

describe("RunProgress", () => {
  it("shows phase, percent and eta", () => {
    render(<RunProgress phase="detail" done={1449} total={6800} etaSeconds={5300} etaIsFallback={false} />);

    expect(screen.getByText(/dettaglio/i)).toBeInTheDocument();
    expect(screen.getByText(/21,3%/)).toBeInTheDocument();
    expect(screen.getByText(/1h 28m/)).toBeInTheDocument();
  });

  it("shows an indeterminate bar when the total is unknown", () => {
    render(<RunProgress phase="search" done={120} total={null} etaSeconds={null} etaIsFallback={false} />);

    expect(screen.getByTestId("run-progress-bar")).toHaveAttribute("data-indeterminate", "true");
    expect(screen.getByText(/120 annunci/)).toBeInTheDocument();
  });

  it("flags a fallback estimate", () => {
    render(<RunProgress phase="detail" done={10} total={100} etaSeconds={600} etaIsFallback={true} />);

    expect(screen.getByText(/stima approssimativa/i)).toBeInTheDocument();
  });
});
