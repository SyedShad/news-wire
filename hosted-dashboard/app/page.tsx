import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Sign in · Open Source AI News Wire",
  description:
    "Owner access for the hosted Open Source AI News Wire dashboard.",
};

export default function Home() {
  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="sign-in-heading">
        <div className="brand-mark" aria-hidden="true">S</div>
        <p className="eyebrow">Open Source AI News Wire</p>
        <h1 id="sign-in-heading">Dashboard access</h1>
        <p className="lede">
          Sign in with the owner password to reach the laptop-backed dashboard.
        </p>

        <div className="entry-options" aria-label="Sign-in options">
          <a className="button button-primary" href="/owner">
            Owner access
          </a>
        </div>

        <p className="domain-note">
          Sessions last eight hours. Repeated failed attempts are throttled and
          temporarily lock owner access.
        </p>
      </section>
      <p className="privacy-note">
        Dashboard data is available only after both Sites access and application
        owner authentication.
      </p>
    </main>
  );
}
