import { redirect } from "next/navigation";

/** Old Route URL. FreeRoute at /freeroute is the one surface. */
export default function ProxyAliasPage(): never {
  redirect("/freeroute");
}
