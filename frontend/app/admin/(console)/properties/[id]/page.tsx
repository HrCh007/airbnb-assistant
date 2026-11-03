import { Suspense } from "react";
import { PropertyEditor } from "./PropertyEditor";

export const instant = false;

export default async function PropertyPage({ params }: PageProps<"/admin/properties/[id]">) {
  const { id } = await params;
  return (
    <Suspense fallback={null}>
      <PropertyEditor id={id} />
    </Suspense>
  );
}
