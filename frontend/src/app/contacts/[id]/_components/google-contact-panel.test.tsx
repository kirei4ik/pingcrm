import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GoogleContactPanel } from "./google-contact-panel";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("@/lib/api-client", () => ({ client: { GET: mocks.get, POST: mocks.post } }));

const contact = {
  gmail_enabled: false,
  links: [{ id: "link-1", account_email: "person@example.com", resource_name: "people/c123", labels: [{ resource_name: "contactGroups/g1", name: "Важные" }], google_url: null, can_write: true }],
  details: [{ link_id: "link-1", birthdays: [{ date: { month: 5, day: 4 } }], biographies: [], addresses: [], urls: [], userDefined: [] }],
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><GoogleContactPanel contactId="contact-1" displayName="Test Contact" /></QueryClientProvider>);
}

describe("GoogleContactPanel", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.post.mockReset();
    mocks.get.mockResolvedValue({ data: { data: contact }, error: undefined });
  });

  it("shows exact source labels and uses an explicit search fallback without a verified URL", async () => {
    renderPanel();
    expect(await screen.findByText("Важные")).toBeInTheDocument();
    const fallback = screen.getByRole("link", { name: /search in google contacts/i });
    expect(fallback).toHaveAttribute("href", "https://contacts.google.com/?q=Test%20Contact");
    expect(fallback).toHaveAttribute("title", expect.stringContaining("not verified"));
  });

  it("loads a fresh preview and applies only the fields the user selected", async () => {
    mocks.post.mockResolvedValueOnce({ data: { data: {
      preview_id: "preview-1",
      account_email: "person@example.com",
      expires_at: "2026-10-27T12:00:00Z",
      can_write: true,
      changes: [
        { field: "emails", label: "Email", current: ["old@example.com"], proposed: ["new@example.com"], selected_by_default: true },
        { field: "title", label: "Title", current: "Old title", proposed: "New title", selected_by_default: false },
        { field: "notes", label: "Private notes", current: "", proposed: "Do not export", selected_by_default: true },
      ],
    } }, error: undefined, response: new Response(null, { status: 200 }) });
    mocks.post.mockResolvedValueOnce({ data: { data: { status: "updated", updated_fields: ["emails", "title"] } }, error: undefined, response: new Response(null, { status: 200 }) });
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Update in Google" }));
    fireEvent.click(screen.getByRole("button", { name: "Load preview" }));
    expect(await screen.findByText("Old title")).toBeInTheDocument();
    expect(screen.getByText("New title")).toBeInTheDocument();
    expect(screen.queryByText("Do not export")).not.toBeInTheDocument();
    const titleChoice = screen.getAllByRole("checkbox")[1];
    expect(titleChoice).not.toBeChecked();
    fireEvent.click(titleChoice);
    fireEvent.click(screen.getByRole("button", { name: "Apply 2 selected" }));
    await waitFor(() => expect(mocks.post).toHaveBeenLastCalledWith("/api/v1/contacts/{contact_id}/google/apply", {
      params: { path: { contact_id: "contact-1" } },
      body: { preview_id: "preview-1", fields: ["emails", "title"] },
    }));
  });
});

it("explains how to import a missing Google source instead of silently hiding actions", async () => {
  mocks.get.mockResolvedValue({ data: { data: { gmail_enabled: false, links: [], details: [] } } });
  renderPanel();
  expect(await screen.findByText(/No Google source is linked/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open Google settings" })).toHaveAttribute("href", "/settings");
  expect(screen.queryByRole("button", { name: "Update in Google" })).not.toBeInTheDocument();
});

it("mounts the preview outside a transformed column and restores scrolling and focus", async () => {
  mocks.get.mockResolvedValue({ data: { data: contact }, error: undefined });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><div data-testid="animated-column" style={{ transform: "translateY(10px)" }}><GoogleContactPanel contactId="contact-1" displayName="Test Contact" /></div></QueryClientProvider>);
  const trigger = await screen.findByRole("button", { name: "Update in Google" });
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog");
  expect(screen.getByTestId("animated-column")).not.toContainElement(dialog);
  expect(dialog.parentElement?.parentElement).toBe(document.body);
  expect(document.body.style.overflow).toBe("hidden");
  expect(screen.getByRole("button", { name: "Load preview" })).toBeInTheDocument();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(document.body.style.overflow).not.toBe("hidden");
  expect(trigger).toHaveFocus();
});

it("opens the provided direct Google contact address instead of search", async () => {
  const target = "https://contacts.google.com/person/c7243330092283387933";
  mocks.get.mockResolvedValue({ data: { data: { ...contact, links: [{ ...contact.links[0], google_url: target }] } } });
  renderPanel();
  expect(await screen.findByRole("link", { name: "Open in Google Contacts" })).toHaveAttribute("href", target);
});
