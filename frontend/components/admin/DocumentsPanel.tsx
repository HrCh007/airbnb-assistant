"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Eye, Loader2, RefreshCw, Trash2, Upload } from "lucide-react";
import { adminApi, ApiError } from "@/services/api";
import type { DocumentRow } from "@/types";
import { btnPrimary, btnSecondary, card } from "./AdminShell";

const ACCEPT = ".pdf,.docx,.txt,.md,.markdown,.csv,.json";

export function StatusBadge({ status, error }: { status: DocumentRow["status"]; error?: string | null }) {
  const map = {
    ready: "bg-green-50 text-leaf-600",
    failed: "bg-terracotta-50 text-terracotta-700",
    processing: "bg-amber-50 text-amber-800",
    pending: "bg-sand-100 text-ink-soft",
  } as const;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${map[status]}`} title={error ?? undefined}>
      {status === "ready" && <CheckCircle2 className="size-3" />}
      {status === "failed" && <AlertCircle className="size-3" />}
      {(status === "processing" || status === "pending") && <Loader2 className="size-3 animate-spin" />}
      {status}
    </span>
  );
}

const fmtDate = (s?: string | null) => (s ? new Date(s).toLocaleString() : "—");

export function DocumentsTable({ docs, onChange, showProperty }: { docs: DocumentRow[]; onChange: () => void; showProperty?: boolean }) {
  const [preview, setPreview] = useState<{ doc: DocumentRow; chunks: { index: number; section?: string; content: string; is_sensitive: boolean }[] } | null>(null);
  if (!docs.length) return <p className="text-sm text-ink-muted">No documents uploaded yet.</p>;
  return (
    <>
      <div className="-mx-5 overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="text-left text-xs text-ink-muted">
            <tr className="border-b border-sand-100">
              <th className="px-5 py-2 font-medium">Document</th>
              {showProperty && <th className="py-2 font-medium">Property</th>}
              <th className="py-2 font-medium">Uploaded</th>
              <th className="py-2 font-medium">Status</th>
              <th className="py-2 font-medium">Chunks</th>
              <th className="py-2 font-medium">Last indexed</th>
              <th className="px-5 py-2" />
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id} className="border-b border-sand-50 align-top" data-testid="document-row">
                <td className="px-5 py-2.5">
                  <p className="font-medium text-ink">{d.filename}</p>
                  <p className="text-xs text-ink-muted uppercase">{d.document_type} · {(d.size_bytes / 1024).toFixed(1)} KB</p>
                  {d.error_message && <p className="mt-1 text-xs text-terracotta-700">{d.error_message}</p>}
                </td>
                {showProperty && <td className="py-2.5 text-ink-soft">{d.property_name}</td>}
                <td className="py-2.5 text-ink-soft">{fmtDate(d.uploaded_at)}</td>
                <td className="py-2.5"><StatusBadge status={d.status} error={d.error_message} /></td>
                <td className="py-2.5">{d.chunk_count}</td>
                <td className="py-2.5 text-ink-soft">{fmtDate(d.last_indexed_at)}</td>
                <td className="px-5 py-2.5">
                  <div className="flex justify-end gap-1">
                    <button type="button" title="View chunks" className="rounded-lg p-1.5 text-ink-soft hover:bg-sand-50"
                      onClick={async () => setPreview({ doc: d, chunks: await adminApi.chunks(d.id) })}>
                      <Eye className="size-4" />
                    </button>
                    <button type="button" title="Re-index" className="rounded-lg p-1.5 text-ink-soft hover:bg-sand-50"
                      onClick={async () => { await adminApi.reindex(d.id); onChange(); }}>
                      <RefreshCw className="size-4" />
                    </button>
                    <button type="button" title="Remove" className="rounded-lg p-1.5 text-terracotta-700 hover:bg-terracotta-50"
                      onClick={async () => {
                        if (confirm(`Remove "${d.filename}" and its knowledge chunks?`)) {
                          await adminApi.deleteDocument(d.id);
                          onChange();
                        }
                      }}>
                      <Trash2 className="size-4" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/30 p-4" onClick={() => setPreview(null)} role="dialog" aria-modal="true">
          <div className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-display text-lg font-semibold">{preview.doc.filename}</h3>
            <p className="mb-3 text-xs text-ink-muted">{preview.chunks.length} chunks</p>
            <ol className="space-y-3">
              {preview.chunks.map((c) => (
                <li key={c.index} className="rounded-xl border border-sand-100 p-3 text-xs">
                  <p className="mb-1 font-medium text-ink-soft">#{c.index} {c.section} {c.is_sensitive && <span className="ml-1 rounded bg-terracotta-50 px-1.5 text-terracotta-700">sensitive</span>}</p>
                  <pre className="font-sans whitespace-pre-wrap text-ink">{c.content}</pre>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </>
  );
}

export function DocumentsPanel({ propertyId }: { propertyId: string }) {
  const [docs, setDocs] = useState<DocumentRow[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => adminApi.documents(propertyId).then(setDocs), [propertyId]);
  useEffect(() => {
    load();
  }, [load]);
  // Poll while anything is still processing.
  useEffect(() => {
    if (!docs?.some((d) => d.status === "pending" || d.status === "processing")) return;
    const t = setTimeout(load, 1500);
    return () => clearTimeout(t);
  }, [docs, load]);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    setError(null);
    try {
      for (const f of Array.from(files)) await adminApi.upload(propertyId, f);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Upload failed.");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
      load();
    }
  };

  return (
    <section className={card}>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-semibold">Knowledge documents</h2>
          <p className="text-sm text-ink-soft">PDF, DOCX, TXT, Markdown, CSV or JSON · max 10 MB. Only this property&apos;s guests can query them.</p>
        </div>
        <div className="flex gap-2">
          <button type="button" className={btnSecondary} onClick={load}><RefreshCw className="size-4" /> Refresh</button>
          <button type="button" className={btnPrimary} disabled={uploading} onClick={() => fileRef.current?.click()}>
            {uploading ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />} Upload
          </button>
          <input ref={fileRef} type="file" accept={ACCEPT} multiple hidden onChange={(e) => upload(e.target.files)} data-testid="file-input" />
        </div>
      </div>
      {error && <p role="alert" className="mb-3 text-sm text-terracotta-700">{error}</p>}
      {docs ? <DocumentsTable docs={docs} onChange={load} /> : <Loader2 className="size-5 animate-spin text-ink-muted" />}
    </section>
  );
}
