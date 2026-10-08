import { createContext, useContext } from "react";

/** Wide content (a report table) can ask for the whole window width: the page drops its width limit until the
 * person narrows it again or leaves the page. (Folding the admin menu away is separate, and always available.) */
export const FullWidth = createContext<{ full: boolean; setFull: (on: boolean) => void }>({ full: false, setFull: () => {} });

export function useFullWidth() {
  return useContext(FullWidth);
}
