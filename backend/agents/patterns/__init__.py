"""
AI Patterns module.

ReACTAgent, ChainOfThoughtAgent, PatternSelector, BasePatternAgent, and
PatternOrchestrator were removed: confirmed unreachable in real usage
(use_planning defaults to True at every layer including the frontend,
which never overrides it), their output never influenced any downstream
field even when manually triggered, and ChainOfThoughtAgent's policy
check had drifted into a third, independent hardcoded keyword matcher
duplicating what PolicyEvaluationService already does correctly
elsewhere. See docs/CAPSTONE_SUMMARY.md for the full removal rationale
(same precedent as the Supervisor orchestration removal).

AdvancedRAGAgent (advanced_rag_agent.py) was not part of this finding and
remains - import it directly from that submodule.
"""
