import { NextResponse, type NextRequest } from "next/server";

// The dashboard shows health information, so a deployed copy asks for a family
// password (HTTP Basic auth, any username). Unset locally: no prompt.
export function proxy(request: NextRequest) {
  const password = process.env.KIN_FAMILY_PASSWORD;
  if (!password) return NextResponse.next();

  const header = request.headers.get("authorization") ?? "";
  const [scheme, encoded] = header.split(" ");
  if (scheme === "Basic" && encoded) {
    const supplied = atob(encoded).split(":").slice(1).join(":");
    if (supplied === password) return NextResponse.next();
  }
  return new NextResponse("Kin family access needs a password.", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Kin family", charset="UTF-8"' },
  });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
