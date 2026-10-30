import { useQuery } from "@tanstack/react-query";
import { client } from "@/lib/api-client";

export function googleTelegramUrl(value: unknown): string | null {
  if (typeof value !== "string" || !URL.canParse(value)) return null;
  const url = new URL(value);
  if (url.protocol !== "https:" || !["t.me", "telegram.me", "www.t.me"].includes(url.hostname) || url.username || url.password) return null;
  return url.pathname.length > 1 ? url.href : null;
}

export function useGoogleContact(contactId: string) {
  return useQuery({
    queryKey: ["contacts", contactId, "google"],
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/contacts/{contact_id}/google", { params: { path: { contact_id: contactId } } });
      if (error || !data?.data) throw new Error("Google contact details could not be loaded");
      return data.data;
    },
    enabled: Boolean(contactId),
  });
}
