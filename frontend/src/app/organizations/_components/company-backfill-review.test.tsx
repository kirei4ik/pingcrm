import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CompanyBackfillReview } from "./company-backfill-review";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("@/lib/api-client", () => ({ client: { GET: mocks.get, POST: mocks.post } }));

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><CompanyBackfillReview /></QueryClientProvider>);
}

describe("CompanyBackfillReview", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.post.mockReset();
    mocks.get.mockResolvedValue({ data: { data: [
      { name: "Acme Inc", contact_ids: ["contact-1", "contact-2"], organization_id: "org-1", organization_name: "Acme Incorporated" },
      { name: "Beta Corp", contact_ids: ["contact-3"], organization_id: null, organization_name: null },
    ] }, error: undefined });
    mocks.post.mockResolvedValue({ data: { data: { contacts_linked: 2 } }, error: undefined });
  });

  it("previews groups and applies only explicitly reviewed contact IDs", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Review company links" }));
    expect(await screen.findByText("Acme Inc")).toBeInTheDocument();
    expect(screen.getByText("2 contacts")).toBeInTheDocument();
    expect(screen.getByText("Will link to Acme Incorporated.")).toBeInTheDocument();
    expect(screen.getByText("Will create an organization with this exact name and link the selected contacts.")).toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("checkbox", { name: "Review Acme Inc (2 contacts)" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply 1 reviewed group" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/api/v1/organizations/company-backfill/apply", {
      body: { reviewed_groups: [{ name: "Acme Inc", contact_ids: ["contact-1", "contact-2"], organization_id: "org-1" }] },
    }));
    expect(await screen.findByRole("status")).toHaveTextContent("Linked 2 contacts from the reviewed groups.");
  });
});

it("selects all previewed groups without applying until confirmed, then clears selection", async () => {
  mocks.post.mockClear();
  mocks.get.mockResolvedValue({ data: { data: [
    { name: "Acme", contact_ids: ["one"], organization_id: null },
    { name: "Beta", contact_ids: ["two", "three"], organization_id: null },
  ] } });
  renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "Review company links" }));
  fireEvent.click(await screen.findByRole("button", { name: "Select all 2 groups" }));
  expect(screen.getByText("2 groups and 3 contacts selected")).toBeInTheDocument();
  expect(mocks.post).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Apply 2 reviewed groups" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
  expect(screen.getByRole("button", { name: "Apply 0 reviewed groups" })).toBeDisabled();
});
