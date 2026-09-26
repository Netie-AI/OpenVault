import type { Config } from '@react-router/dev/config';

export default {
  ssr: false,
  buildDirectory: 'build',
  appDirectory: 'app',
  routeDiscovery: {
    mode: 'initial',
  },
  future: {
    v8_viteEnvironmentApi: true,
  },
} satisfies Config;
