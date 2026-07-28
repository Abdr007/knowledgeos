"use client";

/** The Grounding Meter — this interface's signature element.
 *
 * Every RAG demo shows you an answer. This shows you the DECISION behind it:
 * the top retrieval score plotted on a calibrated scale, with the refusal floor
 * marked as a hard tick. When the needle lands left of the tick the system
 * refuses instead of answering, and you can see exactly that happen.
 *
 * It exists because the floor is a real number in this system (RELEVANCE_FLOOR,
 * cosine 0.58, chosen by measurement) rather than a vibe — so it can be drawn.
 */

interface Props {
  /** Top cosine similarity from the dense retriever, 0..1. */
  score: number;
  /** RELEVANCE_FLOOR — below this the system refuses. */
  floor: number;
  refused: boolean;
  label?: string;
}

const MIN = 0.3;
const MAX = 0.9;

function toPercent(value: number) {
  return Math.max(0, Math.min(100, ((value - MIN) / (MAX - MIN)) * 100));
}

export function GroundingMeter({ score, floor, refused, label = "Retrieval confidence" }: Props) {
  const scorePct = toPercent(score);
  const floorPct = toPercent(floor);
  const colour = refused ? "var(--color-refused)" : "var(--color-verified)";

  return (
    <div className="w-full">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="eyebrow">{label}</span>
        <span className="num text-[11px]" style={{ color: colour }}>
          {score.toFixed(3)}
          <span className="text-faint"> / floor {floor.toFixed(2)}</span>
        </span>
      </div>

      <div className="relative h-6">
        {/* Scale with tick marks every 0.1 — an instrument, not a progress bar. */}
        <div className="absolute inset-x-0 top-2 h-[3px] bg-rule rounded-full overflow-hidden">
          <div
            className="h-full sweep rounded-full"
            style={{ width: `${scorePct}%`, background: colour }}
          />
        </div>

        {[0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].map((tick) => (
          <div
            key={tick}
            className="absolute top-[14px] w-px h-1.5 bg-rule-bright"
            style={{ left: `${toPercent(tick)}%` }}
          />
        ))}

        {/* The floor: the actual decision boundary. */}
        <div
          className="absolute -top-0.5 h-[18px] w-[2px]"
          style={{ left: `${floorPct}%`, background: "var(--color-signal)" }}
          title={`Refusal floor: ${floor}`}
        />
        <div
          className="absolute top-[19px] eyebrow !text-[8px] whitespace-nowrap"
          style={{
            left: `${floorPct}%`,
            transform: "translateX(-50%)",
            color: "var(--color-signal)",
          }}
        >
          floor
        </div>

        {/* The needle. */}
        <div
          className="absolute top-0 w-[7px] h-[7px] rotate-45 rounded-[1px]"
          style={{
            left: `${scorePct}%`,
            transform: "translateX(-50%) rotate(45deg)",
            background: colour,
            boxShadow: `0 0 10px ${colour}`,
          }}
        />
      </div>

      <p className="mt-4 text-[11px] leading-snug text-muted">
        {refused ? (
          <>
            Below the floor. The system answered{" "}
            <span style={{ color: "var(--color-refused)" }}>without calling the model</span> —
            no context means no grounded answer to give.
          </>
        ) : (
          <>
            Above the floor. Retrieved context was passed to the model with{" "}
            <span style={{ color: "var(--color-verified)" }}>citation enforcement</span> on.
          </>
        )}
      </p>
    </div>
  );
}
