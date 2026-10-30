import { BillingLayout } from "./_components/BillingLayout";

export default async function Layout({ children }: { children: React.ReactNode }) {
  return <BillingLayout>{children}</BillingLayout>;
}