import { useEffect, useState, type ChangeEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap, uploadKnowledgeFile } from "../api/client";
import { normalizeWebsiteUrl } from "../lib/url";
import { Badge, EmptyState, FieldError, LoadingState } from "./States";

function isHttpUrl(value: string) {
  try { const parsed = new URL(value); return parsed.protocol === "http:" || parsed.protocol === "https:"; } catch { return false; }
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

/** Knowledge sources, imports and the digest are saved immediately, separately from the config. */
export function KnowledgeSources({ assistantId, maxChars }: { assistantId: string; maxChars: number }) {
  const id = assistantId;
  const queryClient = useQueryClient();
  const sourcesQuery = useQuery({ queryKey: ["knowledge-sources", id], queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/knowledge/sources", { params: { path: { assistant_id: id } } })) });
  const digestQuery = useQuery({ queryKey: ["knowledge-digest", id], queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/knowledge/digest", { params: { path: { assistant_id: id } } })) });
  const [url, setUrl] = useState("");
  const [urlError, setUrlError] = useState("");
  const [fileError, setFileError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [digest, setDigest] = useState("");
  const [jobId, setJobId] = useState("");
  const [status, setStatus] = useState("");
  useEffect(() => { if (digestQuery.data) setDigest(digestQuery.data.content); }, [digestQuery.data]);
  const jobQuery = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => unwrap(api.GET("/jobs/{job_id}", { params: { path: { job_id: jobId ?? "" } } })),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const state = query.state.data?.status;
      return state === "queued" || state === "running" ? 1000 : false;
    },
  });
  useEffect(() => {
    if (!jobQuery.data) return;
    setStatus(jobQuery.data.status === "failed" ? `Import failed: ${jobQuery.data.error ?? "Unknown error"}` : `Import ${jobQuery.data.status}`);
    if (jobQuery.data.status === "succeeded") {
      void queryClient.invalidateQueries({ queryKey: ["knowledge-sources", id] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-digest", id] });
    }
  }, [jobQuery.data, id, queryClient]);
  const digestSave = useMutation({
    mutationFn: () => unwrap(api.PUT("/assistants/{assistant_id}/knowledge/digest", { params: { path: { assistant_id: id } }, body: { content: digest } })),
    onSuccess: () => { setStatus("Digest saved"); void queryClient.invalidateQueries({ queryKey: ["knowledge-digest", id] }); },
  });
  const sourceAdd = useMutation({
    mutationFn: (source: { uri: string }) => unwrap(api.POST("/assistants/{assistant_id}/knowledge/sources", { params: { path: { assistant_id: id } }, body: { kind: "url", uri: source.uri } })),
    onSuccess: () => { setUrl(""); setUrlError(""); setStatus("Website added"); void queryClient.invalidateQueries({ queryKey: ["knowledge-sources", id] }); },
  });
  const upload = useMutation({
    mutationFn: (uploadFile: File) => uploadKnowledgeFile(id, uploadFile),
    onSuccess: () => { setFile(null); setFileError(""); setStatus("File uploaded"); void queryClient.invalidateQueries({ queryKey: ["knowledge-sources", id] }); },
  });
  const importKnowledge = useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/knowledge/import", { params: { path: { assistant_id: id } } })),
    onSuccess: (job) => { setJobId(job.id); setStatus(`Import ${job.status}`); },
  });
  return <>
    <section className="panel stack" aria-labelledby="knowledge-sources-heading"><h2 id="knowledge-sources-heading">Knowledge sources</h2>
      <p className="help">Websites and documents are added immediately; they do not need the Save button.</p>
      {sourcesQuery.isLoading && <LoadingState />}
      {sourcesQuery.data?.items.map((source) => <div className="list-row" key={source.id}><div><strong>{source.kind}</strong> · {source.uri}<br /><span className="muted">{source.fetched_at ? `Fetched ${formatDate(source.fetched_at)}` : "Not imported yet"}</span></div><Badge tone={source.status === "succeeded" ? "success" : "warning"}>{source.status}</Badge></div>)}
      {!sourcesQuery.isLoading && !sourcesQuery.data?.items.length && <EmptyState>No knowledge sources yet.</EmptyState>}
      <form className="toolbar" onSubmit={(event) => { event.preventDefault(); const normalizedUrl = normalizeWebsiteUrl(url); if (!isHttpUrl(normalizedUrl)) { setUrlError("Enter a valid http or https URL."); return; } sourceAdd.mutate({ uri: normalizedUrl }); }}>
        <div className="field"><label htmlFor="website-url">Website URL</label><input id="website-url" type="text" inputMode="url" autoComplete="url" placeholder="example.co.uk" value={url} onChange={(event) => setUrl(event.target.value)} aria-invalid={Boolean(urlError)} aria-describedby={urlError ? "website-url-error" : undefined} /></div>
        <button type="submit">Add website</button>
        <FieldError id="website-url-error">{urlError}</FieldError>
      </form>
      <div className="field"><label htmlFor="knowledge-file">Upload a document</label><input id="knowledge-file" type="file" accept=".pdf,.docx,.md,.txt" onChange={(event: ChangeEvent<HTMLInputElement>) => {
        const selectedFile = event.target.files?.[0] ?? null;
        setFile(selectedFile);
        const extension = selectedFile?.name.split(".").at(-1)?.toLowerCase();
        setFileError(selectedFile && !["pdf", "docx", "md", "txt"].includes(extension ?? "") ? "Choose a PDF, DOCX, Markdown, or text file." : "");
      }} aria-invalid={Boolean(fileError)} aria-describedby={fileError ? "knowledge-file-error" : undefined} /><FieldError id="knowledge-file-error">{fileError}</FieldError><button type="button" disabled={!file || Boolean(fileError) || upload.isPending} onClick={() => file && upload.mutate(file)}>Upload file</button></div>
      <button type="button" className="button-primary" disabled={importKnowledge.isPending || jobQuery.data?.status === "queued" || jobQuery.data?.status === "running"} onClick={() => importKnowledge.mutate()}>Import knowledge</button>
      <div aria-live="polite" role="status">{status}</div>
    </section>
    <section className="panel stack" aria-labelledby="knowledge-digest-heading"><h2 id="knowledge-digest-heading">Knowledge digest</h2>
      {digestQuery.isLoading && <LoadingState />}
      <div className="field"><label htmlFor="digest-content">Digest content</label><textarea id="digest-content" maxLength={maxChars} value={digest} onChange={(event) => setDigest(event.target.value)} /><span className="help">{digest.length} / {maxChars} characters. The digest has its own save button.</span></div>
      <button type="button" className="button-row" onClick={() => digestSave.mutate()} disabled={digestSave.isPending}>Save digest</button>
    </section>
  </>;
}
