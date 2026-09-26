import NotificationsPage from '../notifications/page';
import AppearancePage from '../appearance/page';
import ShortcutsPage from '../shortcuts/page';
import SecurityPage from '../security/page';
import { m } from '@/paraglide/messages';
import GeneralPage from '../general/page';
import { useParams } from 'react-router';
import LabelsPage from '../labels/page';

// /settings/connections + /settings/categories were removed in Phase-1
// of the self-hosted migration. Mailbox identity is now the signed-in
// user itself; there is no separate "connection" concept to manage.
const settingsPages: Record<string, React.ComponentType> = {
  general: GeneralPage,
  security: SecurityPage,
  appearance: AppearancePage,
  shortcuts: ShortcutsPage,
  notifications: NotificationsPage,
  labels: LabelsPage,
};

export default function SettingsPage() {
  const params = useParams();
  const section = params.settings?.[0] || 'general';

  const SettingsComponent = settingsPages[section];

  if (!SettingsComponent) {
    return <div>{m['pages.error.settingsNotFound']()}</div>;
  }

  return <SettingsComponent />;
}
