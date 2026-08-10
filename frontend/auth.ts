import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Credentials({
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        const email = credentials?.email;
        const password = credentials?.password;
        if (typeof email !== "string" || typeof password !== "string") {
          return null;
        }

        // Server-side only: calls FastAPI to independently verify the
        // credentials. Never call verify-credentials from browser JS.
        const res = await fetch(
          `${process.env.NEXT_PUBLIC_API_URL}/api/v1/auth/verify-credentials`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password }),
          }
        );

        if (!res.ok) {
          return null;
        }

        const body = await res.json();
        if (!body.success) {
          return null;
        }

        const { access_token, user } = body.data as {
          access_token: string;
          user: { id: string; email: string; role: string };
        };

        return {
          id: user.id,
          email: user.email,
          role: user.role,
          access_token,
        };
      },
    }),
  ],
  session: { strategy: "jwt" },
  pages: { signIn: "/login" },
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.access_token = user.access_token;
        token.role = user.role;
        token.id = user.id;
      }
      return token;
    },
    async session({ session, token }) {
      session.access_token = token.access_token ?? "";
      session.role = token.role ?? "";
      session.id = token.id ?? "";
      return session;
    },
  },
});
