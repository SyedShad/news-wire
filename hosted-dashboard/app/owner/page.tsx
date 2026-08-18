import Link from "next/link";

const errorMessages: Record<string, string> = {
  invalid_credentials: "That password was not accepted.",
  try_again_later: "Please wait a moment before trying again.",
  temporarily_locked: "Owner access is temporarily locked after repeated attempts.",
  owner_access_unavailable: "Owner access is not configured or is temporarily unavailable.",
};

export default async function OwnerAccess({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  return (
    <main className="auth-shell">
      <section className="auth-card owner-card" aria-labelledby="owner-heading">
        <Link className="back-link" href="/">← Back</Link>
        <p className="eyebrow">Owner access</p>
        <h1 id="owner-heading">Enter master password</h1>
        <p className="lede">
          This account has complete administrative access. The password is
          verified securely and is never stored in plaintext.
        </p>
        {error ? (
          <p className="alert" role="alert">
            {errorMessages[error] || "Owner sign-in could not be completed."}
          </p>
        ) : null}
        <form className="owner-form" method="post" action="/api/auth/master">
          <label htmlFor="password">Master password</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            minLength={20}
            maxLength={4096}
            autoFocus
          />
          <button className="button button-primary" type="submit">
            Unlock dashboard
          </button>
        </form>
      </section>
    </main>
  );
}
