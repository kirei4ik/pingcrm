"use client";

import { Check, Link2, RefreshCw, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { client } from "@/lib/api-client";
import type { ConnectedAccounts, SyncState } from "../../_hooks/use-settings-controller";
import { GoogleStatusBanners } from "./google-card-status-banners";

export type GoogleCardProps = {
  connected: ConnectedAccounts;
  googleConnect: SyncState;
  googleSync: SyncState;
  fetchConnectionStatus: () => Promise<void>;
  handleGoogleConnect: (writeContacts?: boolean) => Promise<void>;
  handleGoogleSyncAll: () => Promise<void>;
};

export function GoogleCard({ connected, googleConnect, googleSync, fetchConnectionStatus, handleGoogleConnect, handleGoogleSyncAll }: GoogleCardProps) {
  return (
    <div className="rounded-xl border border-stone-200 bg-white p-5 transition-shadow hover:shadow-sm dark:border-stone-700 dark:bg-stone-900">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <div className={cn("flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-lg font-bold", connected.google ? "bg-red-50 text-red-600 dark:bg-red-950 dark:text-red-300" : "bg-stone-100 text-stone-400 dark:bg-stone-800")}>G</div>
          <div>
            <div className="flex items-center gap-2.5">
              <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">Google Contacts &amp; Calendar</h3>
              <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium", connected.google ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" : "bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400")}>{connected.google ? "Connected" : "Not connected"}</span>
            </div>
            <p className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">Import contacts and read calendar events. Email is not connected or synchronized.</p>
            {connected.google && connected.google_accounts.length > 0 && <div className="mt-2 space-y-1">{connected.google_accounts.map((account) => <div key={account.id} className="flex items-center gap-2 text-xs">
              <span className="inline-flex items-center gap-1 font-medium text-teal-600 dark:text-teal-400"><Check className="h-3 w-3" />{account.email}</span>
              <button type="button" aria-label={`Disconnect ${account.email}`} onClick={() => { void (async () => {
                const response = await client.DELETE("/api/v1/auth/google/accounts/{account_id}", { params: { path: { account_id: account.id } } });
                if (!response.error) await fetchConnectionStatus();
              })(); }} className="text-stone-400 hover:text-red-500" title="Disconnect Google account"><X className="h-3 w-3" /></button>
            </div>)}</div>}
            {connected.google && connected.google_accounts.length === 0 && connected.google_email && <p className="mt-1 text-xs text-teal-600 dark:text-teal-400">Connected as <strong>{connected.google_email}</strong></p>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {connected.google && <button type="button" onClick={() => void handleGoogleSyncAll()} disabled={googleSync.status === "loading"} className="inline-flex items-center gap-1.5 rounded-lg border border-stone-200 px-3 py-1.5 text-xs font-medium text-stone-600 hover:bg-stone-50 disabled:opacity-50 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800">
            {googleSync.status === "success" ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <RefreshCw className={cn("h-3.5 w-3.5", googleSync.status === "loading" && "animate-spin")} />}
            {googleSync.status === "loading" ? "Syncing…" : googleSync.status === "success" ? "Done" : "Sync contacts & calendar"}
          </button>}
          <button type="button" aria-label={connected.google ? "Allow contact updates" : "Connect Google"} onClick={() => void handleGoogleConnect(connected.google)} disabled={googleConnect.status === "loading"} className="inline-flex items-center gap-1.5 rounded-lg bg-teal-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-teal-700 disabled:opacity-50">
            {googleConnect.status === "loading" ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}
            {connected.google ? "Allow updates" : "Connect"}
          </button>
        </div>
      </div>
      {connected.google && <p className="mt-3 text-[11px] text-stone-400">“Allow contact updates” requests additional Contacts permission for changes you explicitly approve in each contact.</p>}
      <GoogleStatusBanners googleConnect={googleConnect} googleSync={googleSync} />
    </div>
  );
}
