import { useMutation, useQuery } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "../api/client";

export function useGmailMode() {
  return useQuery({ queryKey: ["gmail-mode"], queryFn: () => unwrap(api.GET("/channels/gmail/mode")) });
}

export function useAssistantChannels(assistantId: string) {
  return useQuery({
    queryKey: ["assistant-channels", assistantId],
    queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/channels", { params: { path: { assistant_id: assistantId } } })),
  });
}

export function useGoogleOAuth(
  assistantId: string,
  features: Schemas["GmailOAuthStartRequest"]["features"] | undefined,
  onError: (message: string) => void,
) {
  return useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/channels/gmail/oauth-start", {
      params: { path: { assistant_id: assistantId } },
      ...(features ? { body: { features } } : {}),
    })),
    onSuccess: ({ url }) => window.location.assign(url),
    onError: (caught) => onError(caught instanceof Error ? caught.message : "Unable to connect Google."),
  });
}
