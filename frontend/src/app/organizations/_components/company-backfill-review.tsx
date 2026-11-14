"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, RefreshCw } from "lucide-react";
import { client } from "@/lib/api-client";
import type { components } from "@/lib/api-types";

type CompanyGroup = components["schemas"]["CompanyBackfillGroup"];

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "Could not apply the reviewed organization links. Refresh the preview and try again.";
}

export function CompanyBackfillReview() {
  const [open, setOpen] = useState(false);
  const [selectedNames, setSelectedNames] = useState<Set<string>>(() => new Set());
  const [statusMessage, setStatusMessage] = useState("");
  const [errorMessageText, setErrorMessageText] = useState("");
  const queryClient = useQueryClient();
  const preview = useQuery({
    queryKey: ["organizations", "company-backfill-preview"],
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/organizations/company-backfill/preview");
      if (error || !data?.data) throw new Error("Could not load the company-link preview.");
      return data.data;
    },
    enabled: open,
    retry: false,
  });
  const groups = preview.data ?? [];
  const selectedGroups = useMemo(() => groups.filter((group) => selectedNames.has(group.name)), [groups, selectedNames]);
  const selectedContactCount = selectedGroups.reduce((total, group) => total + group.contact_ids.length, 0);

  useEffect(() => {
    setSelectedNames(new Set());
  }, [preview.data]);

  const apply = useMutation({
    mutationFn: async (reviewedGroups: CompanyGroup[]) => {
      const { data, error } = await client.POST("/api/v1/organizations/company-backfill/apply", {
        body: { reviewed_groups: reviewedGroups.map((group) => ({ name: group.name, contact_ids: group.contact_ids, organization_id: group.organization_id })) },
      });
      if (error || !data?.data) throw new Error("Could not apply the reviewed organization links. Refresh the preview and try again.");
      return data.data;
    },
    onSuccess: async (result) => {
      setErrorMessageText("");
      setStatusMessage(`Linked ${result.contacts_linked} contact${result.contacts_linked === 1 ? "" : "s"} from the reviewed groups.`);
      setSelectedNames(new Set());
      await queryClient.invalidateQueries({ queryKey: ["organizations"] });
      await preview.refetch();
    },
    onError: (error: unknown) => {
      setErrorMessageText(errorMessage(error));
      setStatusMessage("");
    },
  });

  const toggleGroup = (groupName: string) => {
    setSelectedNames((current) => {
      const next = new Set(current);
      if (next.has(groupName)) next.delete(groupName);
      else next.add(groupName);
      return next;
    });
    setErrorMessageText("");
    setStatusMessage("");
  };

  return (
    <section className="mb-5 rounded-xl border border-amber-200 bg-amber-50/70 p-4 dark:border-amber-900 dark:bg-amber-950/40">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-stone-900 dark:text-stone-100">Recover organization links from company names</h2>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-stone-600 dark:text-stone-300">
            Review unlinked contacts grouped by their exact company name. Nothing changes until you select groups and apply them; existing manual organization links are left alone.
          </p>
        </div>
        <button type="button" onClick={() => { setOpen((value) => !value); setErrorMessageText(""); }} className="inline-flex shrink-0 items-center gap-2 rounded-lg border border-amber-300 bg-white px-3 py-2 text-xs font-semibold text-stone-700 hover:bg-amber-100 dark:border-amber-800 dark:bg-stone-900 dark:text-stone-200 dark:hover:bg-stone-800">
          {open ? "Hide review" : "Review company links"}<ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
      </div>

      {statusMessage && <p role="status" className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200">{statusMessage}</p>}
      {open && <div className="mt-4 border-t border-amber-200 pt-4 dark:border-amber-900">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-600 dark:text-stone-300">Previewed company groups</h3>
            {preview.data && <p className="mt-0.5 text-[11px] text-stone-500 dark:text-stone-400">{groups.length} groups · {groups.reduce((total, group) => total + group.contact_ids.length, 0)} eligible contacts</p>}
          </div>
          <button type="button" onClick={() => { setErrorMessageText(""); setStatusMessage(""); void preview.refetch(); }} disabled={preview.isFetching || apply.isPending} className="inline-flex items-center gap-1.5 rounded-md border border-stone-200 bg-white px-2.5 py-1.5 text-xs text-stone-600 disabled:opacity-50 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-300">
            <RefreshCw className={`h-3 w-3 ${preview.isFetching ? "animate-spin" : ""}`} />Refresh preview
          </button>
        </div>

        {preview.isLoading && <p className="py-5 text-center text-xs text-stone-500">Loading company groups…</p>}
        {preview.isError && <div className="rounded-lg border border-red-200 bg-white p-3 text-xs text-red-700 dark:border-red-900 dark:bg-stone-900 dark:text-red-300"><p role="alert">Could not load the company-link preview.</p><button type="button" onClick={() => void preview.refetch()} className="mt-1 font-semibold underline">Try again</button></div>}
        {preview.isSuccess && groups.length === 0 && <p className="rounded-lg border border-stone-200 bg-white p-4 text-xs text-stone-500 dark:border-stone-700 dark:bg-stone-900">No unlinked active contacts with a company name were found.</p>}

        {preview.isSuccess && groups.length > 0 && <div className="mb-3 flex flex-wrap gap-2">
          <button type="button" disabled={apply.isPending || preview.isFetching} onClick={() => { setSelectedNames(new Set(groups.map((group) => group.name))); setErrorMessageText(""); setStatusMessage(""); }} className="rounded-md border border-stone-300 px-3 py-1.5 text-xs font-semibold text-stone-700 disabled:opacity-50 dark:border-stone-600 dark:text-stone-200">Select all {groups.length} groups</button>
          <button type="button" disabled={selectedGroups.length === 0 || apply.isPending} onClick={() => { setSelectedNames(new Set()); setErrorMessageText(""); setStatusMessage(""); }} className="rounded-md border border-stone-300 px-3 py-1.5 text-xs text-stone-700 disabled:opacity-50 dark:border-stone-600 dark:text-stone-200">Clear selection</button>
        </div>}

        {preview.isSuccess && groups.length > 0 && <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
          {groups.map((group) => <label key={group.name} className={`flex cursor-pointer items-start gap-3 rounded-lg border bg-white p-3 transition-colors dark:bg-stone-900 ${selectedNames.has(group.name) ? "border-teal-400 ring-1 ring-teal-300 dark:border-teal-700" : "border-stone-200 dark:border-stone-700"}`}>
            <input type="checkbox" checked={selectedNames.has(group.name)} disabled={apply.isPending} onChange={() => toggleGroup(group.name)} className="mt-0.5 h-4 w-4 rounded border-stone-300 text-teal-600 focus:ring-teal-500" aria-label={`Review ${group.name} (${group.contact_ids.length} contacts)`} />
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                <span className="text-sm font-semibold text-stone-900 dark:text-stone-100">{group.name}</span>
                <span className="rounded-full bg-stone-100 px-2 py-0.5 text-[11px] font-medium text-stone-600 dark:bg-stone-800 dark:text-stone-300">{group.contact_ids.length} contact{group.contact_ids.length === 1 ? "" : "s"}</span>
              </span>
              <span className="mt-1 block text-[11px] text-stone-500 dark:text-stone-400">
                {group.organization_id ? `Will link to ${group.organization_name ?? "an existing exact-name organization"}.` : "Will create an organization with this exact name and link the selected contacts."}
              </span>
            </span>
          </label>)}
        </div>}

        {errorMessageText && <p role="alert" className="mt-3 rounded-lg border border-red-200 bg-white px-3 py-2 text-xs text-red-700 dark:border-red-900 dark:bg-stone-900 dark:text-red-300">{errorMessageText}</p>}
        {groups.length > 0 && preview.isSuccess && <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-amber-200 pt-3 dark:border-amber-900">
          <p className="text-xs text-stone-600 dark:text-stone-300">{selectedGroups.length} groups and {selectedContactCount} contacts selected</p>
          <button type="button" onClick={() => apply.mutate(selectedGroups)} disabled={selectedGroups.length === 0 || apply.isPending} className="inline-flex items-center gap-2 rounded-lg bg-teal-700 px-3 py-2 text-xs font-semibold text-white hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50">
            {apply.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
            {apply.isPending ? "Applying reviewed links…" : `Apply ${selectedGroups.length} reviewed group${selectedGroups.length === 1 ? "" : "s"}`}
          </button>
        </div>}
      </div>}
    </section>
  );
}
