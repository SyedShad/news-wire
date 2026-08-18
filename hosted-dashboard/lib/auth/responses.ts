import { NextResponse } from "next/server";

export function noStore(response: NextResponse): NextResponse {
  response.headers.set("cache-control", "no-store, max-age=0");
  response.headers.set("pragma", "no-cache");
  return response;
}

export function errorRedirect(request: Request, code: string): NextResponse {
  const url = new URL("/auth/error", request.url);
  url.searchParams.set("code", code);
  return noStore(NextResponse.redirect(url, 303));
}
