import type { ReactNode } from "react";
import Link from "next/link";
import {
  ArrowUpRight,
  Bell,
  BrainCircuit,
  Cpu,
  Eye,
  Gauge,
  Lock,
  Scan,
  Share2,
  ShieldCheck,
  Target,
  Users,
  Video,
} from "lucide-react";

import AmbientBackground from "@/components/AmbientBackground";
import Logo from "@/components/Logo";

function EyebrowLabel({ children }: { children: ReactNode }) {
  return (
    <p className="font-mono text-xs tracking-[0.2em] text-cs-amber uppercase">
      {children}
    </p>
  );
}

function SectionHeading({
  eyebrow,
  title,
  body,
}: {
  eyebrow: string;
  title: ReactNode;
  body: string;
}) {
  return (
    <div className="mb-12 border-b border-cs-border pb-8">
      <div className="mb-4 flex items-center gap-4">
        <EyebrowLabel>{eyebrow}</EyebrowLabel>
        <div className="h-px flex-1 bg-cs-border" />
      </div>
      <h2 className="max-w-3xl text-3xl font-bold tracking-tight text-cs-text sm:text-4xl">
        {title}
      </h2>
      <p className="mt-4 max-w-xl text-cs-muted">{body}</p>
    </div>
  );
}

function Nav() {
  return (
    <header className="relative z-10 flex items-center justify-between px-6 py-6 sm:px-10">
      <Logo />
      <Link
        href="/login"
        className="flex items-center gap-1.5 font-mono text-xs tracking-[0.15em] text-cs-text uppercase transition-colors hover:text-cs-teal"
      >
        Explore Project
        <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={1.75} />
      </Link>
    </header>
  );
}

function Hero() {
  return (
    <section className="relative overflow-hidden">
      <AmbientBackground variant="hero" />
      <div className="relative z-10 mx-auto flex max-w-5xl flex-col items-start px-6 pt-16 pb-40 sm:px-10 sm:pt-24 sm:pb-56">
        <h1 className="text-6xl leading-none font-bold tracking-tight sm:text-8xl">
          <span className="text-cs-text">Crowd</span>
          <span
            className="text-transparent"
            style={{ WebkitTextStroke: "1.5px var(--cs-text)" }}
          >
            Shield
          </span>
        </h1>
        <p className="mt-6 max-w-xl text-xl text-cs-text sm:text-2xl">
          An AI-powered early warning system for preventing crowd stampedes
        </p>
        <p className="mt-4 max-w-lg text-cs-muted">
          Turning reactive crowd monitoring into predictive public safety —{" "}
          <span className="font-semibold text-cs-text">
            before a crush ever begins.
          </span>
        </p>

        <div className="mt-10 flex flex-col gap-4 sm:flex-row">
          <a
            href="#how-it-works"
            className="flex items-center justify-center gap-2 bg-cs-amber px-6 py-3.5 font-mono text-xs tracking-[0.15em] text-cs-bg uppercase transition-opacity hover:opacity-90"
          >
            See How It Works
            <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2} />
          </a>
          <a
            href="#solution"
            className="flex items-center justify-center gap-2 border border-cs-border px-6 py-3.5 font-mono text-xs tracking-[0.15em] text-cs-text uppercase transition-colors hover:border-cs-teal hover:text-cs-teal"
          >
            View the Architecture
          </a>
        </div>
      </div>

      <div className="absolute bottom-8 left-6 z-10 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase sm:left-10">
        Live Density / Cam 04
        <div className="mt-2 flex items-center gap-2 normal-case">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cs-teal opacity-75" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-cs-teal" />
          </span>
          System Status: Nominal
        </div>
      </div>
    </section>
  );
}

function StatRow({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex flex-col justify-between gap-2 border-t border-cs-border py-6 sm:flex-row sm:items-center">
      <div className="flex items-baseline text-5xl font-bold text-cs-text sm:text-6xl">
        {value}
        <span className="ml-1 text-cs-amber">+</span>
      </div>
      <p className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
        {label}
      </p>
    </div>
  );
}

