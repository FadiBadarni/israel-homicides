import { fetchReviewPairs } from "@/lib/api";
import type { ReviewPair } from "@/lib/api";

export default async function ReviewPage() {
  const { run_id, pairs } = await fetchReviewPairs();

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Review queue</h1>
        {run_id && (
          <span className="text-xs text-muted-foreground font-mono">{run_id}</span>
        )}
      </div>

      {pairs.length === 0 ? (
        <div className="rounded-xl border bg-card p-8 text-center text-muted-foreground">
          No pairs awaiting review in the latest run.
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {pairs.length} pair{pairs.length !== 1 ? "s" : ""} the pipeline could not
            auto-decide. Each pair passed Jaro-Winkler pre-filter but scored between
            0.70–0.82 cosine similarity. Decide whether they describe the same
            real-world incident.
          </p>
          {pairs.map((pair, i) => (
            <PairCard key={i} pair={pair} index={i} />
          ))}
        </div>
      )}
    </div>
  );
}

function PairCard({ pair, index }: { pair: ReviewPair; index: number }) {
  const jaroColor =
    pair.jaro_score >= 0.88
      ? "text-green-700"
      : pair.jaro_score >= 0.70
      ? "text-amber-700"
      : "text-red-700";

  const cosineColor =
    pair.cosine_score >= 0.82
      ? "text-green-700"
      : pair.cosine_score >= 0.70
      ? "text-amber-700"
      : "text-red-700";

  return (
    <div className="rounded-xl border bg-card p-4 space-y-3">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span className="font-medium">Pair #{index + 1}</span>
        <div className="flex gap-4">
          <span>
            Jaro-Winkler:{" "}
            <span className={`font-mono font-semibold ${jaroColor}`}>
              {pair.jaro_score.toFixed(3)}
            </span>
          </span>
          <span>
            Cosine:{" "}
            <span className={`font-mono font-semibold ${cosineColor}`}>
              {pair.cosine_score.toFixed(3)}
            </span>
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <SideCard label="A" side={pair.a} />
        <SideCard label="B" side={pair.b} />
      </div>
    </div>
  );
}

function SideCard({
  label,
  side,
}: {
  label: string;
  side: ReviewPair["a"];
}) {
  return (
    <div className="rounded-lg border bg-muted/30 p-3 space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-xs font-bold text-muted-foreground uppercase tracking-wider">
          {label}
        </span>
        {side.victim_name ? (
          <bdi
            dir="auto"
            className="text-sm font-semibold"
          >
            {side.victim_name}
          </bdi>
        ) : (
          <span className="text-sm text-muted-foreground italic">unnamed</span>
        )}
      </div>
      {side.city && (
        <p className="text-xs text-muted-foreground">
          <bdi dir="auto">{side.city}</bdi>
        </p>
      )}
      {side.url && (
        <a
          href={side.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-blue-600 hover:underline break-all block"
        >
          {side.url}
        </a>
      )}
    </div>
  );
}
