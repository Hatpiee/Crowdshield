// Procedural stand-in for the reference design's aerial-cityscape hero
// image (deliberately NOT sourced/reproduced — see DECISIONS.md-equivalent
// note in the landing page follow-up report). A dark radial-gradient base,
// a phyllotaxis-scattered field of glowing dots, and an SVG reticle —
// CSS/SVG only, no external image asset. Shared between the landing page
// hero (`variant="hero"`) and the login page (`variant="subtle"`, dimmer
// and reticle-free so it doesn't compete with the form) so the effect is
// never duplicated between the two.

type Dot = {
  left: string;
  top: string;
  size: number;
  amber: boolean;
  delay: string;
};

// Deterministic (no Math.random()) so server- and client-rendered markup
// match exactly — a runtime-random scatter would cause a hydration
// mismatch the first time this "use client" component's SSR pass differs
// from its client render. The golden-angle spiral gives an organic,
// non-gridded scatter from a handful of fixed inputs per dot index.
function generateDots(count: number): Dot[] {
  const dots: Dot[] = [];
  for (let i = 0; i < count; i++) {
    const angle = i * 137.508;
    const radius = Math.sqrt(i / count);
    const radians = (angle * Math.PI) / 180;
    const left = 50 + Math.cos(radians) * radius * 48;
    const top = 50 + Math.sin(radians) * radius * 48;
    const amber = i % 7 === 0;
    dots.push({
      // Fixed precision (not raw float-to-string) — a real hydration
      // mismatch was observed in practice where the server- and
      // client-rendered percentage strings differed at the 5th+ decimal
      // digit despite computing from the same deterministic inputs.
      // toFixed() guarantees an identical string both times.
      left: `${left.toFixed(4)}%`,
      top: `${top.toFixed(4)}%`,
      size: amber ? 5 : 3 + (i % 3),
      amber,
      delay: `${(i % 6) * 0.5}s`,
    });
  }
  return dots;
}

const HERO_DOTS = generateDots(48);
const SUBTLE_DOTS = generateDots(24);

function Reticle({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 120 120"
      className={className}
      fill="none"
      aria-hidden="true"
    >
      <circle
        cx="60"
        cy="60"
        r="40"
        stroke="var(--cs-amber)"
        strokeWidth="1"
        strokeOpacity="0.55"
      />
      <circle
        cx="60"
        cy="60"
        r="4"
        stroke="var(--cs-amber)"
        strokeWidth="1"
        strokeOpacity="0.8"
      />
      <line
        x1="60"
        y1="0"
        x2="60"
        y2="35"
        stroke="var(--cs-amber)"
        strokeWidth="1"
        strokeOpacity="0.55"
      />
      <line
        x1="60"
        y1="85"
        x2="60"
        y2="120"
        stroke="var(--cs-amber)"
        strokeWidth="1"
        strokeOpacity="0.55"
      />
      <line
        x1="0"
        y1="60"
        x2="35"
        y2="60"
        stroke="var(--cs-amber)"
        strokeWidth="1"
        strokeOpacity="0.55"
      />
      <line
        x1="85"
        y1="60"
        x2="120"
        y2="60"
        stroke="var(--cs-amber)"
        strokeWidth="1"
        strokeOpacity="0.55"
      />
    </svg>
  );
}

export default function AmbientBackground({
  variant = "hero",
}: {
  variant?: "hero" | "subtle";
}) {
  const dots = variant === "hero" ? HERO_DOTS : SUBTLE_DOTS;

  return (
    <div
      className={`pointer-events-none absolute inset-0 overflow-hidden ${
        variant === "hero" ? "opacity-100" : "opacity-35"
      }`}
      aria-hidden="true"
    >
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 80% 60% at 50% 15%, #131b24 0%, var(--cs-bg) 70%)",
        }}
      />
      {dots.map((dot, i) => (
        <span
          key={i}
          className="absolute rounded-full"
          style={{
            left: dot.left,
            top: dot.top,
            width: dot.size,
            height: dot.size,
            backgroundColor: dot.amber
              ? "var(--cs-amber)"
              : "var(--cs-teal)",
            boxShadow: `0 0 ${dot.size * 2}px ${
              dot.amber ? "var(--cs-amber)" : "var(--cs-teal)"
            }`,
            animation: "cs-dot-pulse 4s ease-in-out infinite",
            animationDelay: dot.delay,
          }}
        />
      ))}
      {variant === "hero" && (
        <Reticle className="absolute top-[32%] right-[12%] h-40 w-40 md:h-56 md:w-56" />
      )}
    </div>
  );
}
