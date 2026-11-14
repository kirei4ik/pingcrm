import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GoogleSourceDetails } from "./google-source-details";

const details = {
  link_id: "source-1",
  birthdays: [{ date: { year: 1992, month: 12, day: 18 }, metadata: { source: { id: "internal-google-id", type: "CONTACT" } } }],
  biographies: [{ value: "First reminder\nSecond reminder", contentType: "TEXT_PLAIN", metadata: { primary: true } }],
  addresses: [{ streetAddress: "10 Garden Street", city: "London", postalCode: "N1" }],
  urls: [{ value: "https://example.com/profile", formattedType: "Work" }],
  userDefined: [{ key: "How we met", value: "Conference" }],
};

describe("GoogleSourceDetails", () => {
  it("renders useful values without metadata or a duplicate CRM birthday", () => {
    const { container } = render(<GoogleSourceDetails details={details} crmBirthday="1992-12-18" />);
    expect(screen.getByRole("heading", { name: "Notes from Google" })).toBeInTheDocument();
    expect(screen.getByText(/First reminder/).textContent).toBe("First reminder\nSecond reminder");
    expect(screen.getByText("10 Garden Street, London, N1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "https://example.com/profile" })).toHaveAttribute("href", "https://example.com/profile");
    expect(screen.getByText("Conference")).toBeInTheDocument();
    expect(screen.queryByText("Birthday in Google")).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/metadata|internal-google-id|TEXT_PLAIN|CONTACT/);
    expect(container.querySelector("pre")).toBeNull();
  });

  it("keeps a different source birthday visible in a readable format", () => {
    render(<GoogleSourceDetails details={details} crmBirthday="1992-12-19" />);
    expect(screen.getByText("Birthday in Google")).toBeInTheDocument();
    expect(screen.getByText(/Different from the birthday/)).toBeInTheDocument();
    expect(screen.getByText(/1992/).textContent).toMatch(/18/);
  });

  it("does not invent a year for a yearless birthday", () => {
    const source = { ...details, birthdays: [{ date: { month: 2, day: 29 } }] };
    const { container } = render(<GoogleSourceDetails details={source} />);
    expect(screen.getByText("Birthday in Google")).toBeInTheDocument();
    expect(container.textContent).not.toContain("2000");
  });

  it("does not activate unsafe URLs or interpret Google notes as HTML", () => {
    const { container } = render(<GoogleSourceDetails details={{ ...details, urls: [{ value: "javascript:alert(1)" }], biographies: [{ value: "<img src=x onerror=alert(1)>" }] }} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
  });
});