function ProblemSection() {
  return (
    <section className="mx-auto max-w-5xl px-6 py-24 sm:px-10">
      <EyebrowLabel>01 The Problem</EyebrowLabel>
      <h2 className="mt-4 max-w-2xl text-3xl font-bold tracking-tight sm:text-4xl">
        <span className="text-cs-text">By the time you see the crush, </span>
        <span className="text-cs-teal">it&apos;s already too late.</span>
      </h2>
      <p className="mt-4 max-w-xl text-cs-muted">
        Traditional crowd management is reactive — CCTV, manual supervision,
        and human judgment respond only after a crush has already begun. By
        then, it&apos;s often too late.
      </p>

      <div className="mt-10 border-b border-cs-border">
        <StatRow value="120" label="Stampede Deaths in India / 2024" />
        <StatRow value="110" label="Stampede Deaths in India / 2025" />
      </div>
    </section>
  );
}

const ARCHITECTURE_LAYERS = [
  {
    index: "01",
    icon: Scan,
    title: "Perception",
    body: "Detects and tracks people in dense crowds in real time, on ordinary CPU hardware — no GPU required.",
  },
  {
    index: "02",
    icon: Gauge,
    title: "Crowd Intelligence",
    body: "Computes density, flow, and Crowd Pressure — a disaster-validated physics metric that flags dangerous crushes before they happen.",
  },
  {
    index: "03",
    icon: Eye,
    title: "Vision Intelligence",
    body: "A vision-language model reads the scene for blocked exits, barricades, and hazards numbers alone can't see.",
  },
  {
    index: "04",
    icon: BrainCircuit,
    title: "Decision Intelligence",
    body: "Synthesizes every signal into a clear, evidence-cited recommendation for human operators.",
  },
];

function ArchitectureCard({
  index,
  icon: Icon,
  title,
  body,
}: (typeof ARCHITECTURE_LAYERS)[number]) {
  return (
    <div className="flex flex-col justify-between border border-cs-border bg-cs-panel p-6 sm:p-8">
      <div className="flex items-start justify-between">
        <span className="font-mono text-sm text-cs-muted">{index}</span>
        <Icon className="h-5 w-5 text-cs-teal" strokeWidth={1.5} />
      </div>
      <div className="mt-8">
        <h3 className="text-xl font-semibold text-cs-text">{title}</h3>
        <p className="mt-2 text-sm text-cs-muted">{body}</p>
      </div>
      <div className="mt-6 h-px w-16 bg-cs-teal" />
    </div>
  );
}

function SolutionSection() {
  return (
    <section id="solution" className="mx-auto max-w-5xl px-6 py-24 sm:px-10">
      <SectionHeading
        eyebrow="02 The Solution / Four-Layer Architecture"
        title={
          <>
            <span className="text-cs-text">From raw video to </span>
            <span className="text-cs-teal">clear action.</span>
          </>
        }
        body="Four intelligent layers turn a live crowd into a legible, evidence-backed safety picture."
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {ARCHITECTURE_LAYERS.map((layer) => (
          <ArchitectureCard key={layer.index} {...layer} />
        ))}
      </div>
    </section>
  );
}

const DIFFERENTIATORS = [
  {
    icon: Cpu,
    title: "CPU-only, low-cost deployment",
    body: "Runs on affordable hardware, no specialized infrastructure.",
  },
  {
    icon: Target,
    title: "Predictive, not reactive",
    body: "Flags rising risk minutes before it becomes dangerous.",
  },
  {
    icon: Users,
    title: "Human-in-the-loop, always",
    body: "The system recommends. A human operator always decides.",
  },
  {
    icon: Lock,
    title: "Privacy by design",
    body: "Tracks crowd movement, never individual identity.",
  },
];

