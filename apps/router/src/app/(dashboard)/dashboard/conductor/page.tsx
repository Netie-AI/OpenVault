import ConductorPageClient from "./ConductorPageClient";

export const metadata = {
  title: "Conductor — FreeRoute",
  description: "FreeRoute Conductor CLI-agent fleet: runners, task queue and councils, live.",
};

export default function ConductorPage() {
  return <ConductorPageClient />;
}
