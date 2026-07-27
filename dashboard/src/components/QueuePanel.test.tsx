import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QueuePanel } from "./QueuePanel";
import type { QueueOut } from "../types";

const RUNNING: QueueOut = {
  halted: false, halted_reason: null, halted_at: null,
  current: {
    slug: "opel", brand: "Opel", phase: "detail", done: 1449, total: 6800,
    percent: 21.3, eta_seconds: 5300, eta_is_fallback: false,
    started_at: "2026-07-27T14:15:33",
  },
  pending: [
    { slug: "toyota", brand: "Toyota", position: 1, eta_seconds: 7200 },
    { slug: "kia", brand: "Kia", position: 2, eta_seconds: 3600 },
  ],
  total_eta_seconds: 54000,
};

describe("QueuePanel", () => {
  it("shows the running brand and how many are waiting", () => {
    render(<QueuePanel queue={RUNNING} onResume={vi.fn()} />);

    expect(screen.getByText(/Opel/)).toBeInTheDocument();
    expect(screen.getByText(/2 marche in attesa/i)).toBeInTheDocument();
    expect(screen.getByText(/15h 0m/)).toBeInTheDocument();
  });

  it("shows an idle message when nothing is running", () => {
    render(
      <QueuePanel
        queue={{ halted: false, halted_reason: null, halted_at: null, current: null, pending: [], total_eta_seconds: null }}
        onResume={vi.fn()}
      />,
    );

    expect(screen.getByText(/nessuna scansione in corso/i)).toBeInTheDocument();
  });

  it("shows the halt banner with its reason and calls onResume", async () => {
    const onResume = vi.fn();
    render(
      <QueuePanel
        queue={{
          halted: true, halted_reason: "blocco rilevato su Toyota",
          halted_at: "2026-07-27T04:12:00", current: null, pending: [], total_eta_seconds: null,
        }}
        onResume={onResume}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/coda ferma/i);
    expect(screen.getByRole("alert")).toHaveTextContent(/blocco rilevato su Toyota/);

    await userEvent.click(screen.getByRole("button", { name: /riprendi coda/i }));

    expect(onResume).toHaveBeenCalled();
  });

  it("renders nothing while the queue is still loading", () => {
    const { container } = render(<QueuePanel queue={null} onResume={vi.fn()} />);

    expect(container).toBeEmptyDOMElement();
  });
});
