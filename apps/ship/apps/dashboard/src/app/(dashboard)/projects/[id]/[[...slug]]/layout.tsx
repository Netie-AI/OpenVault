import { ProjectSettingsProvider } from "@/context/ProjectSettingsContext";
import CloudConnectionGate from "@/components/cloud/CloudConnectionGate";

const ProjectSettingsWrapper = async ({
  params,
  children,
}: {
  params: Promise<{ id: string; slug?: string[] }>;
  children: React.ReactNode;
}) => {

  const { id, slug } = await params;

  // CloudConnectionGate sits INSIDE the ProjectSettingsProvider so
  // it can read projectData (to know deployTarget) and outside the
  // route's individual page so the gate covers every tab — overview,
  // deployments, settings, env vars, all behave the same way when
  // the user is cloud-disconnected.
  return (
    <ProjectSettingsProvider slug={slug} id={id}>
      <CloudConnectionGate>{children}</CloudConnectionGate>
    </ProjectSettingsProvider>
  );
};

export default ProjectSettingsWrapper;
