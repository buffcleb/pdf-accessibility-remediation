import DOMPurify from "dompurify";
import { chosen, differences, type Readings } from "./readings";

/** A formula's readings, side by side with the formula drawn from its MathML (#403), and from the picture's reading
 * when that's different: the person picks the one that matches what's printed, which goes into the read-aloud
 * text, or writes their own below. */
export function ReadingPicker({ readings, value, onPick, prefix }: {
  readings: Readings; value: string; onPick: (text: string) => void; prefix: string;
}) {
  const current = chosen(value, readings);
  // The MathML was checked by the engine (MathML's elements only); cleaned again here before it's shown
  const clean = (m?: string | null) => m ? DOMPurify.sanitize(m, { USE_PROFILES: { mathMl: true } }) : "";
  const math = clean(readings.mathml);
  const pictured = clean(readings.picture_mathml);
  const differ = differences(readings);
  return (
    <fieldset className="pv-readings">
      <legend>Readings of this formula disagree</legend>
      {math && (
        <div className="pv-formula">
          <span className="hint">The formula, as the AI transcribed it:</span>
          <div className="pv-math" dangerouslySetInnerHTML={{ __html: math }} />
        </div>
      )}
      {pictured && (
        <div className="pv-formula">
          <span className="hint">As read from its picture:</span>
          <div className="pv-math" dangerouslySetInnerHTML={{ __html: pictured }} />
        </div>
      )}
      {differ && <p className="hint">They say different {differ}.</p>}
      {readings.readings.map((r) => (
        <label key={r.source} className="check pv-reading">
          <input type="radio" name={`${prefix}-reading`} checked={current === r.source}
            onChange={() => onPick(r.text)} />
          <span><strong>{r.label}</strong><br />“{r.text}”</span>
        </label>
      ))}
      <span className="hint">Pick the one that matches what&apos;s printed; it becomes the read-aloud text. If none
        does, write your own below. Your choice is kept to improve the engine.</span>
    </fieldset>
  );
}
