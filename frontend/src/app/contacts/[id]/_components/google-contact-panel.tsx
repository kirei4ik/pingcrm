"use client";

import { useState } from "react";
import { ExternalLink, RefreshCw, X } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/lib/api-client";
import type { components } from "@/lib/api-types";

type GoogleContactData = components["schemas"]["GoogleContactReadData"];
type GoogleLink = components["schemas"]["GoogleContactLinkData"];
type Change = components["schemas"]["GoogleExportChange"];
type Preview = components["schemas"]["GoogleExportPreviewData"];
const EXPORT_FIELDS = new Set(["emails", "phones", "full_name", "company", "title", "birthday", "urls"]);

function formatValue(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch (error: unknown) {
    console.error("Could not format Google contact value", error);
    return String(value);
  }
}

function verifiedGoogleContactUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "contacts.google.com" ? url.toString() : null;
  } catch (error: unknown) {
    console.error("Google contact returned an invalid URL", error);
    return null;
  }
}

function GooglePreviewDialog({
  contactId,
  link,
  onClose,
}: {
  contactId: string;
  link: GoogleLink;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [message, setMessage] = useState("");

  const loadPreview = async () => {
    setLoading(true);
    setMessage("");
    try {
      const { data, error, response } = await client.POST("/api/v1/contacts/{contact_id}/google/preview", {
        params: { path: { contact_id: contactId } },
        body: { link_id: link.id },
      });
      if (error || !data?.data) {
        setMessage(response.status === 403 ? "Google needs renewed Contacts access. Reconnect the account in Settings." : "Could not load a fresh Google preview. Please try again.");
        return;
      }
      setPreview(data.data);
      setSelected(data.data.changes.filter((change) => EXPORT_FIELDS.has(change.field) && change.selected_by_default).map((change) => change.field));
    } catch (error: unknown) {
      console.error("Could not load a fresh Google contact preview", error);
      setMessage("Could not load a fresh Google preview. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const apply = async () => {
    if (!preview || selected.length === 0) return;
    setApplying(true);
    setMessage("");
    try {
      const { error, response } = await client.POST("/api/v1/contacts/{contact_id}/google/apply", {
        params: { path: { contact_id: contactId } },
        body: { preview_id: preview.preview_id, fields: selected },
      });
      if (error) {
        if (response.status === 409) {
          setPreview(null);
          setMessage("Google or this contact changed since the preview. Review a new preview before applying.");
        } else if (response.status === 403) {
          setMessage("Google Contacts write access is missing or expired. Reconnect the account in Settings.");
        } else {
          setPreview(null);
          setMessage("Google did not confirm this update. Refresh the preview to check the latest state before trying again.");
        }
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ["contacts", contactId] });
      await queryClient.invalidateQueries({ queryKey: ["contacts", contactId, "google"] });
      onClose();
    } catch (error: unknown) {
      console.error("Could not apply Google contact update", error);
      setPreview(null);
      setMessage("Google did not confirm this update. Refresh the preview to check the latest state before trying again.");
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="presentation" style={{ background: "rgba(28,25,23,0.48)" }}>
      <section role="dialog" aria-modal="true" aria-labelledby="google-preview-title" className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-6 shadow-2xl dark:bg-stone-900">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 id="google-preview-title" className="text-lg font-bold text-stone-900 dark:text-stone-100">Update in Google Contacts</h2>
            <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">{link.account_email} · review each selected field before applying.</p>
          </div>
          <button type="button" aria-label="Close preview" onClick={onClose} className="rounded p-1 text-stone-400 hover:bg-stone-100 dark:hover:bg-stone-800"><X className="h-4 w-4" /></button>
        </div>
        {!preview && <p className="mb-4 rounded-lg bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">This reads the latest Google contact and prepares a preview. It will not write until you select fields and press Apply.</p>}
        {message && <p role="alert" className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">{message}</p>}
        {preview && (
          <div className="space-y-3">
            {!preview.can_write && <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">This account does not have Google Contacts write permission. Reconnect it in Settings to continue.</p>}
            {preview.changes.filter((change) => EXPORT_FIELDS.has(change.field)).length === 0 ? <p className="rounded-lg border border-stone-200 p-4 text-sm text-stone-500 dark:border-stone-700">There are no supported changes to preview.</p> : preview.changes.filter((change) => EXPORT_FIELDS.has(change.field)).map((change) => (
              <label key={change.field} className="block rounded-lg border border-stone-200 p-3 dark:border-stone-700">
                <span className="flex items-center gap-2 text-sm font-semibold text-stone-800 dark:text-stone-200">
                  <input type="checkbox" checked={selected.includes(change.field)} disabled={!preview.can_write || applying} onChange={(event) => setSelected((items) => event.target.checked ? [...items, change.field] : items.filter((item) => item !== change.field))} />
                  {change.label}
                </span>
                <span className="mt-2 grid gap-2 sm:grid-cols-2">
                  <span className="min-w-0"><span className="block text-[10px] font-semibold uppercase text-stone-400">Current in Google</span><pre className="mt-1 whitespace-pre-wrap break-words text-xs text-stone-600 dark:text-stone-300">{formatValue(change.current)}</pre></span>
                  <span className="min-w-0"><span className="block text-[10px] font-semibold uppercase text-stone-400">Proposed from PingCRM</span><pre className="mt-1 whitespace-pre-wrap break-words text-xs text-stone-800 dark:text-stone-100">{formatValue(change.proposed)}</pre></span>
                </span>
              </label>
            ))}
            <p className="text-xs text-stone-400">Preview expires {new Date(preview.expires_at).toLocaleString()}.</p>
          </div>
        )}
        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-600 dark:border-stone-700 dark:text-stone-300">Cancel</button>
          {!preview ? <button type="button" disabled={loading} onClick={() => void loadPreview()} className="inline-flex items-center gap-2 rounded-lg bg-teal-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">{loading && <RefreshCw className="h-3.5 w-3.5 animate-spin" />} {loading ? "Loading preview…" : "Load preview"}</button> : (
            <button type="button" disabled={!preview.can_write || selected.length === 0 || applying} onClick={() => void apply()} className="rounded-lg bg-teal-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">{applying ? "Applying…" : `Apply ${selected.length} selected`}</button>
          )}
        </div>
      </section>
    </div>
  );
}

export function GoogleContactPanel({ contactId, displayName }: { contactId: string; displayName: string }) {
  const [selectedLinkId, setSelectedLinkId] = useState("");
  const [showPreview, setShowPreview] = useState(false);
  const query = useQuery({
    queryKey: ["contacts", contactId, "google"],
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/contacts/{contact_id}/google", { params: { path: { contact_id: contactId } } });
      if (error || !data?.data) throw new Error("Google contact details could not be loaded");
      return data.data;
    },
    enabled: Boolean(contactId),
  });
  const links = query.data?.links ?? [];
  const selectedLink = links.find((link) => link.id === selectedLinkId) ?? links[0];
  const details = query.data?.details.find((detail) => detail.link_id === selectedLink?.id);
  const googleSearchUrl = `https://contacts.google.com/?q=${encodeURIComponent(displayName)}`;

  if (query.isLoading) return <section className="rounded-xl border border-stone-200 bg-white p-4 text-sm text-stone-400 dark:border-stone-700 dark:bg-stone-900">Loading Google contact…</section>;
  if (query.isError) return <section className="rounded-xl border border-stone-200 bg-white p-4 text-sm text-stone-500 dark:border-stone-700 dark:bg-stone-900">Google contact details are unavailable. <button type="button" onClick={() => void query.refetch()} className="text-teal-600 underline">Retry</button></section>;
  if (links.length === 0) return null;

  const googleTarget = verifiedGoogleContactUrl(selectedLink?.google_url ?? null);
  return (
    <>
      <section className="rounded-xl border border-stone-200 bg-white p-5 dark:border-stone-700 dark:bg-stone-900">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-stone-900 dark:text-stone-100">Google Contacts</h2>
            <p className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">Source details stay separate from CRM tags and notes.</p>
          </div>
          {links.length > 1 && <label className="text-xs text-stone-500">Google account <select aria-label="Google contact account" value={selectedLink?.id ?? ""} onChange={(event) => setSelectedLinkId(event.target.value)} className="ml-2 max-w-52 rounded-md border border-stone-200 bg-white px-2 py-1 text-xs dark:border-stone-700 dark:bg-stone-800 dark:text-stone-100">{links.map((link) => <option key={link.id} value={link.id}>{link.account_email}</option>)}</select></label>}
        </div>
        {selectedLink && <div className="mt-3 flex flex-wrap items-center gap-2">
          {links.length === 1 && <span className="text-xs text-stone-500">{selectedLink.account_email}</span>}
          {selectedLink.labels.map((label) => <span key={label.resource_name} className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300">{label.name}</span>)}
          <a href={googleTarget ?? googleSearchUrl} target="_blank" rel="noopener noreferrer" className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-teal-700 hover:underline dark:text-teal-400" title={googleTarget ? "Open this linked Google contact" : "Search Google Contacts by name; this link is not verified"}>
            {googleTarget ? "Open in Google Contacts" : "Search in Google Contacts"}<ExternalLink className="h-3 w-3" />
          </a>
          <button type="button" onClick={() => setShowPreview(true)} disabled={!selectedLink.can_write} title={selectedLink.can_write ? "Review selected changes before updating Google" : "Reconnect this account with Contacts write access in Settings"} className="rounded-lg bg-teal-600 px-3 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">Update in Google</button>
        </div>}
        {details && <div className="mt-4 grid gap-3 border-t border-stone-100 pt-3 dark:border-stone-800 sm:grid-cols-2">
          {([
            ["Birthdays", details.birthdays], ["Biographies", details.biographies], ["Addresses", details.addresses], ["Links", details.urls], ["Other Google fields", details.userDefined],
          ] as const).filter(([, values]) => values.length > 0).map(([label, values]) => <div key={label} className="min-w-0"><h3 className="text-[10px] font-semibold uppercase tracking-wide text-stone-400">{label}</h3><pre className="mt-1 whitespace-pre-wrap break-words text-xs text-stone-600 dark:text-stone-300">{values.map(formatValue).join("\n")}</pre></div>)}
        </div>}
        {selectedLink && !selectedLink.can_write && <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">Read-only access. Re-authorize Google in Settings to enable manual updates.</p>}
      </section>
      {showPreview && selectedLink && <GooglePreviewDialog contactId={contactId} link={selectedLink} onClose={() => setShowPreview(false)} />}
    </>
  );
}
