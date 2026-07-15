# Third-party notices

Oplab is an original AGPL-3.0-or-later implementation. No source code from Multica is
included.

The architecture and adapter boundary were informed by these upstream projects:

- DeerFlow 2.0, ByteDance, MIT License, https://github.com/bytedance/deer-flow
- LangGraph, LangChain, MIT License, https://github.com/langchain-ai/langgraph
- GPT Researcher, Tavily, Apache-2.0, https://github.com/assafelovic/gpt-researcher
- PaperQA2, FutureHouse, Apache-2.0, https://github.com/Future-House/paper-qa
- OpenScience, Synthetic Sciences, Apache-2.0,
  https://github.com/synthetic-sciences/openscience (reviewed commit
  `e9844a49f1f4d93cbf5f88b8f4880c003adc6e61`)

OpenScience informed the model-directed tool loop, persisted plan/trajectory,
reviewer gate, budget guard, and repeated-action protection. Oplab independently
implements these patterns in Python against its own typed domain commands; no
OpenScience source file is copied into this repository.

Runtime dependencies retain their own licenses. This file is informational and does not
replace a generated software bill of materials.
