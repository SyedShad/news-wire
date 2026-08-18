import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

function requestOrigin(requestHeaders: Headers): URL {
  const forwardedHost = requestHeaders.get("x-forwarded-host")?.split(",", 1)[0]?.trim();
  const host = forwardedHost || requestHeaders.get("host")?.trim();
  const forwardedProtocol = requestHeaders.get("x-forwarded-proto")?.split(",", 1)[0]?.trim();
  const protocol = forwardedProtocol === "http" || forwardedProtocol === "https"
    ? forwardedProtocol
    : host?.startsWith("localhost")
      ? "http"
      : "https";
  if (host && /^[a-z0-9.-]+(?::\d+)?$/iu.test(host)) {
    return new URL(`${protocol}://${host}`);
  }
  return new URL("https://sentient-ai-news-wire.shadmanulhaque.chatgpt.site");
}

export async function generateMetadata(): Promise<Metadata> {
  const origin = requestOrigin(await headers());
  const image = new URL("/og.png", origin).toString();
  const title = "Open Source AI News Wire";
  const description = "Protected hosted dashboard for monitoring, review, and content operations.";
  return {
    title,
    description,
    icons: {
      icon: "/favicon.svg",
      shortcut: "/favicon.svg",
    },
    openGraph: {
      title,
      description,
      type: "website",
      url: origin,
      images: [{ url: image, width: 1664, height: 928, alt: `${title} hosted dashboard` }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [image],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
