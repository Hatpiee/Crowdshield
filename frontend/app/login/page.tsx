"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";
import { Eye, EyeOff, Lock, Mail } from "lucide-react";

import AmbientBackground from "@/components/AmbientBackground";
import Logo from "@/components/Logo";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    const result = await signIn("credentials", {
      email,
      password,
      redirect: false,
    });

    setSubmitting(false);

    if (!result || result.error) {
      setError("Invalid email or password");
      return;
    }

    router.push("/dashboard");
    router.refresh();
  }

  return (
    <div className="relative flex flex-1 items-center justify-center overflow-hidden bg-cs-bg px-6 py-16">
      <AmbientBackground variant="subtle" />

      <div className="relative z-10 w-full max-w-sm">
        <div className="mb-8 flex justify-center">
          <Logo />
        </div>

        <form
          onSubmit={handleSubmit}
          className="border border-cs-border bg-cs-panel p-8"
        >
          <h1 className="mb-6 text-center text-lg font-semibold text-cs-text">
            Operator Sign In
          </h1>

          {error && (
            <p className="mb-4 border border-cs-amber/40 bg-cs-amber/10 px-3 py-2 font-mono text-xs tracking-[0.05em] text-cs-amber">
              {error}
            </p>
          )}

          <div className="space-y-1.5">
            <label
              htmlFor="email"
              className="block font-mono text-xs tracking-[0.15em] text-cs-muted uppercase"
            >
              Email
            </label>
            <div className="relative">
              <Mail
                className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-cs-muted"
                strokeWidth={1.5}
              />
              <input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="w-full border border-cs-border bg-cs-bg py-2.5 pr-3 pl-10 text-cs-text outline-none transition-colors focus:border-cs-teal"
              />
            </div>
          </div>

          <div className="mt-4 space-y-1.5">
            <label
              htmlFor="password"
              className="block font-mono text-xs tracking-[0.15em] text-cs-muted uppercase"
            >
              Password
            </label>
            <div className="relative">
              <Lock
                className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-cs-muted"
                strokeWidth={1.5}
              />
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="w-full border border-cs-border bg-cs-bg py-2.5 pr-10 pl-10 text-cs-text outline-none transition-colors focus:border-cs-teal"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Hide password" : "Show password"}
                className="absolute top-1/2 right-3 -translate-y-1/2 text-cs-muted transition-colors hover:text-cs-teal"
              >
                {showPassword ? (
                  <EyeOff className="h-4 w-4" strokeWidth={1.5} />
                ) : (
                  <Eye className="h-4 w-4" strokeWidth={1.5} />
                )}
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="mt-8 flex w-full items-center justify-center bg-cs-amber py-3 font-mono text-xs tracking-[0.15em] text-cs-bg uppercase transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Signing In…" : "Sign In"}
          </button>
        </form>
      </div>
    </div>
  );
}
