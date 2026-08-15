import { auth } from "@/auth";
import LogoutButton from "@/components/LogoutButton";

export default async function Home() {
  const session = await auth();

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8">
      <div>CrowdShield — System Online</div>
      {session && (
        <div className="flex items-center gap-4 text-sm">
          <span>
            {session.user?.email} ({session.role})
          </span>
          <LogoutButton />
        </div>
      )}
    </div>
  );
}