function DifferentiatorsSection() {
  return (
    <section className="mx-auto max-w-5xl px-6 py-24 sm:px-10">
      <SectionHeading
        eyebrow="03 Why It's Different"
        title={
          <>
            <span className="text-cs-text">Built for the moments where </span>
            <span className="text-cs-teal">every second matters.</span>
          </>
        }
        body="CrowdShield is designed to work alongside the people who keep public spaces safe — giving them more time, more context, and a clearer decision."
      />
      <div className="grid grid-cols-1 gap-x-8 gap-y-10 sm:grid-cols-2">
        {DIFFERENTIATORS.map(({ icon: Icon, title, body }) => (
          <div key={title} className="flex gap-4">
            <div className="flex h-11 w-11 flex-none items-center justify-center border border-cs-border">
              <Icon className="h-5 w-5 text-cs-teal" strokeWidth={1.5} />
            </div>
            <div>
              <h3 className="font-semibold text-cs-text">{title}</h3>
              <p className="mt-1 text-sm text-cs-muted">{body}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

const WORKFLOW_STEPS = [
  { icon: Video, label: "Live Input", title: "Video Feed" },
  { icon: Share2, label: "Signals Merged", title: "Real-Time Analysis" },
  { icon: Bell, label: "Risk Detected", title: "Early Alert" },
  { icon: ShieldCheck, label: "Operator Clears", title: "Human Decision" },
];

function HowItWorksSection() {
  return (
    <section
      id="how-it-works"
      className="mx-auto max-w-5xl px-6 py-24 sm:px-10"
    >
      <SectionHeading
        eyebrow="04 How It Works"
        title={
          <>
            <span className="text-cs-text">One signal. </span>
            <span className="text-cs-teal">Four decisions.</span>
          </>
        }
        body="Designed to make the invisible visible — before risk becomes an incident."
      />
      <div className="border border-cs-border p-8 sm:p-12">
        <div className="flex flex-col gap-10 sm:flex-row sm:items-start sm:justify-between">
          {WORKFLOW_STEPS.map(({ icon: Icon, label, title }, i) => (
            <div key={title} className="flex items-start gap-4 sm:flex-1">
              <div className="flex flex-none flex-col items-center gap-3 sm:items-start">
                <div className="flex h-12 w-12 items-center justify-center border border-cs-border">
                  {i === 0 ? (
                    <Icon className="h-5 w-5 text-cs-teal" strokeWidth={1.5} />
                  ) : (
                    <ArrowUpRight
                      className="h-5 w-5 text-cs-teal"
                      strokeWidth={1.5}
                    />
                  )}
                </div>
              </div>
              <div>
                <p className="font-mono text-xs tracking-[0.15em] text-cs-amber uppercase">
                  {label}
                </p>
                <p className="mt-1 text-lg font-semibold text-cs-text">
                  {title}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function ClosingCta() {
  return (
    <section className="relative overflow-hidden px-6 py-24 sm:px-10">
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 60% 60% at 50% 100%, rgba(255,107,53,0.12) 0%, transparent 70%)",
        }}
        aria-hidden="true"
      />
      <div className="relative mx-auto max-w-3xl border border-cs-border px-6 py-20 text-center sm:px-16">
        <EyebrowLabel>05 The Next Step</EyebrowLabel>
        <h2 className="mt-5 text-3xl font-bold tracking-tight sm:text-5xl">
          <span className="text-cs-text">
            Making India&apos;s public gatherings safer,{" "}
          </span>
          <span className="text-cs-teal">one crowd at a time.</span>
        </h2>
        <Link
          href="/login"
          className="mt-10 inline-flex items-center gap-2 bg-cs-amber px-8 py-4 font-mono text-xs tracking-[0.15em] text-cs-bg uppercase transition-opacity hover:opacity-90"
        >
          Explore the Project
          <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2} />
        </Link>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="border-t border-cs-border px-6 py-8 sm:px-10">
      <div className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
        <Logo />
        <p className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
          CrowdShield — Predictive Crowd Safety System
        </p>
      </div>
      <p className="mt-6 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
        Public Safety / 2025
      </p>
    </footer>
  );
}

export default function LandingPage() {
  return (
    <div className="flex-1 bg-cs-bg text-cs-text">
      <Nav />
      <Hero />
      <ProblemSection />
      <SolutionSection />
      <DifferentiatorsSection />
      <HowItWorksSection />
      <ClosingCta />
      <Footer />
    </div>
  );
}
