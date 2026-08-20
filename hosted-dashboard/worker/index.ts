/** Cloudflare Worker entry point for the hosted News Wire dashboard. */
import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";
import type { AuthRuntimeEnv } from "../lib/auth/types.ts";
import { dispatchPendingNotifications } from "../lib/push/dispatch.ts";

interface Env extends AuthRuntimeEnv {
  ASSETS: Fetcher;
  DB: D1Database;
  CONTENT: R2Bucket;
  IMAGES: {
    input(stream: ReadableStream): {
      transform(options: Record<string, unknown>): {
        output(options: { format: string; quality: number }): Promise<{ response(): Response }>;
      };
    };
  };
}

interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
  passThroughOnException(): void;
}

// Image security config. SVG sources with .svg extension auto-skip the
// optimization endpoint on the client side (served directly, no proxy).
// To route SVGs through the optimizer (with security headers), set
// dangerouslyAllowSVG: true in next.config.js and uncomment below:
// const imageConfig: ImageConfig = { dangerouslyAllowSVG: true };

const worker = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    let response: Response;

    if (url.pathname === "/_vinext/image") {
      const allowedWidths = [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES];
      response = await handleImageOptimization(request, {
        fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
        transformImage: async (body, { width, format, quality }) => {
          const result = await env.IMAGES.input(body).transform(width > 0 ? { width } : {}).output({ format, quality });
          return result.response();
        },
      }, allowedWidths);
    } else {
      response = await handler.fetch(request, env, ctx);
      ctx.waitUntil(dispatchPendingNotifications(env).catch(() => undefined));
    }

    const secured = new Response(response.body, response);
    secured.headers.set("referrer-policy", "no-referrer");
    secured.headers.set("x-content-type-options", "nosniff");
    secured.headers.set("x-frame-options", "DENY");
    secured.headers.set(
      "permissions-policy",
      "camera=(), microphone=(), geolocation=()",
    );
    secured.headers.set(
      "content-security-policy",
      "default-src 'self'; base-uri 'none'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; object-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; worker-src 'self'",
    );
    if (url.pathname === "/sw.js") {
      secured.headers.set("cache-control", "no-cache, no-store, must-revalidate");
      secured.headers.set("content-type", "application/javascript; charset=utf-8");
      secured.headers.set("service-worker-allowed", "/");
    } else if (url.pathname === "/manifest.webmanifest") {
      secured.headers.set("cache-control", "public, max-age=300, must-revalidate");
      secured.headers.set("content-type", "application/manifest+json; charset=utf-8");
    }
    return secured;
  },
};

export default worker;
