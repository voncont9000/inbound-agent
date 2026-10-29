import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "../api/client";

export const googleOAuthNavigation = {
  assign: (url: string) => window.location.assign(url),
};

export function useGmailMode() {
  return useQuery({ queryKey: ["gmail-mode"], queryFn: () => unwrap(api.GET("/channels/gmail/mode")) });
}

export function useAssistantChannels(assistantId: string) {
  return useQuery({
    queryKey: ["assistant-channels", assistantId],
    queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/channels", { params: { path: { assistant_id: assistantId } } })),
  });
}

export function useGoogleFeatures(assistantId: string) {
  return useQuery({
    queryKey: ["google-features", assistantId],
    queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/google-features", { params: { path: { assistant_id: assistantId } } })),
  });
}

export function useGoogleOAuth(
  assistantId: string,
  features: Schemas["GmailOAuthStartRequest"]["features"] | undefined,
  onError: (message: string) => void,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/channels/gmail/oauth-start", {
      params: { path: { assistant_id: assistantId } },
      ...(features ? { body: { features } } : {}),
    })),
    onSuccess: ({ url }) => {
      void queryClient.invalidateQueries({ queryKey: ["google-features", assistantId] });
      googleOAuthNavigation.assign(url);
    },
    onError: (caught) => onError(caught instanceof Error ? caught.message : "Unable to connect Google."),
  });
}
