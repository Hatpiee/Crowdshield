import Link from "next/link";
import { ShieldCheck } from "lucide-react";

export default function Logo({
  className = "",
  href = "/",
}: {
  className?: string;
  href?: string;
}) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-2 font-sans text-lg font-bold tracking-tight ${className}`}
    >
      <ShieldCheck className="h-5 w-5 text-cs-teal" strokeWidth={1.75} />
      <span className="text-cs-text">
        CROWD<span className="text-cs-teal">SHIELD</span>
      </span>
    </Link>
  );
}
