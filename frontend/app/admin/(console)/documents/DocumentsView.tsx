"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { card, PageTitle } from "@/components/admin/AdminShell";
import { DocumentsTable } from "@/components/admin/DocumentsPanel";
import { adminApi } from "@/services/api";
import type { DocumentRow } from "@/types";

export function DocumentsView() {
  const [docs, setDocs] = useState<DocumentRow[] | null>(null);
  const load = useCallback(() => adminApi.documents().then(setDocs), []);
  useEffect(() => {
    load();
  }, [load]);
  return (
    <>
      <PageTitle title="Knowledge" subtitle="All uploaded documents across properties. Upload new documents from a property's page." />
      <div className={card}>{docs ? <DocumentsTable docs={docs} onChange={load} showProperty /> : <Loader2 className="size-5 animate-spin text-ink-muted" />}</div>
    </>
  );
}
