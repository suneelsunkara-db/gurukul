// Make every Gurukul widget available globally in MDX, so generated
// topic files don't need import statements (which keeps the FM's job
// simple and the template strict).

import MDXComponents from '@theme-original/MDXComponents';
import Summary from '@site/src/components/Summary';
import KeyAspect from '@site/src/components/KeyAspect';
import Gist from '@site/src/components/Gist';
import OpenProblems from '@site/src/components/OpenProblems';
import References from '@site/src/components/References';
import Experiment from '@site/src/components/Experiment';
import Critique from '@site/src/components/Critique';
import ConfidenceTracker from '@site/src/components/ConfidenceTracker';
import ModelComparison from '@site/src/components/ModelComparison';
import ResearchSeed from '@site/src/components/ResearchSeed';
import TopicHeader from '@site/src/components/TopicHeader';

export default {
  ...MDXComponents,
  Summary,
  KeyAspect,
  Gist,
  OpenProblems,
  References,
  Experiment,
  Critique,
  ConfidenceTracker,
  ModelComparison,
  ResearchSeed,
  TopicHeader,
};
