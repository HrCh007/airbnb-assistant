"use client";

import { useRouter } from "next/navigation";
import { PageTitle } from "@/components/admin/AdminShell";
import { PropertyForm } from "@/components/admin/PropertyForm";

export function NewPropertyView() {
  const router = useRouter();
  return (
    <>
      <PageTitle title="Add property" subtitle="Each property gets its own isolated knowledge base and coordinates." />
      <PropertyForm onSaved={(p) => router.replace(`/admin/properties/${p.id}`)} />
    </>
  );
}
