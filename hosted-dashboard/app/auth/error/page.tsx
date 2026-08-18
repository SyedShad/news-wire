import Link from "next/link";

const messages: Record<string, string> = {
  domain_not_allowed:
    "This Google account is not in an approved Sentient Workspace domain.",
  workspace_not_allowed:
    "Google did not identify this account as a member of the approved Workspace organization.",
  email_not_verified: "Google did not confirm a verified email for this account.",
  invalid_state: "The sign-in request expired or could not be verified. Please try again.",
  provider_denied: "Google sign-in was cancelled or denied.",
  google_not_configured: "Google sign-in has not been configured for this preview yet.",
  authentication_storage_unavailable:
    "Authentication storage is temporarily unavailable.",
};

export default async function AuthenticationError({
  searchParams,
}: {
  searchParams: Promise<{ code?: string }>;
}) {
  const { code } = await searchParams;
  return (
    <main className="auth-shell">
      <section className="auth-card compact-card">
        <p className="eyebrow">Sign-in blocked</p>
        <h1>Access was not granted</h1>
        <p className="lede">
          {messages[code || ""] || "Google sign-in could not be completed safely."}
        </p>
        <Link className="button button-primary" href="/">
          Return to sign in
        </Link>
      </section>
    </main>
  );
}
