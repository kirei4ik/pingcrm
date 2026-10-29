import type { components } from "@/lib/api-types";

type Details = components["schemas"]["GoogleContactDetailsData"];
type GoogleValue = Record<string, unknown>;

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function birthday(value: GoogleValue): { key: string; label: string } | null {
  const raw = value.date;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const date = raw as GoogleValue;
  const month = Number(date.month), day = Number(date.day), year = Number(date.year || 0);
  if (!Number.isInteger(month) || !Number.isInteger(day) || !Number.isInteger(year)
    || month < 1 || month > 12 || day < 1 || year < 0 || year > 9999) return null;
  const parsed = new Date(0);
  parsed.setUTCFullYear(year || 2000, month - 1, day);
  if (parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) return null;
  const monthDay = `${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  return {
    key: year ? `${String(year).padStart(4, "0")}-${monthDay}` : monthDay,
    label: new Intl.DateTimeFormat(undefined, { day: "numeric", month: "long", ...(year ? { year: "numeric" } as const : {}), timeZone: "UTC" }).format(parsed),
  };
}

function address(value: GoogleValue): string {
  return text(value.formattedValue) || ["poBox", "streetAddress", "extendedAddress", "city", "region", "postalCode", "country"]
    .map((key) => text(value[key])).filter(Boolean).join(", ");
}

function safeUrl(value: string): string | null {
  if (!URL.canParse(value)) return null;
  const url = new URL(value);
  return ["https:", "http:"].includes(url.protocol) ? url.href : null;
}

export function GoogleSourceDetails({ details, crmBirthday }: { details: Details; crmBirthday?: string | null }) {
  const birthdays = details.birthdays.map(birthday).filter((item) => item !== null)
    .filter((item) => item.key !== crmBirthday && !(item.key.length === 5 && crmBirthday?.endsWith(item.key)));
  const notes = details.biographies.map((item) => text(item.value)).filter(Boolean);
  const addresses = details.addresses.map(address).filter(Boolean);
  const links = details.urls.map((item) => ({ value: text(item.value), label: text(item.formattedType) || text(item.type) })).filter((item) => item.value);
  const fields = details.userDefined.map((item) => ({ key: text(item.key), value: text(item.value) })).filter((item) => item.value);
  if (![birthdays, notes, addresses, links, fields].some((items) => items.length)) return null;
  const heading = "text-[10px] font-semibold uppercase tracking-wide text-stone-400";
  const content = "mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed text-stone-700 dark:text-stone-300";
  return <div className="mt-4 space-y-4 border-t border-stone-100 pt-4 dark:border-stone-800">
    {birthdays.length > 0 && <section><h3 className={heading}>Birthday in Google</h3>
      {birthdays.map((item, index) => <p key={index} className={content}>{item.label}</p>)}
      {crmBirthday && <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">Different from the birthday in Contact Details.</p>}
    </section>}
    {notes.length > 0 && <section><h3 className={heading}>Notes from Google</h3>{notes.map((note, index) => <p key={index} className={content}>{note}</p>)}</section>}
    {addresses.length > 0 && <section><h3 className={heading}>Addresses</h3>{addresses.map((value, index) => <p key={index} className={content}>{value}</p>)}</section>}
    {links.length > 0 && <section><h3 className={heading}>Links</h3><ul className="mt-1 space-y-2">{links.map((link, index) => {
      const href = safeUrl(link.value);
      return <li key={index} className="break-all text-sm">{link.label && <span className="mr-2 text-stone-500">{link.label}</span>}{href ? <a href={href} target="_blank" rel="noopener noreferrer" className="text-teal-700 underline underline-offset-2 dark:text-teal-400">{link.value}</a> : <span className="text-stone-700 dark:text-stone-300">{link.value}</span>}</li>;
    })}</ul></section>}
    {fields.length > 0 && <dl className="space-y-3">{fields.map((field, index) => <div key={index}><dt className={heading}>{field.key || "Additional information"}</dt><dd className={content}>{field.value}</dd></div>)}</dl>}
  </div>;
}
