// The journey sidebar is *generated* by scripts/journey.ts which writes
// to sidebars.generated.ts. We re-export it here so journey runs don't
// have to touch this file. A default empty version is committed so
// this static import always resolves on a fresh checkout.

import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';
import generated from './sidebars.generated';

const sidebars: SidebarsConfig = generated;

export default sidebars;
