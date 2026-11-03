import { NewPropertyView } from "./NewPropertyView";

// Client-side admin auth: this segment can't be prerendered for instant navigation.
export const instant = false;

export default function Page() {
  return <NewPropertyView />;
}
